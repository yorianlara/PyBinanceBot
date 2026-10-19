
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert

from app.risk.config import RiskConfig
from app.storage.database import SessionLocal
from app.storage.models import (
    RiskSnapshot,
    TradeFill,
    TradeOrder,
    TradePosition,
)
from app.storage.risk_models import RiskAccount, RiskAllocation


class RiskRejected(Exception):
    """Operación rechazada por las reglas de riesgo."""


class PersistentRiskManager:
    LOCK_KEY = 73190421

    def __init__(self, config: RiskConfig | None = None):
        self.config = config or RiskConfig()

    @staticmethod
    def _lock(session) -> None:
        session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": PersistentRiskManager.LOCK_KEY},
        )

    @staticmethod
    def _get_account(session) -> RiskAccount:
        today = datetime.now(timezone.utc).date()

        session.execute(
            insert(RiskAccount)
            .values(id=1, pnl_day=today)
            .on_conflict_do_nothing(index_elements=["id"])
        )

        account = session.execute(
            select(RiskAccount)
            .where(RiskAccount.id == 1)
            .with_for_update()
        ).scalar_one()

        if account.pnl_day != today:
            account.pnl_day = today
            account.daily_pnl_eur = Decimal("0")

        return account

    def get_account_summary(self) -> dict:
        """Devuelve un resumen del estado del gestor de riesgo y capital."""
        with SessionLocal() as session:
            account = self._get_account(session)
            allocations = session.scalars(
                select(RiskAllocation).where(RiskAllocation.status == "active")
            ).all()

            total_allocated = sum((r.amount_eur for r in allocations), Decimal("0"))
            max_daily_loss = Decimal(str(self.config.max_daily_loss_eur))
            max_total_loss = Decimal(str(self.config.max_total_loss_eur))
            max_capital = Decimal(str(self.config.max_capital_eur))

            is_trading_allowed = (
                account.daily_pnl_eur > -max_daily_loss
                and account.total_pnl_eur > -max_total_loss
                and len(allocations) < self.config.max_open_positions
                and total_allocated < max_capital
            )

            return {
                "max_capital_eur": max_capital,
                "max_allocation_per_strategy_eur": Decimal(
                    str(self.config.max_allocation_per_strategy_eur)
                ),
                "max_open_positions": self.config.max_open_positions,
                "daily_pnl_eur": account.daily_pnl_eur,
                "total_pnl_eur": account.total_pnl_eur,
                "max_daily_loss_eur": max_daily_loss,
                "max_total_loss_eur": max_total_loss,
                "active_allocations": len(allocations),
                "total_allocated_eur": total_allocated,
                "trading_allowed": is_trading_allowed,
                "allocations": [
                    {
                        "strategy_key": a.strategy_key,
                        "amount_eur": a.amount_eur,
                        "allocation_type": a.allocation_type,
                        "order_id": a.order_id,
                        "position_id": a.position_id,
                    }
                    for a in allocations
                ],
            }

    def _check_limits(
        self,
        session,
        strategy_key: str,
        allocation_eur: Decimal,
    ) -> None:
        if not strategy_key.strip():
            raise RiskRejected(
                "La estrategia debe tener un identificador."
            )

        if not allocation_eur.is_finite() or allocation_eur <= 0:
            raise RiskRejected(
                "La asignación debe ser positiva y finita."
            )

        account = self._get_account(session)

        max_daily_loss = Decimal(
            str(self.config.max_daily_loss_eur)
        )
        max_total_loss = Decimal(
            str(self.config.max_total_loss_eur)
        )
        max_strategy = Decimal(
            str(self.config.max_allocation_per_strategy_eur)
        )
        max_capital = Decimal(
            str(self.config.max_capital_eur)
        )

        if account.daily_pnl_eur <= -max_daily_loss:
            raise RiskRejected(
                "Se alcanzó el límite de pérdida diaria."
            )

        if account.total_pnl_eur <= -max_total_loss:
            raise RiskRejected(
                "Se alcanzó el límite de pérdida acumulada."
            )

        allocations = session.scalars(
            select(RiskAllocation).where(
                RiskAllocation.status == "active"
            )
        ).all()

        if len(allocations) >= self.config.max_open_positions:
            raise RiskRejected(
                "Se alcanzó el límite de posiciones o reservas activas."
            )

        total_allocated = sum(
            (row.amount_eur for row in allocations),
            Decimal("0"),
        )

        strategy_allocated = sum(
            (
                row.amount_eur
                for row in allocations
                if row.strategy_key == strategy_key
            ),
            Decimal("0"),
        )

        if strategy_allocated + allocation_eur > max_strategy:
            raise RiskRejected(
                "La asignación supera el límite de la estrategia."
            )

        if total_allocated + allocation_eur > max_capital:
            raise RiskRejected(
                "La asignación supera el capital máximo."
            )

    def reserve_order(
        self,
        *,
        strategy_key: str,
        symbol: str,
        side: str,
        order_type: str,
        quantity: Decimal,
        allocation_eur: Decimal,
        bot_run_id: int | None = None,
        client_order_id: str | None = None,
        limit_price: Decimal | None = None,
    ) -> int:
        if not isinstance(side, str) or side.lower() != "buy":
            raise RiskRejected(
                "Esta versión solo permite entradas BUY en Binance Spot."
            )

        if not quantity.is_finite() or quantity <= 0:
            raise RiskRejected(
                "La cantidad debe ser positiva y finita."
            )

        if limit_price is not None and (
            not limit_price.is_finite() or limit_price <= 0
        ):
            raise RiskRejected(
                "El precio límite debe ser positivo y finito."
            )

        if (
            not isinstance(symbol, str)
            or not symbol.strip()
            or not isinstance(order_type, str)
            or not order_type.strip()
        ):
            raise RiskRejected(
                "El símbolo y el tipo de orden son obligatorios."
            )

        with SessionLocal.begin() as session:
            self._lock(session)

            if client_order_id:
                existing = session.scalar(
                    select(TradeOrder).where(
                        TradeOrder.client_order_id == client_order_id
                    )
                )
                if existing is not None:
                    raise RiskRejected(
                        "El identificador de cliente ya está asociado "
                        "a una orden."
                    )

            self._check_limits(
                session,
                strategy_key,
                allocation_eur,
            )

            order = TradeOrder(
                bot_run_id=bot_run_id,
                client_order_id=client_order_id,
                symbol=symbol.upper(),
                side="buy",
                order_type=order_type.upper(),
                status="pending_submission",
                quantity=quantity,
                limit_price=limit_price,
            )

            session.add(order)
            session.flush()

            session.add(
                RiskAllocation(
                    order_id=order.id,
                    strategy_key=strategy_key,
                    amount_eur=allocation_eur,
                    allocation_type="order",
                    status="active",
                )
            )

            session.flush()
            return order.id

    def mark_submitted(
        self,
        order_id: int,
        exchange_order_id: str,
    ) -> None:
        if not exchange_order_id.strip():
            raise ValueError(
                "exchange_order_id es obligatorio."
            )

        with SessionLocal.begin() as session:
            self._lock(session)

            order = session.get(
                TradeOrder,
                order_id,
                with_for_update=True,
            )

            if order is None:
                raise ValueError(
                    f"No existe la orden {order_id}."
                )

            if order.status != "pending_submission":
                raise RiskRejected(
                    "La orden no está pendiente de envío."
                )

            order.exchange_order_id = exchange_order_id
            order.status = "new"

    def reconcile_fills(
        self,
        order_id: int,
        exchange_order: dict,
        exchange_trades: list[dict],
    ) -> None:
        """Registra las ejecuciones reales confirmadas por Binance."""
        status_map = {
            "NEW": "new",
            "PARTIALLY_FILLED": "partially_filled",
            "FILLED": "filled",
        }

        exchange_status = exchange_order.get("status")
        if exchange_status not in status_map:
            raise RiskRejected(
                "Estado de Binance no admitido para esta reconciliación."
            )

        try:
            executed_quantity = Decimal(
                str(exchange_order["executedQty"])
            )
            exchange_order_id = str(exchange_order["orderId"])
            exchange_symbol = exchange_order["symbol"].upper()
        except (KeyError, TypeError, ValueError):
            raise RiskRejected(
                "La respuesta de Binance no contiene datos válidos."
            ) from None

        if (
            not executed_quantity.is_finite()
            or executed_quantity < 0
            or not exchange_order_id
            or not exchange_symbol
        ):
            raise RiskRejected(
                "Los datos de ejecución de Binance no son válidos."
            )

        with SessionLocal.begin() as session:
            self._lock(session)

            order = session.get(
                TradeOrder,
                order_id,
                with_for_update=True,
            )

            if order is None:
                raise ValueError(f"No existe la orden {order_id}.")

            if (
                str(order.exchange_order_id) != exchange_order_id
                or order.symbol != exchange_symbol
                or order.side != "buy"
            ):
                raise RiskRejected(
                    "La respuesta de Binance no corresponde a esta orden."
                )

            if order.status not in {
                "new",
                "partially_filled",
                "filled",
            }:
                raise RiskRejected(
                    "La orden no está en un estado reconciliable."
                )

            for trade in exchange_trades:
                try:
                    trade_order_id = str(trade["orderId"])
                    trade_symbol = trade["symbol"].upper()
                    trade_id = str(trade["id"])
                    quantity = Decimal(str(trade["qty"]))
                    price = Decimal(str(trade["price"]))
                    commission = Decimal(str(trade["commission"]))
                    executed_at = datetime.fromtimestamp(
                        int(trade["time"]) / 1000,
                        tz=timezone.utc,
                    )
                    commission_asset = trade.get("commissionAsset")
                except (KeyError, TypeError, ValueError, OverflowError):
                    raise RiskRejected(
                        "Binance devolvió una ejecución con datos inválidos."
                    ) from None

                if (
                    trade_order_id != exchange_order_id
                    or trade_symbol != order.symbol
                    or not trade_id
                    or not quantity.is_finite()
                    or quantity <= 0
                    or not price.is_finite()
                    or price <= 0
                    or not commission.is_finite()
                    or commission < 0
                ):
                    raise RiskRejected(
                        "Una ejecución no coincide con la orden o "
                        "contiene valores inválidos."
                    )

                statement = insert(TradeFill).values(
                    order_id=order.id,
                    exchange_trade_id=trade_id,
                    quantity=quantity,
                    price=price,
                    commission=commission,
                    commission_asset=commission_asset,
                    executed_at=executed_at,
                ).on_conflict_do_nothing(
                    constraint="uq_trade_fills_exchange_trade_id"
                )

                session.execute(statement)

            persisted_quantity = session.scalar(
                select(func.coalesce(func.sum(TradeFill.quantity), 0))
                .where(TradeFill.order_id == order.id)
            )
            persisted_quantity = Decimal(str(persisted_quantity))

            if persisted_quantity != executed_quantity:
                raise RiskRejected(
                    "Las ejecuciones registradas no coinciden con "
                    "la cantidad ejecutada informada por Binance."
                )

            if persisted_quantity > order.quantity:
                raise RiskRejected(
                    "La cantidad ejecutada supera la cantidad de la orden."
                )

            if exchange_status == "FILLED":
                if persisted_quantity != order.quantity:
                    raise RiskRejected(
                        "Binance marca la orden como ejecutada, pero "
                        "la cantidad no coincide con la orden."
                    )
            elif exchange_status == "PARTIALLY_FILLED":
                if not 0 < persisted_quantity < order.quantity:
                    raise RiskRejected(
                        "La cantidad no corresponde a una ejecución parcial."
                    )
            elif persisted_quantity != 0:
                raise RiskRejected(
                    "Binance marca la orden como nueva, pero existen "
                    "ejecuciones registradas."
                )

            order.status = status_map[exchange_status]
            order.updated_at = datetime.now(timezone.utc)
    
    def mark_filled(self, order_id: int) -> None:
        """Marca como ejecutada una orden confirmada por Binance."""
        with SessionLocal.begin() as session:
            self._lock(session)

            order = session.get(
                TradeOrder,
                order_id,
                with_for_update=True,
            )

            if order is None:
                raise ValueError(
                    f"No existe la orden {order_id}."
                )

            if order.status != "new":
                raise RiskRejected(
                    "Solo se pueden confirmar órdenes en estado new."
                )

            if not order.exchange_order_id:
                raise RiskRejected(
                    "La orden no tiene identificador de Binance."
                )

            allocation = session.execute(
                select(RiskAllocation)
                .where(
                    RiskAllocation.order_id == order_id,
                    RiskAllocation.status == "active",
                    RiskAllocation.allocation_type == "order",
                )
                .with_for_update()
            ).scalar_one_or_none()

            if allocation is None:
                raise RiskRejected(
                    "La orden no tiene reserva activa."
                )

            fills_quantity = session.scalar(
                select(func.coalesce(func.sum(TradeFill.quantity), 0))
                .where(TradeFill.order_id == order_id)
            )

            if Decimal(str(fills_quantity)) != order.quantity:
                raise RiskRejected(
                    "No se puede marcar la orden como ejecutada: "
                    "los fills registrados no coinciden con la cantidad "
                    "total de la orden."
                )

            order.status = "filled"

    def release_order(
        self,
        order_id: int,
        status: str = "canceled",
    ) -> None:
        if status not in {"canceled", "rejected", "expired"}:
            raise ValueError(
                "Estado de liberación no permitido."
            )

        with SessionLocal.begin() as session:
            self._lock(session)

            order = session.get(
                TradeOrder,
                order_id,
                with_for_update=True,
            )

            if order is None:
                raise ValueError(
                    f"No existe la orden {order_id}."
                )

            if order.status == "partially_filled":
                raise RiskRejected(
                    "No se puede liberar una orden parcialmente "
                    "ejecutada sin reconciliar primero sus ejecuciones."
                )

            fills_quantity = session.scalar(
                select(func.coalesce(func.sum(TradeFill.quantity), 0))
                .where(TradeFill.order_id == order_id)
            )

            if Decimal(str(fills_quantity)) > 0:
                raise RiskRejected(
                    "No se puede liberar la reserva mientras existan "
                    "ejecuciones registradas. Deben reconciliarse primero."
                )

            if order.status not in {
                "pending_submission",
                "new",
            }:
                raise RiskRejected(
                    "La orden no puede liberarse desde su estado actual."
                )

            allocation = session.execute(
                select(RiskAllocation)
                .where(
                    RiskAllocation.order_id == order_id,
                    RiskAllocation.status == "active",
                    RiskAllocation.allocation_type == "order",
                )
                .with_for_update()
            ).scalar_one_or_none()

            if allocation is None:
                raise RiskRejected(
                    "La orden no tiene una reserva activa."
                )

            allocation.status = "released"
            allocation.released_at = datetime.now(timezone.utc)
            order.status = status

    def convert_to_position(
        self,
        order_id: int,
        *,
        quantity: Decimal,
        entry_price: Decimal,
    ) -> int:
        if (
            not quantity.is_finite()
            or not entry_price.is_finite()
            or quantity <= 0
            or entry_price <= 0
        ):
            raise ValueError(
                "Cantidad y precio deben ser positivos y finitos."
            )

        with SessionLocal.begin() as session:
            self._lock(session)

            order = session.get(
                TradeOrder,
                order_id,
                with_for_update=True,
            )

            if order is None:
                raise ValueError(
                    f"No existe la orden {order_id}."
                )

            if order.side != "buy":
                raise RiskRejected(
                    "Solo se permiten entradas BUY en Binance Spot."
                )

            if order.status != "filled":
                raise RiskRejected(
                    "La orden debe estar confirmada como ejecutada "
                    "antes de crear la posición."
                )
            
            fills = session.execute(
                select(
                    func.coalesce(func.sum(TradeFill.quantity), 0),
                    func.coalesce(
                        func.sum(TradeFill.quantity * TradeFill.price),
                        0,
                    ),
                ).where(TradeFill.order_id == order_id)
            ).one()

            executed_quantity = Decimal(str(fills[0]))
            executed_value = Decimal(str(fills[1]))

            if (
                not executed_quantity.is_finite()
                or not executed_value.is_finite()
                or executed_quantity <= 0
                or executed_value <= 0
            ):
                raise RiskRejected(
                    "La orden no tiene ejecuciones válidas registradas."
                )

            if executed_quantity != order.quantity:
                raise RiskRejected(
                    "La cantidad ejecutada no coincide con la orden."
                )

            quantity = executed_quantity
            entry_price = executed_value / executed_quantity

            allocation = session.execute(
                select(RiskAllocation)
                .where(
                    RiskAllocation.order_id == order_id,
                    RiskAllocation.status == "active",
                    RiskAllocation.allocation_type == "order",
                )
                .with_for_update()
            ).scalar_one_or_none()

            if allocation is None:
                raise RiskRejected(
                    "La orden no tiene una reserva activa."
                )

            position = TradePosition(
                bot_run_id=order.bot_run_id,
                symbol=order.symbol,
                side="long",
                status="open",
                quantity=quantity,
                entry_price=entry_price,
            )

            session.add(position)
            session.flush()

            allocation.position_id = position.id
            allocation.allocation_type = "position"

            return position.id

    def close_position(
        self,
        position_id: int,
        *,
        exit_price: Decimal,
        realized_pnl_eur: Decimal,
    ) -> None:
        if not exit_price.is_finite() or exit_price <= 0:
            raise ValueError(
                "El precio de salida debe ser positivo y finito."
            )

        if not realized_pnl_eur.is_finite():
            raise ValueError(
                "El PnL en euros debe ser finito."
            )

        with SessionLocal.begin() as session:
            self._lock(session)

            position = session.get(
                TradePosition,
                position_id,
                with_for_update=True,
            )

            if position is None:
                raise ValueError(
                    f"No existe la posición {position_id}."
                )

            if position.status != "open":
                raise RiskRejected(
                    "La posición no está abierta."
                )

            allocation = session.execute(
                select(RiskAllocation)
                .where(
                    RiskAllocation.position_id == position_id,
                    RiskAllocation.status == "active",
                    RiskAllocation.allocation_type == "position",
                )
                .with_for_update()
            ).scalar_one_or_none()

            if allocation is None:
                raise RiskRejected(
                    "La posición no tiene una reserva activa."
                )

            now = datetime.now(timezone.utc)
            pnl = realized_pnl_eur

            position.exit_price = exit_price
            position.realized_pnl = pnl
            position.status = "closed"
            position.closed_at = now

            allocation.status = "released"
            allocation.released_at = now

            account = self._get_account(session)
            account.daily_pnl_eur += pnl
            account.total_pnl_eur += pnl

            open_positions = session.scalar(
                select(func.count())
                .select_from(TradePosition)
                .where(TradePosition.status == "open")
            )

            max_daily_loss = Decimal(
                str(self.config.max_daily_loss_eur)
            )
            max_total_loss = Decimal(
                str(self.config.max_total_loss_eur)
            )

            session.add(
                RiskSnapshot(
                    bot_run_id=position.bot_run_id,
                    capital_eur=Decimal(
                        str(self.config.max_capital_eur)
                    ),
                    daily_pnl_eur=account.daily_pnl_eur,
                    total_pnl_eur=account.total_pnl_eur,
                    open_positions=open_positions,
                    risk_allowed=(
                        account.daily_pnl_eur > -max_daily_loss
                        and account.total_pnl_eur > -max_total_loss
                    ),
                    reason="Cierre de posición",
                )
            )

