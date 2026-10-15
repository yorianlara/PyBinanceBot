"""Tests para MacdStrategy."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.storage.models import MarketCandle
from app.strategies.base import StrategyContext
from app.strategies.macd import MacdStrategy


def _make_candles(closes: list[Decimal]) -> list[MarketCandle]:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        MarketCandle(
            symbol="BTCUSDT",
            interval="1m",
            open_time=base + timedelta(minutes=i),
            close_time=base + timedelta(minutes=i + 1),
            open=c,
            high=c + Decimal("0.5"),
            low=c - Decimal("0.5"),
            close=c,
            volume=Decimal("10"),
            is_closed=True,
        )
        for i, c in enumerate(closes)
    ]


def test_rejects_invalid_parameters():
    with pytest.raises(ValueError, match="mayores que cero"):
        MacdStrategy(fast_period=0)

    with pytest.raises(ValueError, match="menor que el lento"):
        MacdStrategy(fast_period=26, slow_period=12)


def test_returns_empty_with_insufficient_candles():
    strategy = MacdStrategy(fast_period=5, slow_period=10, signal_period=5)
    candles = _make_candles([Decimal("100")] * 10)
    context = StrategyContext(symbol="BTCUSDT", interval="1m", candles=candles)
    assert strategy.generate_signals(context) == []


def test_detects_bullish_macd_crossover():
    strategy = MacdStrategy(fast_period=2, slow_period=4, signal_period=2)
    # Downtrend then strong upward turn with cross at index 7
    closes = [
        Decimal("100"),
        Decimal("95"),
        Decimal("90"),
        Decimal("85"),
        Decimal("80"),
        Decimal("75"),
        Decimal("70"),
        Decimal("75"),
    ]
    candles = _make_candles(closes)
    context = StrategyContext(symbol="BTCUSDT", interval="1m", candles=candles)

    signals = strategy.generate_signals(context)

    assert len(signals) == 1
    assert signals[0].side == "BUY"
    assert "Cruce MACD" in signals[0].reason


def test_detects_bearish_macd_crossover():
    strategy = MacdStrategy(fast_period=2, slow_period=4, signal_period=2)
    # Uptrend then strong downward reversal with cross at index 7
    closes = [
        Decimal("50"),
        Decimal("55"),
        Decimal("60"),
        Decimal("65"),
        Decimal("70"),
        Decimal("75"),
        Decimal("80"),
        Decimal("75"),
    ]
    candles = _make_candles(closes)
    context = StrategyContext(symbol="BTCUSDT", interval="1m", candles=candles)

    signals = strategy.generate_signals(context)

    assert len(signals) == 1
    assert signals[0].side == "SELL"
    assert "Cruce MACD" in signals[0].reason
