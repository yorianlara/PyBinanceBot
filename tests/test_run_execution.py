from decimal import Decimal
from types import SimpleNamespace

from app.execution import run_execution
from app.execution.signal_router import BINANCE_SPOT_TESTNET_URL


def test_execute_once_preserves_testnet_strategy_execution(monkeypatch):
    calls = []
    candles = [object()]
    monkeypatch.setattr(
        run_execution,
        "get_latest_candles",
        lambda **kwargs: calls.append(("candles", kwargs)) or candles,
    )

    class FakeCoordinator:
        last_run_id = 12

        def __init__(self, strategies):
            calls.append(("strategies", [strategy.key for strategy in strategies]))

        def run(self, context, mode):
            calls.append(("run", context, mode))
            return {"ema": []}

    class FakeClient:
        def __init__(self, *, base_url):
            calls.append(("client", base_url))

        def close(self):
            calls.append(("close",))

    class FakeRouter:
        def __init__(self, *, client, quote_budget_usdt):
            calls.append(("router", quote_budget_usdt))

        def route_signals(self, signals, *, bot_run_id):
            calls.append(("route", signals, bot_run_id))
            return [
                SimpleNamespace(
                    strategy_key="ema",
                    symbol="BTCUSDT",
                    status="rejected",
                    reason="no signal",
                    order_id=None,
                    position_id=None,
                )
            ]

    monkeypatch.setattr(run_execution, "StrategyCoordinator", FakeCoordinator)
    monkeypatch.setattr(run_execution, "BinanceMarketClient", FakeClient)
    monkeypatch.setattr(run_execution, "SignalRouter", FakeRouter)

    result = run_execution.execute_once(
        symbol="btcusdt",
        interval="1m",
        limit=100,
        budget_usdt=Decimal(20),
    )

    assert result["environment"] == "binance_spot_testnet"
    assert result["symbol"] == "BTCUSDT"
    assert result["candles"] == 1
    assert result["executions"][0]["status"] == "rejected"
    assert calls[0] == (
        "candles",
        {"symbol": "BTCUSDT", "interval": "1m", "limit": 100},
    )
    assert ("client", BINANCE_SPOT_TESTNET_URL) in calls
    assert calls[-1] == ("close",)
