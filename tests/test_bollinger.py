"""Tests para BollingerBreakoutStrategy."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.storage.models import MarketCandle
from app.strategies.base import StrategyContext
from app.strategies.bollinger import BollingerBreakoutStrategy


def _make_candles(closes: list[Decimal]) -> list[MarketCandle]:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        MarketCandle(
            symbol="BTCUSDT",
            interval="1m",
            open_time=base + timedelta(minutes=i),
            close_time=base + timedelta(minutes=i + 1),
            open=c,
            high=c + Decimal("1"),
            low=c - Decimal("1"),
            close=c,
            volume=Decimal("10"),
            is_closed=True,
        )
        for i, c in enumerate(closes)
    ]


def test_rejects_invalid_parameters():
    with pytest.raises(ValueError, match="periodo"):
        BollingerBreakoutStrategy(period=1)

    with pytest.raises(ValueError, match="multiplicador"):
        BollingerBreakoutStrategy(period=20, std_dev_multiplier=Decimal("0"))


def test_returns_empty_with_insufficient_candles():
    strategy = BollingerBreakoutStrategy(period=20)
    candles = _make_candles([Decimal("100")] * 15)
    context = StrategyContext(symbol="BTCUSDT", interval="1m", candles=candles)
    assert strategy.generate_signals(context) == []


def test_generates_buy_on_upper_band_breakout():
    strategy = BollingerBreakoutStrategy(period=5, std_dev_multiplier=Decimal("1.5"))
    # Flat market then sudden explosion above upper band
    closes = [
        Decimal("100"),
        Decimal("101"),
        Decimal("100"),
        Decimal("100"),
        Decimal("101"),
        Decimal("100"),
        Decimal("115"),  # Big breakout
    ]
    candles = _make_candles(closes)
    context = StrategyContext(symbol="BTCUSDT", interval="1m", candles=candles)

    signals = strategy.generate_signals(context)

    assert len(signals) == 1
    assert signals[0].side == "BUY"
    assert "Ruptura alcista" in signals[0].reason


def test_generates_sell_on_mean_drop():
    strategy = BollingerBreakoutStrategy(period=5, std_dev_multiplier=Decimal("1.5"))
    # High prices then dropping below mean
    closes = [
        Decimal("110"),
        Decimal("110"),
        Decimal("110"),
        Decimal("110"),
        Decimal("110"),
        Decimal("110"),
        Decimal("102"),  # Falls below SMA
    ]
    candles = _make_candles(closes)
    context = StrategyContext(symbol="BTCUSDT", interval="1m", candles=candles)

    signals = strategy.generate_signals(context)

    assert len(signals) == 1
    assert signals[0].side == "SELL"
    assert "Pérdida de media móvil" in signals[0].reason

