import logging
from decimal import Decimal

import pytest

from app.execution import run_scheduler


@pytest.mark.parametrize(
    ("interval", "seconds"),
    [
        ("1m", 60),
        ("5m", 300),
        ("1h", 3600),
        ("1d", 86400),
        ("1w", 604800),
    ],
)
def test_scheduler_period_matches_candle_interval(interval, seconds):
    assert run_scheduler.INTERVAL_SECONDS[interval] == seconds


def test_run_cycle_collects_candles_before_executing(monkeypatch):
    calls = []
    monkeypatch.setattr(
        run_scheduler,
        "collect_candles",
        lambda **kwargs: calls.append(("collect", kwargs)) or 3,
    )
    monkeypatch.setattr(
        run_scheduler,
        "execute_once",
        lambda **kwargs: calls.append(("execute", kwargs))
        or {"symbol": kwargs["symbol"]},
    )
    options = {
        "symbol": "BTCUSDT",
        "interval": "1m",
        "limit": 100,
        "fast": 9,
        "slow": 21,
        "rsi_period": 14,
        "budget_usdt": Decimal(20),
    }

    result = run_scheduler.run_cycle(**options)

    assert [call[0] for call in calls] == ["collect", "execute"]
    assert result == {"symbol": "BTCUSDT", "candles_updated": 3}
    assert calls[0][1] == {
        "symbol": "BTCUSDT",
        "interval": "1m",
        "limit": 100,
    }


def test_scheduler_logs_cycle_errors_and_continues(caplog):
    class StopAfterTwoCycles:
        def __init__(self):
            self.waits = 0

        def wait(self, timeout):
            assert timeout == 60
            self.waits += 1
            return self.waits == 3

    calls = []

    def run_cycle():
        calls.append("cycle")
        if len(calls) == 1:
            raise RuntimeError("temporary failure")
        return {"status": "ok"}

    with caplog.at_level(logging.ERROR):
        run_scheduler.run_forever(
            run_cycle,
            interval_seconds=60,
            stop_event=StopAfterTwoCycles(),
        )

    assert calls == ["cycle", "cycle"]
    assert "temporary failure" in caplog.text


def test_scheduler_rejects_nonpositive_period():
    with pytest.raises(ValueError, match="positivo"):
        run_scheduler.run_forever(dict, interval_seconds=0)
