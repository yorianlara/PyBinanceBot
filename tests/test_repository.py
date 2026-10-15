
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
