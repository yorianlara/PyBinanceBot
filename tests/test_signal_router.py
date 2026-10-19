from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.execution.signal_router import (
    BINANCE_SPOT_TESTNET_URL,
    SignalRouter,
)
from app.risk.persistent_manager import RiskRejected
from app.strategies.base import StrategySignal


class FakeClient:
    base_url = BINANCE_SPOT_TESTNET_URL

    def __init__(self, status="FILLED"):
        self.status = status
        self.calls = []

    def get_symbol_info(self, symbol):
        self.calls.append(("symbol_info", symbol))
        return {
            "symbol": symbol,
            "status": "TRADING",
            "baseAsset": "BTC",
            "quoteAsset": "USDT",
            "quoteOrderQtyMarketAllowed": True,
            "filters": [
                {
                    "filterType": "MARKET_LOT_SIZE",
                    "minQty": "0.00001",
                    "maxQty": "1000",
                    "stepSize": "0.00001",
                },
                {
                    "filterType": "MIN_NOTIONAL",
                    "minNotional": "5",
                    "applyToMarket": True,
                },
            ],
        }

    def get_price(self, symbol):
        self.calls.append(("price", symbol))
        prices = {"BTCUSDT": "50000", "EURUSDT": "1.10"}
        return {"symbol": symbol, "price": prices[symbol]}

    def create_order(self, **kwargs):
        self.calls.append(("create_order", kwargs))
        return {"orderId": 12345}

    def cancel_order(self, symbol, order_id):
        self.calls.append(("cancel_order", symbol, order_id))
        self.status = "CANCELED"
        return {"orderId": order_id, "status": self.status}

    def get_order(self, symbol, order_id):
        self.calls.append(("get_order", symbol, order_id))
        executed_quantity = {
            "FILLED": "0.00039",
            "PARTIALLY_FILLED": "0.0002",
            "NEW": "0",
            "CANCELED": "0.0002",
            "EXPIRED": "0",
            "REJECTED": "0",
        }[self.status]
        return {
            "orderId": order_id,
            "symbol": symbol,
            "status": self.status,
            "executedQty": executed_quantity,
        }

    def get_my_trades(self, symbol, order_id):
        self.calls.append(("get_my_trades", symbol, order_id))
        quantities = {
            "FILLED": ["0.00039"],
            "PARTIALLY_FILLED": ["0.0002"],
            "NEW": [],
            "CANCELED": ["0.0002"],
            "EXPIRED": [],
            "REJECTED": [],
        }[self.status]
        return [
            {
                "id": index,
                "orderId": order_id,
                "symbol": symbol,
                "qty": quantity,
                "price": "50000",
                "commission": "0",
                "commissionAsset": "BNB",
                "time": 1760000000000,
            }
            for index, quantity in enumerate(quantities, start=1)
        ]


class FakeRiskManager:
    def __init__(self, reject=False):
        self.reject = reject
        self.calls = []

    def reserve_order(self, **kwargs):
        self.calls.append(("reserve_order", kwargs))
        if self.reject:
            raise RiskRejected("Límite de riesgo alcanzado.")
        return 17

    def mark_submitted(self, order_id, exchange_order_id):
        self.calls.append(("mark_submitted", order_id, exchange_order_id))

    def reconcile_fills(self, order_id, exchange_order, exchange_trades):
        self.calls.append(
            ("reconcile_fills", order_id, exchange_order, exchange_trades)
        )

    def convert_to_position(
        self,
        order_id,
        *,
        quantity,
        entry_price,
        base_asset=None,
        quote_asset=None,
    ):
        self.calls.append(
            (
                "convert_to_position",
                order_id,
                quantity,
                entry_price,
                base_asset,
                quote_asset,
            )
        )
        return 29


def make_signal(side="BUY"):
    return StrategySignal(
        symbol="BTCUSDT",
        side=side,
        reason="prueba",
        generated_at=datetime.now(UTC),
    )


def test_routes_buy_with_quote_budget_and_converts_fills_to_position():
    client = FakeClient()
    risk_manager = FakeRiskManager()
    router = SignalRouter(client, risk_manager)

    results = router.route_signals(
        {"ema": [make_signal()]},
        bot_run_id=123,
    )

    assert len(results) == 1
    assert results[0].status == "opened"
    assert results[0].order_id == 17
    assert results[0].position_id == 29

    reservation = risk_manager.calls[0][1]
    assert reservation["allocation_eur"] == Decimal("18.19")
    assert reservation["quote_order_qty"] == Decimal(20)
    assert reservation["quantity"] == Decimal("0.00040")
    assert reservation["side"] == "buy"
    assert reservation["order_type"] == "MARKET"
    assert reservation["bot_run_id"] == 123

    order_call = next(call for call in client.calls if call[0] == "create_order")
    assert order_call[1]["quote_order_qty"] == Decimal(20)
    assert "quantity" not in order_call[1]
    assert order_call[1]["side"] == "BUY"

    assert any(call[0] == "reconcile_fills" for call in risk_manager.calls)
    assert any(
        call[0] == "convert_to_position" and call[2] == Decimal("0.00040")
        for call in risk_manager.calls
    )


