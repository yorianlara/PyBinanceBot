from datetime import datetime, timezone
from decimal import Decimal

from app.strategies.base import StrategyContext, StrategySignal


class EmaCrossStrategy:
    def __init__(
        self,
        fast_period: int = 9,
        slow_period: int = 21,
    ):
        if fast_period < 1 or slow_period < 2:
            raise ValueError("Los periodos deben ser positivos.")

        if fast_period >= slow_period:
            raise ValueError(
                "La EMA rápida debe tener un periodo menor que la lenta."
            )

        self.fast_period = fast_period
        self.slow_period = slow_period

    @property
    def key(self) -> str:
        return f"ema_cross_{self.fast_period}_{self.slow_period}"

    @staticmethod
    def _ema(values: list[Decimal], period: int) -> list[Decimal]:
        multiplier = Decimal("2") / Decimal(period + 1)
        ema_values = [values[0]]

        for value in values[1:]:
            ema = (value - ema_values[-1]) * multiplier + ema_values[-1]
            ema_values.append(ema)

        return ema_values

    def generate_signals(
        self,
        context: StrategyContext,
    ) -> list[StrategySignal]:
        minimum = self.slow_period + 1

        if len(context.candles) < minimum:
            return []

        candles = sorted(context.candles, key=lambda candle: candle.open_time)
        closes = [Decimal(str(candle.close)) for candle in candles]

        if any(not value.is_finite() or value <= 0 for value in closes):
            raise ValueError("Los precios de cierre deben ser positivos y finitos.")

        fast = self._ema(closes, self.fast_period)
        slow = self._ema(closes, self.slow_period)

        previous_difference = fast[-2] - slow[-2]
        current_difference = fast[-1] - slow[-1]

        side = None

        if previous_difference <= 0 and current_difference > 0:
            side = "BUY"
        elif previous_difference >= 0 and current_difference < 0:
            side = "SELL"

        if side is None:
            return []

        return [
            StrategySignal(
                symbol=context.symbol.upper(),
                side=side,
                reason=(
                    f"Cruce EMA {self.fast_period}/{self.slow_period}; "
                    f"cierre={closes[-1]}"
                ),
                generated_at=candles[-1].close_time,
            )
        ]