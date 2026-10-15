
from datetime import datetime, timezone
from decimal import Decimal

from app.market_data.binance_client import BinanceMarketClient
from app.storage.repository import upsert_candle


def collect_candles(
    symbol: str = "BTCUSDT",
    interval: str = "1m",
    limit: int = 100,
) -> int:
    client = BinanceMarketClient()

    try:
        klines = client.get_klines(
            symbol=symbol,
            interval=interval,
            limit=limit,
        )
    finally:
        client.close()

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    saved = 0

    for kline in klines:
        open_time_ms = int(kline[0])
        close_time_ms = int(kline[6])

        if close_time_ms >= now_ms:
            continue

        upsert_candle(
            symbol=symbol.upper(),
            interval=interval,
            open_time=datetime.fromtimestamp(
                open_time_ms / 1000,
                tz=timezone.utc,
            ),
            close_time=datetime.fromtimestamp(
                close_time_ms / 1000,
                tz=timezone.utc,
            ),
            open_price=Decimal(kline[1]),
            high=Decimal(kline[2]),
            low=Decimal(kline[3]),
            close=Decimal(kline[4]),
            volume=Decimal(kline[5]),
            is_closed=True,
        )
        saved += 1

    return saved
