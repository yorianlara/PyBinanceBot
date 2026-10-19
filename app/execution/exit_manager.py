from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Protocol
from uuid import uuid4

import httpx

from app.execution.models import ExecutionResult, ExecutionStatus
from app.risk.persistent_manager import RiskRejected
from app.storage.repository import get_open_position


class ExitClient(Protocol):
    base_url: str

    def get_symbol_info(self, symbol: str) -> dict: ...

    def get_price(self, symbol: str) -> dict: ...

    def get_balances(self) -> list[dict]: ...

    def create_order(self, **kwargs) -> dict: ...

    def cancel_order(self, symbol: str, order_id: int | str) -> dict: ...

    def get_order(self, symbol: str, order_id: int | str) -> dict: ...

    def get_my_trades(self, symbol: str, order_id: int) -> list[dict]: ...


class ExitRiskController(Protocol):
    def reserve_exit_order(
        self,
        *,
        position_id: int,
        quantity: Decimal,
        client_order_id: str,
    ) -> int: ...

    def mark_submitted(self, order_id: int, exchange_order_id: str) -> None: ...

    def reconcile_fills(
        self,
        order_id: int,
        exchange_order: dict,
        exchange_trades: list[dict],
        expected_side: str = "buy",
    ) -> None: ...

    def finalize_exit_order(
        self,
        *,
        position_id: int,
        order_id: int,
        eur_usdt_rate: Decimal,
        base_asset: str,
        quote_asset: str,
    ) -> tuple[Decimal, Decimal, bool, list[str]]: ...