def test_sell_signal_is_routed_to_exit_manager():
    client = FakeClient()
    risk_manager = FakeRiskManager()
    exit_results = []

    class FakeExitManager:
        def close_for_signal(self, **kwargs):
            exit_results.append(kwargs)
            from app.execution.models import ExecutionResult

            return ExecutionResult(
                strategy_key=kwargs["strategy_key"],
                symbol=kwargs["symbol"],
                status="exited",
                reason="posición cerrada",
            )

    router = SignalRouter(client, risk_manager, exit_manager=FakeExitManager())

    result = router.route_signals({"ema": [make_signal("SELL")]})[0]

    assert result.status == "exited"
    assert result.reason == "posición cerrada"
    assert exit_results == [
        {"strategy_key": "ema", "symbol": "BTCUSDT", "bot_run_id": None}
    ]


def test_conflicting_signals_for_symbol_do_not_submit_orders():
    client = FakeClient()
    risk_manager = FakeRiskManager()
    exit_calls = []

    class FakeExitManager:
        def close_for_signal(self, **kwargs):
            exit_calls.append(kwargs)
            raise AssertionError("Conflicting signal must not close position")

    router = SignalRouter(
        client,
        risk_manager,
        exit_manager=FakeExitManager(),
    )

    results = router.route_signals(
        {
            "ema": [make_signal("BUY")],
            "rsi": [make_signal("SELL")],
        }
    )

    assert len(results) == 2
    assert all(result.status == "rejected" for result in results)
    assert all("simultáneas" in result.reason for result in results)
    assert client.calls == []
    assert risk_manager.calls == []
    assert exit_calls == []
    assert client.calls == []
    assert risk_manager.calls == []


def test_partial_fill_is_recorded_as_a_position():
    client = FakeClient(status="PARTIALLY_FILLED")
    risk_manager = FakeRiskManager()
    router = SignalRouter(client, risk_manager)

    result = router.route_signals({"ema": [make_signal()]})[0]

    assert result.status == "partially_filled"
    assert result.position_id == 29
    assert any(call[0] == "convert_to_position" for call in risk_manager.calls)
    assert any(call[0] == "cancel_order" for call in client.calls)


def test_canceled_order_with_fills_is_recorded_as_a_position():
    client = FakeClient(status="CANCELED")
    risk_manager = FakeRiskManager()
    router = SignalRouter(client, risk_manager)

    result = router.route_signals({"ema": [make_signal()]})[0]

    assert result.status == "partially_filled"
    assert result.position_id == 29


def test_canceled_order_without_fills_is_rejected():
    client = FakeClient(status="EXPIRED")
    risk_manager = FakeRiskManager()
    router = SignalRouter(client, risk_manager)

    result = router.route_signals({"ema": [make_signal()]})[0]

    assert result.status == "rejected"
    assert result.position_id is None


def test_risk_rejection_does_not_submit_order():
    client = FakeClient()
    risk_manager = FakeRiskManager(reject=True)
    router = SignalRouter(client, risk_manager)

    result = router.route_signals({"ema": [make_signal()]})[0]

    assert result.status == "rejected"
    assert result.reason == "Límite de riesgo alcanzado."
    assert not any(call[0] == "create_order" for call in client.calls)


def test_rejects_non_testnet_client():
    client = FakeClient()
    client.base_url = "https://api.binance.com"

    with pytest.raises(ValueError, match="Spot Testnet"):
        SignalRouter(client, FakeRiskManager())


def test_rejects_budget_below_binance_market_minimum():
    router = SignalRouter(
        FakeClient(),
        FakeRiskManager(),
        quote_budget_usdt=Decimal(4),
    )

    with pytest.raises(ValueError, match="mínimo"):
        router.route_signals({"ema": [make_signal()]})

    assert not any(call[0] == "create_order" for call in router.client.calls)


def test_pending_market_order_is_reported_without_position():
    client = FakeClient(status="NEW")
    risk_manager = FakeRiskManager()
    router = SignalRouter(client, risk_manager)

    result = router.route_signals({"ema": [make_signal()]})[0]

    assert result.status == "pending"
    assert result.order_id == 17
    assert result.position_id is None
    assert not any(call[0] == "convert_to_position" for call in risk_manager.calls)
