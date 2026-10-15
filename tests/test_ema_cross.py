from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.strategies.base import StrategyContext
from app.strategies.ema_cross import EmaCrossStrategy


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

    return StrategyContext(
        symbol="BTCUSDT",
        interval="1m",
        candles=candles,
    )


def test_rejects_invalid_periods():
    with pytest.raises(ValueError):
        EmaCrossStrategy(fast_period=21, slow_period=9)


def test_returns_no_signal_with_insufficient_candles():
    strategy = EmaCrossStrategy(3, 5)

    assert strategy.generate_signals(make_context([10, 9, 8, 7, 8])) == []


def test_detects_bullish_cross():
    strategy = EmaCrossStrategy(3, 5)
    context = make_context([1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 100])

    signals = strategy.generate_signals(context)

    assert signals
    assert signals[-1].side == "BUY"
    assert signals[-1].symbol == "BTCUSDT"


def test_returns_no_signal_without_cross():
    strategy = EmaCrossStrategy(3, 5)

    assert strategy.generate_signals(make_context([10, 10, 10, 10, 10, 10, 10])) == []


def test_rejects_nonpositive_prices():
    strategy = EmaCrossStrategy(3, 5)

    with pytest.raises(ValueError, match="positivos"):
        strategy.generate_signals(make_context([10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0]))