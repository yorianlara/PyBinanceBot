from datetime import UTC, datetime

from app.risk.signal_evaluator import evaluate_signals
from app.strategies.base import StrategySignal


def make_signal(side: str) -> StrategySignal:
    return StrategySignal(
        symbol="BTCUSDT",
        side=side,
        reason="test",
        generated_at=datetime.now(UTC),
    )


def test_buy_is_candidate():
    result = evaluate_signals(
        {"ema_cross_9_21": [make_signal("BUY")]}
    )

    assert len(result) == 1
    assert result[0].status == "candidate"
    assert result[0].side == "BUY"


def test_sell_is_an_exit_candidate():
    result = evaluate_signals(
        {"rsi_14": [make_signal("SELL")]}
    )

    assert len(result) == 1
    assert result[0].status == "exit_candidate"


def test_strategies_are_evaluated_independently():
    result = evaluate_signals(
        {
            "ema_cross_9_21": [make_signal("BUY")],
            "rsi_14": [make_signal("BUY")],
        }
    )

    assert len(result) == 2
    assert all(item.status == "candidate" for item in result)
    assert {item.strategy_key for item in result} == {
        "ema_cross_9_21",
        "rsi_14",
    }


def test_empty_signals_return_no_decisions():
    assert evaluate_signals({}) == []