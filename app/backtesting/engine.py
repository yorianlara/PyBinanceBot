from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.storage.models import MarketCandle
from app.strategies.base import Strategy, StrategyContext, StrategySignal

ZERO = Decimal(0)
BPS_DIVISOR = Decimal(10_000)


@dataclass(frozen=True)
class BacktestTrade:
    entry_time: str
    exit_time: str
    entry_price: Decimal
    exit_price: Decimal
    quantity: Decimal
    entry_fee: Decimal
    exit_fee: Decimal
    pnl: Decimal


@dataclass(frozen=True)
class BacktestResult:
    strategy: str
    symbol: str
    interval: str
    candles: int
    starting_cash: Decimal
    final_equity: Decimal
    realized_pnl: Decimal
    return_pct: Decimal
    max_drawdown_pct: Decimal
    closed_trades: int
    winning_trades: int
    win_rate_pct: Decimal
    open_quantity: Decimal
    open_entry_price: Decimal | None
    trades: tuple[BacktestTrade, ...]
    equity_curve: tuple[tuple[str, Decimal], ...]


@dataclass
class _OpenPosition:
    entry_time: str
    entry_price: Decimal
    quantity: Decimal
    entry_fee: Decimal
    total_entry_cost: Decimal


class Backtester:
    """Simulate fractional spot fills with per-side fees and price slippage."""

    def __init__(
        self,
        *,
        starting_cash: Decimal = Decimal(1000),
        trade_amount: Decimal = Decimal(20),
        fee_bps: Decimal = Decimal(10),
        slippage_bps: Decimal = Decimal(5),
    ) -> None:
        self.starting_cash = self._positive_decimal(
            starting_cash,
            "El capital inicial",
        )
        self.trade_amount = self._positive_decimal(
            trade_amount,
            "El importe por operación",
        )
        self.fee_bps = self._nonnegative_decimal(
            fee_bps,
            "La comisión",
        )
        self.slippage_bps = self._nonnegative_decimal(
            slippage_bps,
            "El slippage",
        )
        if self.fee_bps >= BPS_DIVISOR or self.slippage_bps >= BPS_DIVISOR:
            raise ValueError("Comisión y slippage deben ser menores de 10000 bps.")

    def run(
        self,
        strategy: Strategy,
        candles: Sequence[MarketCandle],
    ) -> BacktestResult:
        """Fill close-generated signals next bar; mark remaining inventory at close."""
        ordered_candles = sorted(candles, key=lambda candle: candle.open_time)
        self._validate_candles(ordered_candles)
        first = ordered_candles[0]
        symbol = first.symbol.upper()
        interval = first.interval

        cash = self.starting_cash
        position: _OpenPosition | None = None
        pending_signal: StrategySignal | None = None
        trades: list[BacktestTrade] = []
        equity_curve: list[tuple[str, Decimal]] = []

        for index, candle in enumerate(ordered_candles):
            if pending_signal is not None:
                if pending_signal.side == "BUY" and position is None:
                    position, cash = self._buy(
                        candle,
                        cash,
                    )
                elif pending_signal.side == "SELL" and position is not None:
                    trade, cash = self._sell(
                        candle,
                        cash,
                        position,
                    )
                    trades.append(trade)
                    position = None

            marked_equity = cash
            if position is not None:
                marked_equity += position.quantity * Decimal(str(candle.close))
            equity_curve.append((candle.close_time.isoformat(), marked_equity))

            context = StrategyContext(
                symbol=symbol,
                interval=interval,
                candles=ordered_candles[: index + 1],
            )
            signals = strategy.generate_signals(context)
            pending_signal = next(
                (
                    signal
                    for signal in signals
                    if signal.symbol.upper() == symbol
                    and signal.generated_at == candle.close_time
                ),
                None,
            )

        final_equity = equity_curve[-1][1]
        realized_pnl = sum((trade.pnl for trade in trades), ZERO)
        winning_trades = sum(trade.pnl > 0 for trade in trades)
        total_trades = len(trades)
        return_pct = (
            (final_equity - self.starting_cash)
            / self.starting_cash
            * Decimal(100)
        )
        peak = self.starting_cash
        max_drawdown = ZERO
        for _, equity in equity_curve:
            peak = max(peak, equity)
            if peak > 0:
                max_drawdown = max(max_drawdown, (peak - equity) / peak)

        return BacktestResult(
            strategy=strategy.key,
            symbol=symbol,
            interval=interval,
            candles=len(ordered_candles),
            starting_cash=self.starting_cash,
            final_equity=final_equity,
            realized_pnl=realized_pnl,
            return_pct=return_pct,
            max_drawdown_pct=max_drawdown * Decimal(100),
            closed_trades=total_trades,
            winning_trades=winning_trades,
            win_rate_pct=(
                Decimal(winning_trades) / Decimal(total_trades) * Decimal(100)
                if total_trades
                else ZERO
            ),
            open_quantity=position.quantity if position is not None else ZERO,
            open_entry_price=(
                position.entry_price if position is not None else None
            ),
            trades=tuple(trades),
            equity_curve=tuple(equity_curve),
        )

    def _buy(
        self,
        candle: MarketCandle,
        cash: Decimal,
    ) -> tuple[_OpenPosition | None, Decimal]:
        raw_price = Decimal(str(candle.open))
        price = raw_price * (
            Decimal(1) + self.slippage_bps / BPS_DIVISOR
        )
        spend = min(self.trade_amount, cash / (Decimal(1) + self.fee_bps / BPS_DIVISOR))
        if spend <= 0:
            return None, cash
        fee = spend * self.fee_bps / BPS_DIVISOR
        quantity = spend / price
        return (
            _OpenPosition(
                entry_time=candle.open_time.isoformat(),
                entry_price=price,
                quantity=quantity,
                entry_fee=fee,
                total_entry_cost=spend + fee,
            ),
            cash - spend - fee,
        )

    def _sell(
        self,
        candle: MarketCandle,
        cash: Decimal,
        position: _OpenPosition,
    ) -> tuple[BacktestTrade, Decimal]:
        raw_price = Decimal(str(candle.open))
        price = raw_price * (
            Decimal(1) - self.slippage_bps / BPS_DIVISOR
        )
        gross_proceeds = position.quantity * price
        exit_fee = gross_proceeds * self.fee_bps / BPS_DIVISOR
        net_proceeds = gross_proceeds - exit_fee
        return (
            BacktestTrade(
                entry_time=position.entry_time,
                exit_time=candle.open_time.isoformat(),
                entry_price=position.entry_price,
                exit_price=price,
                quantity=position.quantity,
                entry_fee=position.entry_fee,
                exit_fee=exit_fee,
                pnl=net_proceeds - position.total_entry_cost,
            ),
            cash + net_proceeds,
        )

    @staticmethod
    def _validate_candles(candles: Sequence[MarketCandle]) -> None:
        if not candles:
            raise ValueError("No hay velas para ejecutar el backtest.")
        symbol = candles[0].symbol.upper()
        interval = candles[0].interval
        seen_open_times = set()
        for candle in candles:
            if candle.symbol.upper() != symbol or candle.interval != interval:
                raise ValueError("Todas las velas deben ser del mismo símbolo e intervalo.")
            if not candle.is_closed:
                raise ValueError("El backtest solo admite velas cerradas.")
            if candle.open_time in seen_open_times:
                raise ValueError("El conjunto contiene velas duplicadas.")
            seen_open_times.add(candle.open_time)
            prices = [
                Decimal(str(value))
                for value in (
                    candle.open,
                    candle.high,
                    candle.low,
                    candle.close,
                )
            ]
            if any(not price.is_finite() or price <= 0 for price in prices):
                raise ValueError("Los precios OHLC deben ser positivos y finitos.")
            if (
                prices[1] < prices[2]
                or prices[1] < max(prices[0], prices[3])
                or prices[2] > min(prices[0], prices[3])
            ):
                raise ValueError("Una vela tiene valores OHLC inconsistentes.")

    @staticmethod
    def _positive_decimal(value: Decimal, label: str) -> Decimal:
        result = Decimal(str(value))
        if not result.is_finite() or result <= 0:
            raise ValueError(f"{label} debe ser positivo y finito.")
        return result

    @staticmethod
    def _nonnegative_decimal(value: Decimal, label: str) -> Decimal:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0:
            raise ValueError(f"{label} debe ser no negativa y finita.")
        return result
