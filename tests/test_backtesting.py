"""Tests para el motor de backtesting."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.backtesting.engine import BacktestEngine
from app.backtesting.models import BacktestReport, BacktestTrade
from app.execution.exit_config import ExitConfig
from app.storage.models import MarketCandle
from app.strategies.base import StrategySignal
from app.strategies.ema_cross import EmaCrossStrategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_synthetic_candles(
    count: int = 50,
    base_price: Decimal = Decimal("60000"),
    step: Decimal = Decimal("50"),
) -> list[MarketCandle]:
    candles = []
    start_time = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    price = base_price

    for i in range(count):
        o_time = start_time + timedelta(minutes=i)
        c_time = o_time + timedelta(minutes=1)
        candles.append(
            MarketCandle(
                symbol="BTCUSDT",
                interval="1m",
                open_time=o_time,
                close_time=c_time,
                open=price,
                high=price + Decimal("0.5"),
                low=price - Decimal("0.5"),
                close=price + step,
                volume=Decimal("10"),
                is_closed=True,
            )
        )
        price += step

    return candles


# ---------------------------------------------------------------------------
# Tests: Validation & Edge Cases
# ---------------------------------------------------------------------------

class TestBacktestInit:
    def test_rejects_empty_strategies(self):
        with pytest.raises(ValueError, match="al menos una estrategia"):
            BacktestEngine(strategies=[])

    def test_rejects_non_positive_capital(self):
        strat = MagicMock()
        with pytest.raises(ValueError, match="positivos"):
            BacktestEngine(strategies=[strat], initial_capital_eur=Decimal("0"))


class TestBacktestExecution:
    def test_insufficient_candles_returns_empty_report(self):
        strat = MagicMock()
        strat.key = "mock_strat"
        engine = BacktestEngine(strategies=[strat], min_warmup_candles=30)

        candles = _generate_synthetic_candles(count=20)
        report = engine.run(candles)

        assert report.total_trades == 0
        assert report.total_candles == 20
        assert report.total_pnl_eur == Decimal("0")

    def test_backtest_with_mock_signals_triggers_take_profit(self):
        mock_strat = MagicMock()
        mock_strat.key = "test_tp"

        # Signal BUY on first evaluation candle
        buy_signal = StrategySignal(
            symbol="BTCUSDT",
            side="BUY",
            reason="Prueba compra",
            generated_at=datetime.now(timezone.utc),
        )
        mock_strat.generate_signals.side_effect = [
            [buy_signal],
            [],
            [],
            [],
            [],
            [],
            [],
            [],
            [],
            [],
        ]

        engine = BacktestEngine(
            strategies=[mock_strat],
            initial_capital_eur=Decimal("100"),
            allocation_per_trade_eur=Decimal("20"),
            exit_config=ExitConfig(stop_loss_pct=Decimal("3"), take_profit_pct=Decimal("5")),
            min_warmup_candles=5,
        )

        candles = _generate_synthetic_candles(count=15, base_price=Decimal("100"), step=Decimal("2"))
        # Candle 8 has high of 120 -> +20% spike -> triggers take profit
        candles[8].high = Decimal("120")

        report = engine.run(candles)

        assert report.total_trades >= 1
        tp_trades = [t for t in report.trades if t.exit_reason == "take_profit"]
        assert len(tp_trades) == 1
        assert tp_trades[0].realized_pnl_eur > 0
        assert report.winning_trades >= 1

    def test_backtest_with_mock_signals_triggers_stop_loss(self):
        mock_strat = MagicMock()
        mock_strat.key = "test_sl"

        buy_signal = StrategySignal(
            symbol="BTCUSDT",
            side="BUY",
            reason="Prueba compra",
            generated_at=datetime.now(timezone.utc),
        )
        mock_strat.generate_signals.side_effect = [
            [buy_signal],
            [],
            [],
            [],
            [],
            [],
            [],
            [],
            [],
            [],
        ]

        engine = BacktestEngine(
            strategies=[mock_strat],
            initial_capital_eur=Decimal("100"),
            allocation_per_trade_eur=Decimal("20"),
            exit_config=ExitConfig(stop_loss_pct=Decimal("3"), take_profit_pct=Decimal("5")),
            min_warmup_candles=5,
        )

        candles = _generate_synthetic_candles(count=15, base_price=Decimal("100"), step=Decimal("0"))
        # Candle 7 crashes to 90 -> -10% drop -> triggers stop loss
        candles[7].low = Decimal("90")

        report = engine.run(candles)

        sl_trades = [t for t in report.trades if t.exit_reason == "stop_loss"]
        assert len(sl_trades) == 1
        assert sl_trades[0].realized_pnl_eur < 0
        assert report.losing_trades >= 1

    def test_backtest_with_ema_strategy_end_to_end(self):
        strategy = EmaCrossStrategy(fast_period=5, slow_period=10)
        engine = BacktestEngine(
            strategies=[strategy],
            initial_capital_eur=Decimal("100"),
            allocation_per_trade_eur=Decimal("20"),
            min_warmup_candles=12,
        )

        # Generate a bullish trend followed by a drop
        candles_up = _generate_synthetic_candles(count=25, base_price=Decimal("100"), step=Decimal("2"))
        candles_down = _generate_synthetic_candles(count=15, base_price=Decimal("150"), step=Decimal("-3"))

        all_candles = candles_up + candles_down
        # Adjust timestamps
        start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        for i, c in enumerate(all_candles):
            c.open_time = start + timedelta(minutes=i)
            c.close_time = c.open_time + timedelta(minutes=1)

        report = engine.run(all_candles)

        assert isinstance(report, BacktestReport)
        assert report.total_candles == 40
        assert report.strategy_keys == ["ema_cross_5_10"]
        assert report.final_capital_eur > Decimal("0")
