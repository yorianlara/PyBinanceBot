"""Estrategia de Tendencia e Impulso MACD (Moving Average Convergence Divergence)."""

from decimal import Decimal

from app.strategies.base import StrategyContext, StrategySignal


class MacdStrategy:
    """Estrategia MACD (12, 26, 9 estándar).

    - Compra (BUY) cuando la línea MACD cruza por encima de la línea de señal.
    - Venta (SELL) cuando la línea MACD cruza por debajo de la línea de señal.
    """

    def __init__(
        self,
        fast_period: int = 12,
        slow_period: int = 26,
        signal_period: int = 9,
    ):
        if fast_period < 1 or slow_period < 2 or signal_period < 1:
            raise ValueError("Todos los periodos deben ser mayores que cero.")
        if fast_period >= slow_period:
            raise ValueError("El periodo rápido debe ser menor que el lento.")

        self.fast_period = fast_period
        self.slow_period = slow_period
        self.signal_period = signal_period

    @property
    def key(self) -> str:
        return f"macd_{self.fast_period}_{self.slow_period}_{self.signal_period}"

    @staticmethod
    def _ema(values: list[Decimal], period: int) -> list[Decimal]:
        multiplier = Decimal("2") / Decimal(period + 1)
        ema_values = [values[0]]
        for val in values[1:]:
            ema = (val - ema_values[-1]) * multiplier + ema_values[-1]
            ema_values.append(ema)
        return ema_values

    def generate_signals(
        self,
        context: StrategyContext,
    ) -> list[StrategySignal]:
        candles = sorted(context.candles, key=lambda c: c.open_time)
        min_candles = self.slow_period + self.signal_period + 2

        if len(candles) < min_candles:
            return []

        closes = [Decimal(str(c.close)) for c in candles]
        if any(not v.is_finite() or v <= 0 for v in closes):
            raise ValueError("Los precios de cierre deben ser positivos y finitos.")

        fast_ema = self._ema(closes, self.fast_period)
        slow_ema = self._ema(closes, self.slow_period)

        macd_line = [f - s for f, s in zip(fast_ema, slow_ema)]
        signal_line = self._ema(macd_line, self.signal_period)

        prev_macd = macd_line[-2]
        curr_macd = macd_line[-1]
        prev_signal = signal_line[-2]
        curr_signal = signal_line[-1]

        prev_diff = prev_macd - prev_signal
        curr_diff = curr_macd - curr_signal

        side = None
        if prev_diff <= 0 and curr_diff > 0:
            side = "BUY"
        elif prev_diff >= 0 and curr_diff < 0:
            side = "SELL"

        if side is None:
            return []

        return [
            StrategySignal(
                symbol=context.symbol.upper(),
                side=side,
                reason=(
                    f"Cruce MACD({self.fast_period},{self.slow_period},{self.signal_period}): "
                    f"MACD={curr_macd:.2f}, Señal={curr_signal:.2f}"
                ),
                generated_at=candles[-1].close_time,
            )
        ]

