
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.storage.database import SessionLocal
from app.storage.models import (
    BotRun,
    MarketCandle,
    RiskSnapshot,
    TradeFill,
    TradeOrder,
    TradePosition,
)


def create_run(mode: str = "testnet", details: str | None = None) -> int:
    with SessionLocal.begin() as session:
        run = BotRun(mode=mode, status="running", details=details)
        session.add(run)
        session.flush()
        return run.id


def finish_run(
    run_id: int,
    status: str = "completed",
    details: str | None = None,
) -> None:
    with SessionLocal.begin() as session:
        run = session.get(BotRun, run_id)
        if run is None:
            raise ValueError(f"No existe la ejecución {run_id}.")
        run.status = status
        run.finished_at = datetime.now().astimezone()
        if details is not None:
            run.details = details


def upsert_candle(
    *,
    symbol: str,
    interval: str,
    open_time: datetime,
    close_time: datetime,
    open_price: Decimal,
    high: Decimal,
    low: Decimal,
    close: Decimal,
    volume: Decimal,
    is_closed: bool = True,
) -> None:
    values = {
        "symbol": symbol.upper(),
        "interval": interval,
        "open_time": open_time,
        "close_time": close_time,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "is_closed": is_closed,
    }

    statement = insert(MarketCandle).values(**values)
    statement = statement.on_conflict_do_update(
        constraint="uq_market_candle_symbol_interval_open_time",
        set_={
            key: getattr(statement.excluded, key)
            for key in (
                "close_time", "open", "high", "low",
                "close", "volume", "is_closed",
            )
        },
    )

    with SessionLocal.begin() as session:
        session.execute(statement)


def get_latest_candles(
    symbol: str,
    interval: str,
    limit: int = 100,
) -> list[MarketCandle]:
    if not 1 <= limit <= 5000:
        raise ValueError("limit debe estar entre 1 y 5000.")

    with SessionLocal() as session:
        statement = (
            select(MarketCandle)
            .where(
                MarketCandle.symbol == symbol.upper(),
                MarketCandle.interval == interval,
                MarketCandle.is_closed.is_(True),
            )
            .order_by(MarketCandle.open_time.desc())
            .limit(limit)
        )
        return list(reversed(session.scalars(statement).all()))


def create_order(
    *,
    symbol: str,
    side: str,
    order_type: str,
    quantity: Decimal,
    bot_run_id: int | None = None,
    client_order_id: str | None = None,
    limit_price: Decimal | None = None,
    status: str = "new",
    exchange_order_id: str | None = None,
    raw_response: str | None = None,
) -> int:
    if side.lower() not in {"buy", "sell"}:
        raise ValueError("side debe ser 'buy' o 'sell'.")
    if quantity <= 0:
        raise ValueError("quantity debe ser mayor que cero.")

    with SessionLocal.begin() as session:
        order = TradeOrder(
            bot_run_id=bot_run_id,
            symbol=symbol.upper(),
            side=side.lower(),
            order_type=order_type.upper(),
            quantity=quantity,
            client_order_id=client_order_id,
            limit_price=limit_price,
            status=status.lower(),
            exchange_order_id=exchange_order_id,
            raw_response=raw_response,
        )
        session.add(order)
        session.flush()
        return order.id


def add_fill(
    *,
    order_id: int,
    quantity: Decimal,
    price: Decimal,
    executed_at: datetime,
    exchange_trade_id: str | None = None,
    commission: Decimal = Decimal("0"),
    commission_asset: str | None = None,
) -> int | None:
    if quantity <= 0 or price <= 0 or commission < 0:
        raise ValueError(
            "quantity y price deben ser positivos; "
            "commission no puede ser negativa."
        )

    with SessionLocal.begin() as session:
        if session.get(TradeOrder, order_id) is None:
            raise ValueError(f"No existe la orden {order_id}.")

        values = {
            "order_id": order_id,
            "exchange_trade_id": exchange_trade_id,
            "quantity": quantity,
            "price": price,
            "executed_at": executed_at,
            "commission": commission,
            "commission_asset": commission_asset,
        }
        statement = insert(TradeFill).values(**values)

        if exchange_trade_id is not None:
            statement = statement.on_conflict_do_nothing(
                constraint="uq_trade_fills_exchange_trade_id"
            )

        result = session.execute(statement.returning(TradeFill.id))
        return result.scalar_one_or_none()


def open_position(
    *,
    symbol: str,
    quantity: Decimal,
    entry_price: Decimal,
    bot_run_id: int | None = None,
    side: str = "long",
) -> int:
    if quantity <= 0 or entry_price <= 0:
        raise ValueError("quantity y entry_price deben ser mayores que cero.")
    if side.lower() not in {"long", "short"}:
        raise ValueError("side debe ser 'long' o 'short'.")

    with SessionLocal.begin() as session:
        position = TradePosition(
            bot_run_id=bot_run_id,
            symbol=symbol.upper(),
            side=side.lower(),
            status="open",
            quantity=quantity,
            entry_price=entry_price,
        )
        session.add(position)
        session.flush()
        return position.id


def close_position(
    position_id: int,
    *,
    exit_price: Decimal,
    realized_pnl: Decimal,
) -> None:
    if exit_price <= 0:
        raise ValueError("exit_price debe ser mayor que cero.")

    with SessionLocal.begin() as session:
        position = session.get(
            TradePosition,
            position_id,
            with_for_update=True,
        )
        if position is None:
            raise ValueError(f"No existe la posición {position_id}.")
        if position.status != "open":
            raise ValueError(f"La posición {position_id} no está abierta.")

        position.exit_price = exit_price
        position.realized_pnl = realized_pnl
        position.status = "closed"
        position.closed_at = datetime.now().astimezone()


def record_risk_snapshot(
    *,
    capital_eur: Decimal,
    risk_allowed: bool,
    daily_pnl_eur: Decimal = Decimal("0"),
    total_pnl_eur: Decimal = Decimal("0"),
    open_positions: int = 0,
    reason: str | None = None,
    bot_run_id: int | None = None,
) -> int:
    if capital_eur < 0 or open_positions < 0:
        raise ValueError(
            "capital_eur y open_positions no pueden ser negativos."
        )

    with SessionLocal.begin() as session:
        snapshot = RiskSnapshot(
            bot_run_id=bot_run_id,
            capital_eur=capital_eur,
            daily_pnl_eur=daily_pnl_eur,
            total_pnl_eur=total_pnl_eur,
            open_positions=open_positions,
            risk_allowed=risk_allowed,
            reason=reason,
        )
        session.add(snapshot)
        session.flush()
        return snapshot.id
