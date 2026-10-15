"""Tests para SignalRouter — sin conexión a Binance ni PostgreSQL."""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.execution.order_executor import OrderExecutionError
from app.execution.signal_router import ExecutionResult, SignalRouter
from app.risk.persistent_manager import RiskRejected
from app.strategies.base import StrategySignal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _buy_signal(symbol="BTCUSDT"):
    return StrategySignal(
        symbol=symbol,
        side="BUY",
        reason="Cruce EMA prueba",
        generated_at=datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc),
    )


def _sell_signal(symbol="BTCUSDT"):
    return StrategySignal(
        symbol=symbol,
        side="SELL",
        reason="RSI sobrecomprado",
        generated_at=datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc),
    )


def _make_router(
    *,
    current_price=Decimal("60000"),
    reserve_return=1,
    reserve_error=None,
    execute_return=42,
    execute_error=None,
    price_error=None,
    allocation_eur=Decimal("20"),
):
    risk = MagicMock()
    executor = MagicMock()

    if price_error:
        executor.get_current_price.side_effect = price_error
    else:
        executor.get_current_price.return_value = current_price

    if reserve_error:
        risk.reserve_order.side_effect = reserve_error
    else:
        risk.reserve_order.return_value = reserve_return

    if execute_error:
        executor.execute_market_buy.side_effect = execute_error
    else:
        executor.execute_market_buy.return_value = execute_return

    router = SignalRouter(
        risk=risk,
        executor=executor,
        allocation_eur=allocation_eur,
    )
    return router, risk, executor


# ---------------------------------------------------------------------------
# Tests: constructor
# ---------------------------------------------------------------------------

class TestSignalRouterInit:
    def test_rejects_zero_allocation(self):
        with pytest.raises(ValueError, match="positiva"):
            SignalRouter(
                risk=MagicMock(),
                executor=MagicMock(),
                allocation_eur=Decimal("0"),
            )

    def test_rejects_negative_allocation(self):
        with pytest.raises(ValueError, match="positiva"):
            SignalRouter(
                risk=MagicMock(),
                executor=MagicMock(),
                allocation_eur=Decimal("-10"),
            )

    def test_rejects_invalid_exchange_rate(self):
        with pytest.raises(ValueError, match="cambio"):
            SignalRouter(
                risk=MagicMock(),
                executor=MagicMock(),
                eur_to_usdt=Decimal("0"),
            )


# ---------------------------------------------------------------------------
# Tests: calculate_quantity
# ---------------------------------------------------------------------------

class TestCalculateQuantity:
    def test_basic_calculation(self):
        router, _, _ = _make_router()
        # 20 EUR * 1.08 = 21.60 USDT / 60000 = 0.00036
        quantity = router._calculate_quantity(Decimal("60000"))
        assert quantity == Decimal("0.00036")

    def test_rounds_down(self):
        router, _, _ = _make_router()
        # 20 * 1.08 / 50000 = 0.000432 → 0.00043 (rounded down)
        quantity = router._calculate_quantity(Decimal("50000"))
        assert quantity == Decimal("0.00043")

    def test_high_price_gives_small_quantity(self):
        router, _, _ = _make_router()
        quantity = router._calculate_quantity(Decimal("100000"))
        # 21.60 / 100000 = 0.000216 → 0.00021
        assert quantity == Decimal("0.00021")

    def test_custom_precision(self):
        router, _, _ = _make_router()
        router.quantity_precision = 8
        quantity = router._calculate_quantity(Decimal("60000"))
        # 21.60 / 60000 = 0.00036 with 8 decimals
        assert quantity == Decimal("0.00036000")


# ---------------------------------------------------------------------------
# Tests: route_signal — SELL ignorada
# ---------------------------------------------------------------------------

class TestRouteSignalSell:
    def test_sell_signal_is_skipped(self):
        router, _, _ = _make_router()
        result = router.route_signal("rsi_14", _sell_signal())

        assert result.status == "skipped"
        assert result.order_id is None
        assert result.position_id is None
        assert "SELL" in result.reason

    def test_sell_signal_does_not_call_risk(self):
        router, risk, _ = _make_router()
        router.route_signal("rsi_14", _sell_signal())

        risk.reserve_order.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: route_signal — error de precio
# ---------------------------------------------------------------------------

