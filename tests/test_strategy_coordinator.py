from datetime import datetime, timezone

import pytest

from app.strategies.base import StrategyContext, StrategySignal
from app.strategies.coordinator import StrategyCoordinator


class FakeStrategy:
    def __init__(self, key, signals=None, error=None):
        self.key = key
        self.signals = signals or []
        self.error = error

    def generate_signals(self, context):
        if self.error:
            raise self.error
        return self.signals


def test_rejects_duplicate_strategy_keys():
    with pytest.raises(ValueError, match="únicas"):
        StrategyCoordinator([
            FakeStrategy("ema"),
            FakeStrategy("ema"),
        ])


def test_runs_multiple_strategies(monkeypatch):
    monkeypatch.setattr(
        "app.strategies.coordinator.create_run",
        lambda **kwargs: 123,
    )

    finished = []
    monkeypatch.setattr(
        "app.strategies.coordinator.finish_run",
        lambda run_id, **kwargs: finished.append((run_id, kwargs)),
    )

    signal = StrategySignal(
        symbol="BTCUSDT",
        side="BUY",
        reason="Prueba",
        generated_at=datetime.now(timezone.utc),
    )

    coordinator = StrategyCoordinator([
        FakeStrategy("ema", [signal]),
        FakeStrategy("rsi"),
    ])

    context = StrategyContext(
        symbol="BTCUSDT",
        interval="1m",
        candles=[],
    )

    results = coordinator.run(context)

    assert results["ema"] == [signal]
    assert results["rsi"] == []
    assert coordinator.last_run_id == 123
    assert finished == [(123, {"status": "completed"})]


def test_marks_run_failed_when_strategy_raises(monkeypatch):
    monkeypatch.setattr(
        "app.strategies.coordinator.create_run",
        lambda **kwargs: 456,
    )

    finished = []
    monkeypatch.setattr(
        "app.strategies.coordinator.finish_run",
        lambda run_id, **kwargs: finished.append((run_id, kwargs)),
    )

    coordinator = StrategyCoordinator([
        FakeStrategy("broken", error=RuntimeError("fallo de prueba")),
    ])

    context = StrategyContext(
        symbol="BTCUSDT",
        interval="1m",
        candles=[],
    )

    with pytest.raises(RuntimeError, match="fallo de prueba"):
        coordinator.run(context)

    assert finished[0][0] == 456
    assert finished[0][1]["status"] == "failed"