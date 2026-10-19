from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

from app.execution.exit_manager import PositionExitManager
from app.execution.models import ExecutionResult
from app.execution.signal_router import BINANCE_SPOT_TESTNET_URL, SignalRouter
from app.strategies.base import StrategySignal


class FakeExitClient:
    base_url = BINANCE_SPOT_TESTNET_URL

    def __init__(self, status="FILLED", free_balance="0.00040"):
        self.status = status
        self.free_balance = free_balance
        self.calls = []

    def get_symbol_info(self, symbol):
        self.calls.append(("symbol_info", symbol))
        return {
            "status": "TRADING",
            "baseAsset": "BTC",
            "quoteAsset": "USDT",
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
        return {
            "symbol": symbol,
            "price": "52000" if symbol == "BTCUSDT" else "1.10",
        }

    def get_balances(self):
        self.calls.append(("balances",))
        return [{"asset": "BTC", "free": self.free_balance, "locked": "0"}]

    def create_order(self, **kwargs):
        self.calls.append(("create_order", kwargs))
        return {"orderId": 23456}

    def cancel_order(self, symbol, order_id):
        self.calls.append(("cancel_order", symbol, order_id))
        self.status = "CANCELED"
        return {"orderId": order_id, "status": self.status}

    def get_order(self, symbol, order_id):
        self.calls.append(("get_order", symbol, order_id))
        executed_quantity = {
            "FILLED": "0.00040",
            "PARTIALLY_FILLED": "0.00020",
            "NEW": "0",
            "CANCELED": "0.00020",
            "EXPIRED": "0",
        }[self.status]
        return {
            "orderId": order_id,
            "symbol": symbol,
            "status": self.status,
            "executedQty": executed_quantity,
        }

    def get_my_trades(self, symbol, order_id):
        self.calls.append(("get_my_trades", symbol, order_id))
        qty = {
            "FILLED": ["0.00040"],
            "PARTIALLY_FILLED": ["0.00020"],
            "NEW": [],
            "CANCELED": ["0.00020"],
            "EXPIRED": [],
        }[self.status]
        return [
            {
                "id": index,
                "orderId": order_id,
                "symbol": symbol,
                "qty": amount,
                "price": "52000",
                "commission": "0",
                "commissionAsset": "USDT",
                "time": 1760000000000,
            }
            for index, amount in enumerate(qty, start=1)
        ]


class FakeExitRiskManager:
    def __init__(self, fully_closed=True, external_commission_assets=None):
        self.fully_closed = fully_closed
        self.external_commission_assets = external_commission_assets or []
        self.calls = []

    def reserve_exit_order(self, **kwargs):
        self.calls.append(("reserve_exit_order", kwargs))
        return 31

    def mark_submitted(self, order_id, exchange_order_id):
        self.calls.append(("mark_submitted", order_id, exchange_order_id))

    def reconcile_fills(
        self,
        order_id,
        exchange_order,
        exchange_trades,
        expected_side="buy",
    ):
        self.calls.append(
            (
                "reconcile_fills",
                order_id,
                exchange_order,
                exchange_trades,
                expected_side,
            )
        )

    def finalize_exit_order(self, **kwargs):
        self.calls.append(("finalize_exit_order", kwargs))
        return (
            Decimal("0.00040"),
            Decimal(52000),
            self.fully_closed,
            self.external_commission_assets,
        )


def make_position(quantity="0.00040"):
    return SimpleNamespace(
        id=7,
        symbol="BTCUSDT",
        side="long",
        quantity=Decimal(quantity),
        bot_run_id=123,
    )


def test_sell_signal_submits_and_reconciles_market_exit(monkeypatch):
    from app.execution import exit_manager as module

    monkeypatch.setattr(module, "get_open_position", lambda symbol: make_position())
    client = FakeExitClient()
    risk_manager = FakeExitRiskManager()
    manager = PositionExitManager(client, risk_manager)

    result = manager.close_for_signal(
        strategy_key="ema",
        symbol="btcusdt",
        bot_run_id=123,
    )

    assert result.status == "exited"
    assert result.position_id == 7
    order = next(call[1] for call in client.calls if call[0] == "create_order")
    assert order["side"] == "SELL"
    assert order["type"] == "MARKET"
    assert order["quantity"] == Decimal("0.00040")
    assert risk_manager.calls[0][1]["position_id"] == 7
    assert any(
        call[-1] == "sell"
        for call in risk_manager.calls
        if call[0] == "reconcile_fills"
    )
    assert risk_manager.calls[-1][0] == "finalize_exit_order"


def test_exit_result_warns_about_unconverted_external_commissions(monkeypatch):
    from app.execution import exit_manager as module

    monkeypatch.setattr(module, "get_open_position", lambda symbol: make_position())
    manager = PositionExitManager(
        FakeExitClient(),
        FakeExitRiskManager(external_commission_assets=["BNB"]),
    )

    result = manager.close_for_signal(strategy_key="ema", symbol="BTCUSDT")

    assert result.status == "exited"
    assert "PnL excluye conversión de comisiones en BNB." in result.reason


def test_partial_market_exit_is_canceled_and_recorded(monkeypatch):
    from app.execution import exit_manager as module

    monkeypatch.setattr(module, "get_open_position", lambda symbol: make_position())
    client = FakeExitClient(status="PARTIALLY_FILLED")
    risk_manager = FakeExitRiskManager(fully_closed=False)
    manager = PositionExitManager(client, risk_manager)

    result = manager.close_for_signal(
        strategy_key="rsi",
        symbol="BTCUSDT",
    )

    assert result.status == "partially_exited"
    assert any(call[0] == "cancel_order" for call in client.calls)
    assert any(call[0] == "finalize_exit_order" for call in risk_manager.calls)


def test_exit_sells_no_more_than_position_or_free_balance(monkeypatch):
    from app.execution import exit_manager as module

    monkeypatch.setattr(
        module,
        "get_open_position",
        lambda symbol: make_position("0.00040"),
    )
    client = FakeExitClient(free_balance="0.000276")
    manager = PositionExitManager(client, FakeExitRiskManager())

    manager.close_for_signal(strategy_key="ema", symbol="BTCUSDT")

    order = next(call[1] for call in client.calls if call[0] == "create_order")
    assert order["quantity"] == Decimal("0.00027")


def test_sell_signal_without_open_position_does_not_submit(monkeypatch):
    from app.execution import exit_manager as module

    monkeypatch.setattr(module, "get_open_position", lambda symbol: None)
    client = FakeExitClient()
    manager = PositionExitManager(client, FakeExitRiskManager())

    result = manager.close_for_signal(strategy_key="ema", symbol="BTCUSDT")

    assert result.status == "rejected"
    assert not any(call[0] == "create_order" for call in client.calls)


def test_router_routes_sell_signal_to_position_exit(monkeypatch):
    exit_result = ExecutionResult(
        strategy_key="ema",
        symbol="BTCUSDT",
        status="exited",
        reason="position closed",
    )

    class FakeRiskManager:
        def __getattr__(self, name):
            raise AssertionError(f"BUY-only risk operation called: {name}")

    class FakeClient:
        base_url = BINANCE_SPOT_TESTNET_URL

    class FakeExitManager:
        def close_for_signal(self, **kwargs):
            return exit_result

    router = SignalRouter(
        client=FakeClient(),
        risk_manager=FakeRiskManager(),
        exit_manager=FakeExitManager(),
    )
    signal = StrategySignal(
        symbol="BTCUSDT",
        side="SELL",
        reason="exit",
        generated_at=datetime.now(UTC),
    )

    assert router.route_signals({"ema": [signal]}) == [exit_result]
