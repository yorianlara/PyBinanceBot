import pytest

from app.storage.models import TradeFill, TradeOrder
from app.storage.repository import add_fill, create_order

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import delete, select

from app.storage.database import SessionLocal
from app.storage.models import BotRun, MarketCandle
from app.storage.repository import (
    create_run,
    finish_run,
    get_latest_candles,
    upsert_candle,
)


def test_repository_saves_and_updates_candles():
    run_id = create_run(mode="testnet", details="Prueba automatizada")
    open_time = datetime.now(timezone.utc).replace(microsecond=0)
    symbol = "TESTREPOSITORY"

    try:
        values = {
            "symbol": symbol,
            "interval": "1m",
            "open_time": open_time,
            "close_time": open_time + timedelta(minutes=1),
            "open_price": Decimal("100"),
            "high": Decimal("105"),
            "low": Decimal("99"),
            "close": Decimal("103"),
            "volume": Decimal("10"),
            "is_closed": True,
        }

        upsert_candle(**values)
        values["close"] = Decimal("104")
        upsert_candle(**values)

        candles = get_latest_candles(symbol, "1m", limit=10)

        assert len(candles) == 1
        assert candles[0].close == Decimal("104")

        finish_run(run_id, status="completed")

    finally:
        with SessionLocal.begin() as session:
            session.execute(
                delete(MarketCandle).where(
                    MarketCandle.symbol == symbol,
                    MarketCandle.interval == "1m",
                    MarketCandle.open_time == open_time,
                )
            )
            session.execute(
                delete(BotRun).where(BotRun.id == run_id)
            )

def test_add_fill_persists_execution():
    client_trade_id = f"test-{__import__('uuid').uuid4().hex}"
    order_id = create_order(
        symbol="BTCUSDT",
        side="buy",
        order_type="LIMIT",
        quantity=Decimal("0.002"),
        status="filled",
        client_order_id=f"client-{__import__('uuid').uuid4().hex}",
    )

    try:
        fill_id = add_fill(
            order_id=order_id,
            quantity=Decimal("0.001"),
            price=Decimal("50000"),
            executed_at=datetime.now(timezone.utc),
            exchange_trade_id=client_trade_id,
            commission=Decimal("0.000001"),
            commission_asset="BTC",
        )

        assert fill_id is not None

        with SessionLocal() as session:
            fill = session.get(TradeFill, fill_id)
            assert fill is not None
            assert fill.order_id == order_id
            assert fill.quantity == Decimal("0.0010000000")
            assert fill.price == Decimal("50000.0000000000")
            assert fill.commission_asset == "BTC"
    finally:
        with SessionLocal.begin() as session:
            session.execute(
                delete(TradeFill).where(
                    TradeFill.order_id == order_id
                )
            )
            session.execute(
                delete(TradeOrder).where(TradeOrder.id == order_id)
            )


def test_add_fill_ignores_duplicate_exchange_trade_id():
    exchange_trade_id = f"test-{__import__('uuid').uuid4().hex}"
    order_id = create_order(
        symbol="BTCUSDT",
        side="buy",
        order_type="LIMIT",
        quantity=Decimal("0.002"),
        status="filled",
        client_order_id=f"client-{__import__('uuid').uuid4().hex}",
    )

    try:
        first_id = add_fill(
            order_id=order_id,
            quantity=Decimal("0.001"),
            price=Decimal("50000"),
            executed_at=datetime.now(timezone.utc),
            exchange_trade_id=exchange_trade_id,
        )
        duplicate_id = add_fill(
            order_id=order_id,
            quantity=Decimal("0.001"),
            price=Decimal("50000"),
            executed_at=datetime.now(timezone.utc),
            exchange_trade_id=exchange_trade_id,
        )

        assert first_id is not None
        assert duplicate_id is None

        with SessionLocal() as session:
            fills = session.scalars(
                select(TradeFill).where(
                    TradeFill.exchange_trade_id == exchange_trade_id
                )
            ).all()
            assert len(fills) == 1
    finally:
        with SessionLocal.begin() as session:
            session.execute(
                delete(TradeFill).where(
                    TradeFill.order_id == order_id
                )
            )
            session.execute(
                delete(TradeOrder).where(TradeOrder.id == order_id)
            )


def test_add_fill_rejects_invalid_values():
    with pytest.raises(ValueError, match="positivos"):
        add_fill(
            order_id=1,
            quantity=Decimal("0"),
            price=Decimal("50000"),
            executed_at=datetime.now(timezone.utc),
        )
