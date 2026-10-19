from datetime import datetime, timezone

from app.strategies.coordinator import StrategyCoordinator
from app.strategies.ema_cross import EmaCrossStrategy
from app.strategies.rsi import RsiStrategy
from app.strategies.base import StrategyContext


def test_ema_and_rsi_run_together(monkeypatch):
    monkeypatch.setattr(
        "app.strategies.coordinator.create_run",
        lambda **kwargs: 123,
    )
    monkeypatch.setattr(
        "app.strategies.coordinator.finish_run",
        lambda *args, **kwargs: None,
    )

    coordinator = StrategyCoordinator([
        EmaCrossStrategy(fast_period=9, slow_period=21),
        RsiStrategy(period=14),
    ])

    context = StrategyContext(
        symbol="BTCUSDT",
        interval="1m",
        candles=[],
    )

    results = coordinator.run(context)

    assert set(results) == {"ema_cross_9_21", "rsi_14"}
    assert results["ema_cross_9_21"] == []
    assert results["rsi_14"] == []