class PositionExitManager:
    def __init__(self, client: ExitClient, risk_manager: ExitRiskController):
        if client.base_url.rstrip("/") != "https://testnet.binance.vision":
            raise ValueError("La gestión de salida solo opera en Spot Testnet.")
        self.client = client
        self.risk_manager = risk_manager

    def close_for_signal(
        self,
        *,
        strategy_key: str,
        symbol: str,
        bot_run_id: int | None = None,
    ) -> ExecutionResult:
        symbol = symbol.upper()
        position = get_open_position(symbol)
        if position is None:
            return self._result(
                strategy_key,
                symbol,
                "rejected",
                "No hay una posición abierta de este símbolo para cerrar.",
            )
        if position.side != "long":
            return self._result(
                strategy_key,
                symbol,
                "rejected",
                "La posición no es una posición Spot long compatible.",
            )

        symbol_info = self.client.get_symbol_info(symbol)
        if symbol_info.get("status") != "TRADING":
            raise ValueError(f"El símbolo {symbol} no está habilitado para trading.")
        if symbol_info.get("quoteAsset") != "USDT":
            raise ValueError("La gestión de salida requiere un par cotizado en USDT.")

        market_price = self._positive_decimal(
            self.client.get_price(symbol).get("price"),
            f"precio de {symbol}",
        )
        free_quantity = self._free_balance(
            symbol_info["baseAsset"],
            self.client.get_balances(),
        )
        try:
            quantity = self._sell_quantity(
                symbol_info,
                min(Decimal(str(position.quantity)), free_quantity),
            )
        except RiskRejected as exc:
            return self._result(
                strategy_key,
                symbol,
                "rejected",
                str(exc),
                position_id=position.id,
            )
        min_notional = self._minimum_market_notional(symbol_info)
        if min_notional is not None and quantity * market_price < min_notional:
            raise RiskRejected(
                f"La cantidad disponible para cerrar {symbol} queda por "
                f"debajo del mínimo de {min_notional} USDT."
            )

        client_order_id = f"exit-{uuid4().hex}"
        try:
            order_id = self.risk_manager.reserve_exit_order(
                position_id=position.id,
                quantity=quantity,
                client_order_id=client_order_id,
            )
        except RiskRejected as exc:
            return self._result(
                strategy_key,
                symbol,
                "rejected",
                str(exc),
            )

        try:
            order_response = self.client.create_order(
                symbol=symbol,
                side="SELL",
                type="MARKET",
                quantity=quantity,
                new_client_order_id=client_order_id,
            )
        except (httpx.HTTPError, RuntimeError) as exc:
            raise RuntimeError(
                f"No se pudo confirmar el envío de la salida {order_id}; "
                "la orden queda reservada para reconciliarla con Binance."
            ) from exc

        try:
            exchange_order_id = int(order_response["orderId"])
        except (KeyError, TypeError, ValueError):
            raise RuntimeError(
                f"Binance no devolvió orderId válido para la salida {order_id}; "
                "la orden queda reservada para evitar duplicados."
            ) from None

        self.risk_manager.mark_submitted(order_id, str(exchange_order_id))
        exchange_order = self._fetch_and_reconcile(
            order_id,
            symbol,
            exchange_order_id,
        )

        if exchange_order.get("status") in {"NEW", "PARTIALLY_FILLED"}:
            try:
                self.client.cancel_order(symbol, exchange_order_id)
            except (httpx.HTTPError, RuntimeError) as exc:
                raise RuntimeError(
                    f"No se pudo confirmar la cancelación de la salida "
                    f"{order_id}; la orden permanece pendiente de reconciliar."
                ) from exc
            exchange_order = self._fetch_and_reconcile(
                order_id,
                symbol,
                exchange_order_id,
            )
            if exchange_order.get("status") in {"NEW", "PARTIALLY_FILLED"}:
                raise RuntimeError(
                    f"La salida {order_id} sigue activa tras solicitar la "
                    "cancelación; requiere reconciliación antes de otro intento."
                )

        status = exchange_order.get("status")
        if status not in {"FILLED", "CANCELED", "EXPIRED", "REJECTED"}:
            raise RuntimeError(
                f"Estado de salida no esperado para la orden {order_id}: {status!r}."
            )
        if Decimal(str(exchange_order["executedQty"])) <= 0:
            return self._result(
                strategy_key,
                symbol,
                "rejected",
                f"Binance terminó la salida sin fills ({status}).",
                order_id=order_id,
                position_id=position.id,
            )

        eur_usdt_rate = self._positive_decimal(
            self.client.get_price("EURUSDT").get("price"),
            "precio de EURUSDT",
        )
        (
            executed_quantity,
            average_exit,
            fully_closed,
            external_commission_assets,
        ) = (
            self.risk_manager.finalize_exit_order(
                position_id=position.id,
                order_id=order_id,
                eur_usdt_rate=eur_usdt_rate,
                base_asset=symbol_info["baseAsset"],
                quote_asset=symbol_info["quoteAsset"],
            )
        )
        status = "exited" if fully_closed else "partially_exited"
        commission_note = (
            " PnL excluye conversión de comisiones en "
            f"{', '.join(external_commission_assets)}."
            if external_commission_assets
            else ""
        )
        return self._result(
            strategy_key,
            symbol,
            status,
            (
                f"Vendidos {executed_quantity} {symbol_info['baseAsset']} "
                f"a un precio medio de {average_exit} "
                f"{symbol_info['quoteAsset']}.{commission_note}"
            ),
            order_id=order_id,
            position_id=position.id,
        )

    def _fetch_and_reconcile(
        self,
        order_id: int,
        symbol: str,
        exchange_order_id: int,
    ) -> dict:
        try:
            exchange_order = self.client.get_order(symbol, exchange_order_id)
            trades = self.client.get_my_trades(symbol, exchange_order_id)
            self.risk_manager.reconcile_fills(
                order_id,
                exchange_order,
                trades,
                expected_side="sell",
            )
        except (httpx.HTTPError, RiskRejected, RuntimeError) as exc:
            raise RuntimeError(
                f"No se pudo reconciliar la salida local {order_id} "
                f"(Binance {exchange_order_id}); la reserva permanece activa."
            ) from exc
        return exchange_order

    @staticmethod
    def _result(
        strategy_key: str,
        symbol: str,
        status: ExecutionStatus,
        reason: str,
        *,
        order_id: int | None = None,
        position_id: int | None = None,
    ) -> ExecutionResult:
        return ExecutionResult(
            strategy_key=strategy_key,
            symbol=symbol,
            status=status,
            reason=reason,
            order_id=order_id,
            position_id=position_id,
        )

    @staticmethod
    def _free_balance(asset: str, balances: list[dict]) -> Decimal:
        for balance in balances:
            if balance.get("asset") == asset:
                try:
                    amount = Decimal(str(balance["free"]))
                except (InvalidOperation, KeyError, TypeError, ValueError):
                    raise ValueError(
                        f"Binance devolvió un saldo inválido para {asset}."
                    ) from None
                if not amount.is_finite() or amount < 0:
                    raise ValueError(
                        f"Binance devolvió un saldo inválido para {asset}."
                    )
                return amount
        return Decimal(0)

    @classmethod
    def _sell_quantity(
        cls,
        symbol_info: dict,
        available_quantity: Decimal,
    ) -> Decimal:
        filters = {
            item.get("filterType"): item for item in symbol_info.get("filters", [])
        }
        lot_filter = filters.get("MARKET_LOT_SIZE")
        if not cls._has_positive_lot_limits(lot_filter):
            lot_filter = filters.get("LOT_SIZE")
        if lot_filter is None:
            raise ValueError("Binance no devolvió filtros de cantidad para el símbolo.")

        minimum = cls._positive_decimal(lot_filter.get("minQty"), "cantidad mínima")
        maximum = cls._positive_decimal(lot_filter.get("maxQty"), "cantidad máxima")
        step = cls._positive_decimal(
            lot_filter.get("stepSize"), "incremento de cantidad"
        )
        quantity = (available_quantity / step).to_integral_value(
            rounding=ROUND_DOWN
        ) * step
        if quantity < minimum or quantity > maximum:
            raise RiskRejected(
                "El saldo disponible no permite vender una cantidad válida "
                "según los filtros de Binance."
            )
        return quantity

    @staticmethod
    def _has_positive_lot_limits(lot_filter: dict | None) -> bool:
        if lot_filter is None:
            return False
        try:
            values = [
                Decimal(str(lot_filter[key]))
                for key in ("minQty", "maxQty", "stepSize")
            ]
        except (InvalidOperation, KeyError, TypeError, ValueError):
            return False
        return all(value.is_finite() and value > 0 for value in values)

    @staticmethod
    def _minimum_market_notional(symbol_info: dict) -> Decimal | None:
        filters = {
            item.get("filterType"): item for item in symbol_info.get("filters", [])
        }
        for filter_type, apply_key in (
            ("NOTIONAL", "applyMinToMarket"),
            ("MIN_NOTIONAL", "applyToMarket"),
        ):
            item = filters.get(filter_type)
            if item is None or not item.get(apply_key, True):
                continue
            return PositionExitManager._positive_decimal(
                item.get("minNotional"),
                "notional mínimo",
            )
        return None

    @staticmethod
    def _positive_decimal(value: object, label: str) -> Decimal:
        try:
            result = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            raise ValueError(f"Binance devolvió un {label} inválido.") from None
        if not result.is_finite() or result <= 0:
            raise ValueError(f"Binance devolvió un {label} inválido.")
        return result
