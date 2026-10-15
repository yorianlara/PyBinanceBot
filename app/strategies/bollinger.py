"""Estrategia de Ruptura de Bandas de Bollinger (Bollinger Breakout)."""

from decimal import Decimal
import math

from app.strategies.base import StrategyContext, StrategySignal


class BollingerBreakoutStrategy:
    """Estrategia de ruptura de volatilidad con Bandas de Bollinger.

    - Compra (BUY) cuando el precio cierra por encima de la banda superior con expansión.
    - Venta (SELL) cuando el precio cae por debajo de la media móvil central.
    """

    def __init__(
        self,
        period: int = 20,
        std_dev_multiplier: Decimal = Decimal("2.0"),
    ):
        if period < 2:
            raise ValueError("El periodo debe ser al menos 2.")
        if std_dev_multiplier <= Decimal("0"):
            raise ValueError("El multiplicador de desviación debe ser positivo.")

        self.period = period
        self.std_dev_multiplier = std_dev_multiplier

    @property
    def key(self) -> str:
        return f"bollinger_{self.period}_{self.std_dev_multiplier}"

    def generate_signals(
        self,
        context: StrategyContext,
    ) -> list[StrategySignal]:
        candles = sorted(context.candles, key=lambda c: c.open_time)

        if len(candles) < self.period + 1:
            return []

        closes = [Decimal(str(c.close)) for c in candles]
        if any(not v.is_finite() or v <= 0 for v in closes):
            raise ValueError("Los precios de cierre deben ser positivos y finitos.")

        # Calcular media y desviación típica de la ventana anterior y actual
        def _calc_bands(slice_closes: list[Decimal]) -> tuple[Decimal, Decimal, Decimal]:
            sma = sum(slice_closes, Decimal("0")) / Decimal(len(slice_closes))
            variance = sum(
                (x - sma) ** 2 for x in slice_closes
            ) / Decimal(len(slice_closes))
            std_dev = Decimal(str(math.sqrt(float(variance))))
            upper = sma + (self.std_dev_multiplier * std_dev)
            lower = sma - (self.std_dev_multiplier * std_dev)
            return sma, upper, lower

        prev_slice = closes[-(self.period + 1) : -1]
        curr_slice = closes[-self.period :]

        prev_sma, prev_upper, _ = _calc_bands(prev_slice)
        curr_sma, curr_upper, curr_lower = _calc_bands(curr_slice)

        prev_close = closes[-2]
        curr_close = closes[-1]

        # Ruptura alcista: el precio cruza por encima de la banda superior
        if prev_close <= prev_upper and curr_close > curr_upper:
            return [
                StrategySignal(
                    symbol=context.symbol.upper(),
                    side="BUY",
                    reason=(
                        f"Ruptura alcista Bollinger({self.period}): "
                        f"cierre {curr_close} > banda superior {curr_upper:.2f}"
                    ),
                    generated_at=candles[-1].close_time,
                )
            ]

        # Cruce bajista: el precio cae por debajo de la media móvil central
        if prev_close >= prev_sma and curr_close < curr_sma:
            return [
                StrategySignal(
                    symbol=context.symbol.upper(),
                    side="SELL",
                    reason=(
                        f"Pérdida de media móvil Bollinger({self.period}): "
                        f"cierre {curr_close} < SMA {curr_sma:.2f}"
                    ),
                    generated_at=candles[-1].close_time,
                )
            ]

        return []