class TestRouteSignalPriceError:
    def test_price_error_returns_error_result(self):
        router, _, _ = _make_router(
            price_error=ConnectionError("Red caída"),
        )
        result = router.route_signal("ema_9_21", _buy_signal())

        assert result.status == "error"
        assert "precio" in result.reason.lower()

    def test_price_error_does_not_call_risk(self):
        router, risk, _ = _make_router(
            price_error=ConnectionError("Red caída"),
        )
        router.route_signal("ema_9_21", _buy_signal())

        risk.reserve_order.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: route_signal — riesgo rechaza
# ---------------------------------------------------------------------------

class TestRouteSignalRiskRejected:
    def test_risk_rejection_returns_rejected(self):
        router, _, _ = _make_router(
            reserve_error=RiskRejected("Límite de pérdida diaria"),
        )
        result = router.route_signal("ema_9_21", _buy_signal())

        assert result.status == "rejected"
        assert "pérdida" in result.reason.lower()

    def test_risk_rejection_does_not_call_executor(self):
        router, _, executor = _make_router(
            reserve_error=RiskRejected("Capital agotado"),
        )
        router.route_signal("ema_9_21", _buy_signal())

        executor.execute_market_buy.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: route_signal — flujo exitoso
# ---------------------------------------------------------------------------

class TestRouteSignalSuccess:
    def test_successful_buy_returns_executed(self):
        router, _, _ = _make_router(
            reserve_return=10,
            execute_return=42,
        )
        result = router.route_signal("ema_9_21", _buy_signal())

        assert result.status == "executed"
        assert result.order_id == 10
        assert result.position_id == 42
        assert "0.00036" in result.reason

    def test_calls_risk_with_correct_params(self):
        router, risk, _ = _make_router()
        router.route_signal("ema_9_21", _buy_signal())

        risk.reserve_order.assert_called_once_with(
            strategy_key="ema_9_21",
            symbol="BTCUSDT",
            side="BUY",
            order_type="MARKET",
            quantity=Decimal("0.00036"),
            allocation_eur=Decimal("20"),
        )

    def test_calls_executor_with_correct_params(self):
        router, _, executor = _make_router(reserve_return=7)
        router.route_signal("ema_9_21", _buy_signal())

        executor.execute_market_buy.assert_called_once_with(
            7, "BTCUSDT", Decimal("0.00036"),
        )


# ---------------------------------------------------------------------------
# Tests: route_signal — error de ejecución
# ---------------------------------------------------------------------------

class TestRouteSignalExecutionError:
    def test_execution_error_returns_error_with_order_id(self):
        router, _, _ = _make_router(
            reserve_return=5,
            execute_error=OrderExecutionError("Binance rechazó"),
        )
        result = router.route_signal("ema_9_21", _buy_signal())

        assert result.status == "error"
        assert result.order_id == 5
        assert result.position_id is None
        assert "rechazó" in result.reason.lower()

    def test_risk_rejected_during_execution(self):
        router, _, _ = _make_router(
            reserve_return=5,
            execute_error=RiskRejected("Reconciliación fallida"),
        )
        result = router.route_signal("ema_9_21", _buy_signal())

        assert result.status == "error"
        assert result.order_id == 5


# ---------------------------------------------------------------------------
# Tests: route_all
# ---------------------------------------------------------------------------

class TestRouteAll:
    def test_routes_all_signals(self):
        router, _, _ = _make_router(
            reserve_return=1,
            execute_return=10,
        )

        signals = {
            "ema_9_21": [_buy_signal()],
            "rsi_14": [_sell_signal()],
        }

        results = router.route_all(signals)

        assert len(results) == 2
        executed = [r for r in results if r.status == "executed"]
        skipped = [r for r in results if r.status == "skipped"]
        assert len(executed) == 1
        assert len(skipped) == 1

    def test_empty_signals_returns_empty(self):
        router, _, _ = _make_router()
        results = router.route_all({})
        assert results == []

    def test_multiple_buy_signals_same_strategy(self):
        router, risk, executor = _make_router(
            reserve_return=1,
            execute_return=10,
        )

        signals = {
            "ema_9_21": [_buy_signal(), _buy_signal("ETHUSDT")],
        }

        results = router.route_all(signals)
        assert len(results) == 2
        assert all(r.status == "executed" for r in results)

