from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.backtesting.engine import Backtester
from app.strategies.base import StrategySignal


def make_candles(opens, closes=None):
    closes = closes or opens
    start = datetime(2026, 1, 1, tzinfo=UTC)
    candles = []
    for index, (open_price, close_price) in enumerate(zip(opens, closes)):
        open_price = Decimal(open_price)
        close_price = Decimal(close_price)
        timestamp = start + timedelta(minutes=index)
        low = min(open_price, close_price) - 1
        high = max(open_price, close_price) + 1
        candles.append(
            SimpleNamespace(
                symbol="BTCUSDT",
                interval="1m",
                open_time=timestamp,
                close_time=timestamp + timedelta(minutes=1) - timedelta(milliseconds=1),
                open=open_price,
                high=high,
                low=low,
                close=close_price,
                is_closed=True,
            )
        )
    return candles


class ScriptedStrategy:
    key = "scripted"

    def __init__(self, signals_by_length):
        self.signals_by_length = signals_by_length

    def generate_signals(self, context):
        signal = self.signals_by_length.get(len(context.candles))
        return [signal] if signal else []


def signal_for(candle, side):
    return StrategySignal(
        symbol="BTCUSDT",
        side=side,
        reason="test",
        generated_at=candle.close_time,
    )


def test_executes_signals_at_next_candle_open_and_accounts_for_costs():
    candles = make_candles(
        ["100", "110", "120", "130"],
        ["100", "115", "120", "135"],
    )
    strategy = ScriptedStrategy(
        {
            1: signal_for(candles[0], "BUY"),
            3: signal_for(candles[2], "SELL"),
        }
    )
    result = Backtester(
        starting_cash=Decimal(1000),
        trade_amount=Decimal(100),
        fee_bps=Decimal(100),
        slippage_bps=Decimal(100),
    ).run(strategy, candles)

    trade = result.trades[0]
    quantity = Decimal(100) / Decimal("111.1")
    gross_exit = quantity * Decimal("128.7")
    exit_fee = gross_exit * Decimal("0.01")

    assert trade.entry_price == Decimal("111.1")
    assert trade.exit_price == Decimal("128.7")
    assert trade.entry_time == candles[1].open_time.isoformat()
    assert trade.exit_time == candles[3].open_time.isoformat()
    assert trade.quantity == quantity
    assert trade.entry_fee == Decimal(1)
    assert trade.exit_fee == exit_fee
    assert trade.pnl == gross_exit - exit_fee - Decimal(101)
    assert result.closed_trades == 1
    assert result.winning_trades == 1
    assert result.open_quantity == 0
    assert result.final_equity == Decimal(1000) + trade.pnl


def test_does_not_execute_signal_until_a_later_candle_is_available():
    candles = make_candles(["100", "120"])
    strategy = ScriptedStrategy({2: signal_for(candles[1], "BUY")})

    result = Backtester().run(strategy, candles)

    assert result.closed_trades == 0
    assert result.open_quantity == 0
    assert result.final_equity == Decimal(1000)


def test_marks_open_position_to_market_at_final_close():
    candles = make_candles(["100", "110"], ["100", "120"])
    strategy = ScriptedStrategy({1: signal_for(candles[0], "BUY")})
    result = Backtester(
        starting_cash=Decimal(100),
        trade_amount=Decimal(20),
        fee_bps=Decimal(0),
        slippage_bps=Decimal(0),
    ).run(strategy, candles)

    assert result.closed_trades == 0
    assert result.open_quantity == Decimal(20) / Decimal(110)
    assert result.open_entry_price == Decimal(110)
    assert result.final_equity == Decimal(80) + result.open_quantity * Decimal(120)
    assert result.realized_pnl == 0


def test_calculates_drawdown_from_marked_equity():
    candles = make_candles(["100", "100", "80"], ["100", "90", "80"])
    strategy = ScriptedStrategy({1: signal_for(candles[0], "BUY")})
    result = Backtester(
        starting_cash=Decimal(100),
        trade_amount=Decimal(20),
        fee_bps=Decimal(0),
        slippage_bps=Decimal(0),
    ).run(strategy, candles)

    assert result.final_equity == Decimal(96)
    assert result.return_pct == Decimal(-4)
    assert result.max_drawdown_pct == Decimal(4)


def test_rejects_empty_candles():
    with pytest.raises(ValueError):
        Backtester().run(ScriptedStrategy({}), [])


def test_rejects_duplicate_candles():
    candle = make_candles(["100"])[0]
    with pytest.raises(ValueError, match="duplicadas"):
        Backtester().run(ScriptedStrategy({}), [candle, candle])


def test_rejects_open_candles():
    candle = make_candles(["100"])[0]
    candle.is_closed = False

    with pytest.raises(ValueError, match="cerradas"):
        Backtester().run(ScriptedStrategy({}), [candle])


@pytest.mark.parametrize(
    ("fee_bps", "slippage_bps"),
    [
        (Decimal(-1), Decimal(0)),
        (Decimal(0), Decimal(-1)),
        (Decimal(10000), Decimal(0)),
        (Decimal(0), Decimal(10000)),
    ],
)
def test_rejects_invalid_cost_assumptions(fee_bps, slippage_bps):
    with pytest.raises(ValueError):
        Backtester(fee_bps=fee_bps, slippage_bps=slippage_bps)
