"""Tests para ExitManager — evaluación y cierre de posiciones abiertas."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.execution.exit_config import ExitConfig
from app.execution.exit_manager import ExitDecision, ExitManager, ExitResult
from app.execution.order_executor import OrderExecutionError
from app.risk.persistent_manager import RiskRejected
from app.storage.models import TradePosition
from app.strategies.base import StrategySignal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_position(
    *,
    id: int = 1,
    symbol: str = "BTCUSDT",
    entry_price: Decimal = Decimal("60000"),
    quantity: Decimal = Decimal("0.00036"),
    opened_at: datetime | None = None,
    status: str = "open",
) -> TradePosition:
    pos = TradePosition(
        id=id,
        symbol=symbol,
        entry_price=entry_price,
        quantity=quantity,
        status=status,
    )
    pos.opened_at = opened_at or datetime.now(timezone.utc)
    return pos


def _make_exit_manager(
    *,
    config: ExitConfig | None = None,
    current_price: Decimal = Decimal("60000"),
    sell_exit_price: Decimal = Decimal("60000"),
    sell_error: Exception | None = None,
    risk_error: Exception | None = None,
    price_error: Exception | None = None,
    eur_to_usdt: Decimal = Decimal("1.08"),
):
    risk = MagicMock()
    executor = MagicMock()

    if price_error:
        executor.get_current_price.side_effect = price_error
    else:
        executor.get_current_price.return_value = current_price

    if sell_error:
        executor.execute_market_sell.side_effect = sell_error
    else:
        executor.execute_market_sell.return_value = sell_exit_price

    if risk_error:
        risk.close_position.side_effect = risk_error

    manager = ExitManager(
        risk=risk,
        executor=executor,
        config=config or ExitConfig(stop_loss_pct=Decimal("3"), take_profit_pct=Decimal("5"), max_hours=48),
        eur_to_usdt=eur_to_usdt,
    )
    return manager, risk, executor


# ---------------------------------------------------------------------------
# Tests: Initialization & Validation
# ---------------------------------------------------------------------------

class TestExitManagerInit:
    def test_rejects_invalid_eur_to_usdt(self):
        with pytest.raises(ValueError, match="positivo"):
            ExitManager(
                risk=MagicMock(),
                executor=MagicMock(),
                eur_to_usdt=Decimal("0"),
            )


# ---------------------------------------------------------------------------
# Tests: evaluate_position
# ---------------------------------------------------------------------------

class TestEvaluatePosition:
    def test_hold_when_within_thresholds(self):
        manager, _, _ = _make_exit_manager()
        pos = _make_position(entry_price=Decimal("60000"))

        # Precio 60600 = +1.0%
        decision = manager.evaluate_position(pos, current_price=Decimal("60600"))

        assert not decision.should_close
        assert decision.reason == "hold"
        assert decision.pnl_pct == Decimal("1.0")

    def test_stop_loss_triggered(self):
        manager, _, _ = _make_exit_manager()
        pos = _make_position(entry_price=Decimal("60000"))

        # Precio 58100 = -3.166...% (menor o igual a -3%)
        decision = manager.evaluate_position(pos, current_price=Decimal("58100"))

        assert decision.should_close
        assert decision.reason == "stop_loss"
        assert decision.pnl_pct < Decimal("-3.0")

    def test_stop_loss_exact_boundary(self):
        manager, _, _ = _make_exit_manager()
        pos = _make_position(entry_price=Decimal("100"))

        # Exactamente -3%
        decision = manager.evaluate_position(pos, current_price=Decimal("97"))

        assert decision.should_close
        assert decision.reason == "stop_loss"
        assert decision.pnl_pct == Decimal("-3.0")

    def test_take_profit_triggered(self):
        manager, _, _ = _make_exit_manager()
        pos = _make_position(entry_price=Decimal("60000"))

        # Precio 63100 = +5.166...% (mayor o igual a +5%)
        decision = manager.evaluate_position(pos, current_price=Decimal("63100"))

        assert decision.should_close
        assert decision.reason == "take_profit"
        assert decision.pnl_pct > Decimal("5.0")

    def test_take_profit_exact_boundary(self):
        manager, _, _ = _make_exit_manager()
        pos = _make_position(entry_price=Decimal("100"))

        # Exactamente +5%
        decision = manager.evaluate_position(pos, current_price=Decimal("105"))

        assert decision.should_close
        assert decision.reason == "take_profit"
        assert decision.pnl_pct == Decimal("5.0")

    def test_timeout_triggered(self):
        manager, _, _ = _make_exit_manager()
        now = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)
        # Abierta hace 49 horas con límite de 48 horas
        pos = _make_position(
            entry_price=Decimal("60000"),
            opened_at=now - timedelta(hours=49),
        )

        decision = manager.evaluate_position(
            pos,
            current_price=Decimal("60100"),
            current_time=now,
        )

        assert decision.should_close
        assert decision.reason == "timeout"

    def test_rejects_non_positive_entry_price(self):
        manager, _, _ = _make_exit_manager()
        pos = _make_position(entry_price=Decimal("0"))

        with pytest.raises(ValueError, match="inválido"):
            manager.evaluate_position(pos, current_price=Decimal("60000"))


# ---------------------------------------------------------------------------
# Tests: close_position
# ---------------------------------------------------------------------------

class TestClosePosition:
    def test_successful_close_calculates_pnl_eur(self):
        # Entry: 60000, Exit: 63000, Qty: 0.00036
        # PnL USDT = (63000 - 60000) * 0.00036 = 3000 * 0.00036 = 1.08 USDT
        # PnL EUR = 1.08 / 1.08 = 1.00 EUR
        manager, risk, executor = _make_exit_manager(
            sell_exit_price=Decimal("63000"),
            eur_to_usdt=Decimal("1.08"),
        )
        pos = _make_position(
            id=10,
            symbol="BTCUSDT",
            entry_price=Decimal("60000"),
            quantity=Decimal("0.00036"),
        )
        decision = ExitDecision(
            position_id=10,
            symbol="BTCUSDT",
            should_close=True,
            reason="take_profit",
            current_price=Decimal("63000"),
            entry_price=Decimal("60000"),
            pnl_pct=Decimal("5.0"),
        )

        result = manager.close_position(pos, decision)

        assert result.closed
        assert result.exit_price == Decimal("63000")
        assert result.realized_pnl_eur == Decimal("1.00")
        executor.execute_market_sell.assert_called_once_with(
            symbol="BTCUSDT",
            quantity=Decimal("0.00036"),
        )
        risk.close_position.assert_called_once_with(
            10,
            exit_price=Decimal("63000"),
            realized_pnl_eur=Decimal("1.00"),
        )

    def test_executor_error_leaves_position_unclosed(self):
        manager, risk, _ = _make_exit_manager(
            sell_error=OrderExecutionError("Binance rechazó venta"),
        )
        pos = _make_position(id=5)
        decision = ExitDecision(
            position_id=5,
            symbol="BTCUSDT",
            should_close=True,
            reason="stop_loss",
            current_price=Decimal("57000"),
            entry_price=Decimal("60000"),
            pnl_pct=Decimal("-5.0"),
        )

        result = manager.close_position(pos, decision)

        assert not result.closed
        assert "Binance" in result.error
        risk.close_position.assert_not_called()

    def test_risk_error_recorded(self):
        manager, _, _ = _make_exit_manager(
            sell_exit_price=Decimal("58000"),
            risk_error=RiskRejected("No se puede cerrar"),
        )
        pos = _make_position(id=7)
        decision = ExitDecision(
            position_id=7,
            symbol="BTCUSDT",
            should_close=True,
            reason="stop_loss",
            current_price=Decimal("58000"),
            entry_price=Decimal("60000"),
            pnl_pct=Decimal("-3.33"),
        )

        result = manager.close_position(pos, decision)

        assert not result.closed
        assert "riesgo" in result.error.lower()


# ---------------------------------------------------------------------------
# Tests: check_and_exit_positions
# ---------------------------------------------------------------------------

class TestCheckAndExitPositions:
    def test_evaluates_multiple_positions(self):
        manager, risk, executor = _make_exit_manager(
            current_price=Decimal("57000"),  # -5% vs 60000 -> Stop Loss
            sell_exit_price=Decimal("57000"),
        )
        pos1 = _make_position(id=1, entry_price=Decimal("60000"))
        pos2 = _make_position(id=2, entry_price=Decimal("55000"))  # +3.63% -> Hold

        results = manager.check_and_exit_positions(positions=[pos1, pos2])

        assert len(results) == 2
        assert results[0].closed  # pos1 closed by stop_loss
        assert results[0].decision.reason == "stop_loss"
        assert not results[1].closed  # pos2 held
        assert results[1].decision.reason == "hold"

    def test_price_fetch_error_handled_gracefully(self):
        manager, _, _ = _make_exit_manager(
            price_error=ConnectionError("Fallo de red"),
        )
        pos = _make_position(id=1)

        results = manager.check_and_exit_positions(positions=[pos])

        assert len(results) == 1
        assert not results[0].closed
        assert "precio" in results[0].error.lower()


# ---------------------------------------------------------------------------
# Tests: check_signal_exit
# ---------------------------------------------------------------------------

class TestCheckSignalExit:
    def test_signal_sell_triggers_exit(self):
        manager, risk, executor = _make_exit_manager(
            sell_exit_price=Decimal("61000"),
        )
        pos = _make_position(id=3, symbol="BTCUSDT", entry_price=Decimal("60000"))
        signal = StrategySignal(
            symbol="BTCUSDT",
            side="SELL",
            reason="Cruce bajista EMA",
            generated_at=datetime.now(timezone.utc),
        )

        result = manager.check_signal_exit(pos, signal)

        assert result is not None
        assert result.closed
        assert "signal_sell" in result.decision.reason
        risk.close_position.assert_called_once()

    def test_signal_buy_ignored(self):
        manager, _, _ = _make_exit_manager()
        pos = _make_position(id=3, symbol="BTCUSDT")
        signal = StrategySignal(
            symbol="BTCUSDT",
            side="BUY",
            reason="Cruce alcista EMA",
            generated_at=datetime.now(timezone.utc),
        )

        result = manager.check_signal_exit(pos, signal)
        assert result is None

    def test_signal_different_symbol_ignored(self):
        manager, _, _ = _make_exit_manager()
        pos = _make_position(id=3, symbol="BTCUSDT")
        signal = StrategySignal(
            symbol="ETHUSDT",
            side="SELL",
            reason="RSI sobrecompra",
            generated_at=datetime.now(timezone.utc),
        )

        result = manager.check_signal_exit(pos, signal)
        assert result is None

