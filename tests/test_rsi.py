
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.strategies.base import StrategyContext
from app.strategies.rsi import RsiStrategy


def make_context(closes):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = [
        SimpleNamespace(
            close=Decimal(str(price)),
            open_time=start + timedelta(minutes=i),
            close_time=start + timedelta(minutes=i + 1),
        )
        for i, price in enumerate(closes)
    ]
    return StrategyContext("BTCUSDT", "1m", candles)


def test_rejects_invalid_period():
    with pytest.raises(ValueError):
        RsiStrategy(period=1)


def test_returns_no_signal_with_insufficient_candles():
    assert RsiStrategy(period=14).generate_signals(
        make_context([100, 99, 98, 97])
    ) == []


def test_generates_buy_after_recovery_from_oversold():
    closes = [100] * 10 + [90, 80, 70, 60, 50, 45, 40, 60]
    signals = RsiStrategy(period=5).generate_signals(make_context(closes))

    assert signals
    assert signals[-1].side == "BUY"


def test_generates_sell_after_fall_from_overbought():
    closes = [100] * 10 + [110, 120, 130, 140, 150, 155, 160, 140]
    signals = RsiStrategy(period=5).generate_signals(make_context(closes))

    assert signals
    assert signals[-1].side == "SELL"


def test_returns_no_signal_on_flat_prices():
    signals = RsiStrategy(period=5).generate_signals(
        make_context([100] * 20)
    )
    assert signals == []
