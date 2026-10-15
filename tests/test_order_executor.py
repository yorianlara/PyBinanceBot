"""Tests para OrderExecutor — sin conexión a Binance ni PostgreSQL."""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.execution.order_executor import OrderExecutionError, OrderExecutor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_executor(
    *,
    place_order_response=None,
    place_order_error=None,
    order_status_response=None,
    trades_response=None,
    price_response=None,
):
    """Crea un OrderExecutor con cliente y riesgo mockeados."""
    client = MagicMock()
    risk = MagicMock()

    if price_response is not None:
        client.get_price.return_value = price_response

    if place_order_error:
        client.place_order.side_effect = place_order_error
    elif place_order_response is not None:
        client.place_order.return_value = place_order_response

    if order_status_response is not None:
        client.get_order.return_value = order_status_response

    if trades_response is not None:
        client.get_my_trades.return_value = trades_response

    return OrderExecutor(client=client, risk=risk), client, risk


def _binance_filled_response(
    order_id="12345",
    symbol="BTCUSDT",
    quantity="0.00036",
):
    """Respuesta simulada de Binance para una orden FILLED."""
    return {
        "orderId": order_id,
        "symbol": symbol,
        "status": "FILLED",
        "executedQty": quantity,
        "side": "BUY",
        "type": "MARKET",
    }


def _binance_trade(
    order_id="12345",
    trade_id="99001",
    symbol="BTCUSDT",
    quantity="0.00036",
    price="60000.00",
    commission="0.00000036",
    time_ms=1700000000000,
):
    return {
        "orderId": order_id,
        "id": trade_id,
        "symbol": symbol,
        "qty": quantity,
        "price": price,
        "commission": commission,
        "commissionAsset": "BTC",
        "time": time_ms,
    }


# ---------------------------------------------------------------------------
# Tests: get_current_price
# ---------------------------------------------------------------------------

class TestGetCurrentPrice:
    def test_returns_valid_price(self):
        executor, _, _ = _make_executor(
            price_response={"price": "60123.45"},
        )
        assert executor.get_current_price("BTCUSDT") == Decimal("60123.45")

    def test_rejects_zero_price(self):
        executor, _, _ = _make_executor(
            price_response={"price": "0"},
        )
        with pytest.raises(OrderExecutionError, match="inválido"):
            executor.get_current_price("BTCUSDT")

    def test_rejects_negative_price(self):
        executor, _, _ = _make_executor(
            price_response={"price": "-100"},
        )
        with pytest.raises(OrderExecutionError, match="inválido"):
            executor.get_current_price("BTCUSDT")


# ---------------------------------------------------------------------------
# Tests: execute_market_buy — flujo exitoso
# ---------------------------------------------------------------------------

class TestExecuteMarketBuySuccess:
    def test_full_flow_returns_position_id(self):
        """Flujo completo: envío → submitted → reconcile → posición."""
        executor, client, risk = _make_executor(
            place_order_response={"orderId": "12345"},
            order_status_response=_binance_filled_response(),
            trades_response=[_binance_trade()],
        )
        risk.convert_to_position.return_value = 42

        position_id = executor.execute_market_buy(
            order_id=1,
            symbol="BTCUSDT",
            quantity=Decimal("0.00036"),
        )

        assert position_id == 42

    def test_calls_mark_submitted(self):
        executor, _, risk = _make_executor(
            place_order_response={"orderId": "12345"},
            order_status_response=_binance_filled_response(),
            trades_response=[_binance_trade()],
        )
        risk.convert_to_position.return_value = 1

        executor.execute_market_buy(1, "BTCUSDT", Decimal("0.00036"))

        risk.mark_submitted.assert_called_once_with(1, "12345")

    def test_calls_reconcile_fills(self):
        executor, _, risk = _make_executor(
            place_order_response={"orderId": "12345"},
            order_status_response=_binance_filled_response(),
            trades_response=[_binance_trade()],
        )
        risk.convert_to_position.return_value = 1

        executor.execute_market_buy(1, "BTCUSDT", Decimal("0.00036"))

        risk.reconcile_fills.assert_called_once_with(
            1,
            _binance_filled_response(),
            [_binance_trade()],
        )

    def test_calls_convert_to_position(self):
        executor, _, risk = _make_executor(
            place_order_response={"orderId": "12345"},
            order_status_response=_binance_filled_response(),
            trades_response=[_binance_trade()],
        )
        risk.convert_to_position.return_value = 7

        executor.execute_market_buy(1, "BTCUSDT", Decimal("0.00036"))

        risk.convert_to_position.assert_called_once_with(
            1,
            quantity=Decimal("0.00036"),
            entry_price=Decimal("0"),
        )

    def test_forwards_client_order_id(self):
        executor, client, risk = _make_executor(
            place_order_response={"orderId": "12345"},
            order_status_response=_binance_filled_response(),
            trades_response=[_binance_trade()],
        )
        risk.convert_to_position.return_value = 1

        executor.execute_market_buy(
            1, "BTCUSDT", Decimal("0.00036"),
            client_order_id="my-custom-id",
        )

        client.place_order.assert_called_once_with(
            symbol="BTCUSDT",
            side="BUY",
            order_type="MARKET",
            quantity="0.00036",
            client_order_id="my-custom-id",
        )


# ---------------------------------------------------------------------------
# Tests: execute_market_buy — errores
# ---------------------------------------------------------------------------

class TestExecuteMarketBuyErrors:
    def test_binance_rejection_releases_order(self):
        """Si Binance rechaza, se libera la reserva de riesgo."""
        executor, _, risk = _make_executor(
            place_order_error=RuntimeError("Insufficient balance"),
        )

        with pytest.raises(OrderExecutionError, match="rechazó"):
            executor.execute_market_buy(
                1, "BTCUSDT", Decimal("0.00036"),
            )

        risk.release_order.assert_called_once_with(
            1, status="rejected",
        )

    def test_binance_rejection_does_not_call_submit(self):
        executor, _, risk = _make_executor(
            place_order_error=RuntimeError("Rejected"),
        )

        with pytest.raises(OrderExecutionError):
            executor.execute_market_buy(
                1, "BTCUSDT", Decimal("0.00036"),
            )

        risk.mark_submitted.assert_not_called()

    def test_not_filled_raises(self):
        """Si la orden no se completa, lanza error."""
        partial = _binance_filled_response()
        partial["status"] = "PARTIALLY_FILLED"

        executor, _, risk = _make_executor(
            place_order_response={"orderId": "12345"},
            order_status_response=partial,
            trades_response=[_binance_trade()],
        )

        with pytest.raises(
            OrderExecutionError,
            match="no se ejecutó completamente",
        ):
            executor.execute_market_buy(
                1, "BTCUSDT", Decimal("0.00036"),
            )

        risk.convert_to_position.assert_not_called()

