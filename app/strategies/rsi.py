
from decimal import Decimal

from app.strategies.base import StrategyContext, StrategySignal


class RsiStrategy:
    def __init__(
        self,
        period: int = 14,
        oversold: Decimal = Decimal("30"),
        overbought: Decimal = Decimal("70"),
    ):
        if period < 2:
            raise ValueError("El periodo RSI debe ser al menos 2.")

        if not Decimal("0") < oversold < overbought < Decimal("100"):
            raise ValueError("Los umbrales RSI deben cumplir 0 < oversold < overbought < 100.")

        self.period = period
        self.oversold = oversold
        self.overbought = overbought

    @property
    def key(self) -> str:
        return f"rsi_{self.period}"

    def _rsi_values(self, closes: list[Decimal]) -> list[Decimal]:
        changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains = [max(change, Decimal("0")) for change in changes]
        losses = [max(-change, Decimal("0")) for change in changes]

        avg_gain = sum(gains[:self.period], Decimal("0")) / self.period
        avg_loss = sum(losses[:self.period], Decimal("0")) / self.period

        values = []

        def calculate_rsi(gain: Decimal, loss: Decimal) -> Decimal:
            if gain == 0 and loss == 0:
                return Decimal("50")
            if loss == 0:
                return Decimal("100")
            if gain == 0:
                return Decimal("0")
            return Decimal("100") - (
                Decimal("100") / (Decimal("1") + gain / loss)
            )

        values.append(calculate_rsi(avg_gain, avg_loss))

        for i in range(self.period, len(changes)):
            avg_gain = (
                avg_gain * (self.period - 1) + gains[i]
            ) / self.period
            avg_loss = (
                avg_loss * (self.period - 1) + losses[i]
            ) / self.period
            values.append(calculate_rsi(avg_gain, avg_loss))

        return values

    def generate_signals(
        self,
        context: StrategyContext,
    ) -> list[StrategySignal]:
        candles = sorted(context.candles, key=lambda candle: candle.open_time)

        if len(candles) < self.period + 2:
            return []

        closes = [Decimal(str(candle.close)) for candle in candles]

        if any(not value.is_finite() or value <= 0 for value in closes):
            raise ValueError("Los precios de cierre deben ser positivos y finitos.")

        rsi = self._rsi_values(closes)

        previous = rsi[-2]
        current = rsi[-1]
        side = None

        if previous <= self.oversold and current > self.oversold:
            side = "BUY"
        elif previous >= self.overbought and current < self.overbought:
            side = "SELL"

        if side is None:
            return []

        return [
            StrategySignal(
                symbol=context.symbol.upper(),
                side=side,
                reason=f"RSI({self.period}) cruzó el umbral: {previous:.2f} -> {current:.2f}",
                generated_at=candles[-1].close_time,
            )
        ]
