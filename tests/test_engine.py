"""Tests para TradingEngine — ciclo completo del motor autónomo."""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.engine import EngineCycleResult, TradingEngine, build_default_engine
from app.execution.exit_config import ExitConfig
from app.execution.exit_manager import ExitDecision, ExitResult
from app.execution.signal_router import ExecutionResult
from app.storage.models import MarketCandle, TradePosition
from app.strategies.base import StrategySignal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_candle(symbol="BTCUSDT"):
    candle = MarketCandle(
        symbol=symbol,
        interval="1m",
        open_time=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
        close_time=datetime(2026, 1, 1, 12, 1, tzinfo=timezone.utc),
        open=Decimal("60000"),
        high=Decimal("60100"),
        low=Decimal("59900"),
        close=Decimal("60050"),
        volume=Decimal("10"),
        is_closed=True,
    )
    return candle


def _make_engine(
    *,
    symbols=None,
    interval="1m",
    candles_count=5,
    signals_by_strat=None,
    exit_results=None,
    route_results=None,
):
    coordinator = MagicMock()
    risk_manager = MagicMock()
    executor = MagicMock()
    router = MagicMock()
    exit_manager = MagicMock()

    coordinator.run.return_value = signals_by_strat or {}
    exit_manager.check_and_exit_positions.return_value = exit_results or []
    exit_manager.check_signal_exit.return_value = None
    router.route_all.return_value = route_results or []

    engine = TradingEngine(
        coordinator=coordinator,
        risk_manager=risk_manager,
        executor=executor,
        router=router,
        exit_manager=exit_manager,
        symbols=symbols or ["BTCUSDT"],
        interval=interval,
    )
    return engine, coordinator, exit_manager, router


# ---------------------------------------------------------------------------
# Tests: run_once
# ---------------------------------------------------------------------------

class TestTradingEngineRunOnce:
    @patch("app.engine.collect_candles", return_value=10)
    @patch("app.engine.get_latest_candles")
    @patch("app.engine.get_open_positions", return_value=[])
    def test_run_once_basic_cycle(
        self,
        mock_get_open_positions,
        mock_get_candles,
        mock_collect_candles,
    ):
        mock_get_candles.return_value = [_make_candle()]
        engine, coordinator, exit_manager, router = _make_engine()

        result = engine.run_once()

        assert isinstance(result, EngineCycleResult)
        assert result.candles_saved == {"BTCUSDT": 10}
        mock_collect_candles.assert_called_once_with(
            symbol="BTCUSDT",
            interval="1m",
            limit=100,
        )
        exit_manager.check_and_exit_positions.assert_called_once()
        coordinator.run.assert_called_once()

    @patch("app.engine.collect_candles", side_effect=Exception("Binance network down"))
    @patch("app.engine.get_latest_candles", return_value=[])
    @patch("app.engine.get_open_positions", return_value=[])
    def test_run_once_handles_candle_collection_error(
        self,
        mock_get_open_positions,
        mock_get_candles,
        mock_collect_candles,
    ):
        engine, _, _, _ = _make_engine()

        result = engine.run_once()

        assert result.candles_saved == {"BTCUSDT": 0}
        assert result.execution_results == []

    @patch("app.engine.collect_candles", return_value=5)
    @patch("app.engine.get_latest_candles")
    @patch("app.engine.get_open_positions", return_value=[])
    def test_run_once_routes_buy_signals(
        self,
        mock_get_open_positions,
        mock_get_candles,
        mock_collect_candles,
    ):
        mock_get_candles.return_value = [_make_candle()]
        buy_signal = StrategySignal(
            symbol="BTCUSDT",
            side="BUY",
            reason="EMA Bullish",
            generated_at=datetime.now(timezone.utc),
        )
        engine, coordinator, _, router = _make_engine(
            signals_by_strat={"ema_cross": [buy_signal]},
        )
        expected_exec = ExecutionResult(
            strategy_key="ema_cross",
            signal=buy_signal,
            order_id=1,
            position_id=10,
            status="executed",
            reason="Compra ejecutada",
        )
        router.route_all.return_value = [expected_exec]

        result = engine.run_once()

        router.route_all.assert_called_once_with({"ema_cross": [buy_signal]})
        assert result.execution_results == [expected_exec]

    @patch("app.engine.collect_candles", return_value=5)
    @patch("app.engine.get_latest_candles")
    @patch("app.engine.get_open_positions")
    def test_run_once_sell_signal_checks_open_position(
        self,
        mock_get_open_positions,
        mock_get_candles,
        mock_collect_candles,
    ):
        mock_get_candles.return_value = [_make_candle()]
        open_pos = TradePosition(
            id=12,
            symbol="BTCUSDT",
            entry_price=Decimal("60000"),
            quantity=Decimal("0.00036"),
            status="open",
        )
        mock_get_open_positions.return_value = [open_pos]

        sell_signal = StrategySignal(
            symbol="BTCUSDT",
            side="SELL",
            reason="RSI Overbought fall",
            generated_at=datetime.now(timezone.utc),
        )
        engine, _, exit_manager, router = _make_engine(
            signals_by_strat={"rsi_14": [sell_signal]},
        )

        decision = ExitDecision(
            position_id=12,
            symbol="BTCUSDT",
            should_close=True,
            reason="signal_sell: RSI Overbought fall",
            current_price=Decimal("62000"),
            entry_price=Decimal("60000"),
            pnl_pct=Decimal("3.33"),
        )
        exit_res = ExitResult(
            position_id=12,
            symbol="BTCUSDT",
            decision=decision,
            closed=True,
            exit_price=Decimal("62000"),
            realized_pnl_eur=Decimal("0.60"),
        )
        exit_manager.check_signal_exit.return_value = exit_res

        result = engine.run_once()

        exit_manager.check_signal_exit.assert_called_once_with(open_pos, sell_signal)
        assert exit_res in result.exit_results
        # No BUY signal to route
        router.route_all.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: run_loop
# ---------------------------------------------------------------------------

class TestTradingEngineRunLoop:
    @patch("app.engine.collect_candles", return_value=0)
    @patch("app.engine.get_latest_candles", return_value=[])
    @patch("app.engine.get_open_positions", return_value=[])
    def test_run_loop_terminates_at_max_iterations(
        self,
        mock_get_open_positions,
        mock_get_candles,
        mock_collect_candles,
    ):
        engine, _, _, _ = _make_engine()

        # Execute 2 iterations with poll=0
        engine.run_loop(poll_interval_seconds=0, max_iterations=2)

        assert mock_collect_candles.call_count == 2


# ---------------------------------------------------------------------------
# Tests: build_default_engine
# ---------------------------------------------------------------------------

class TestBuildDefaultEngine:
    def test_instantiates_successfully(self):
        engine = build_default_engine(
            symbols=["BTCUSDT", "ETHUSDT"],
            interval="5m",
            allocation_eur=Decimal("20"),
        )
        assert isinstance(engine, TradingEngine)
        assert engine.symbols == ["BTCUSDT", "ETHUSDT"]
        assert engine.interval == "5m"
        assert len(engine.coordinator.strategies) == 4

