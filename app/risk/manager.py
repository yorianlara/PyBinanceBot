
from dataclasses import dataclass

from app.risk.config import DEFAULT_RISK_CONFIG, RiskConfig


@dataclass(frozen=True)
class RiskState:
    allocated_capital_eur: float = 0.0
    open_positions: int = 0
    daily_loss_eur: float = 0.0
    total_loss_eur: float = 0.0


class RiskManager:
    def __init__(self, config: RiskConfig = DEFAULT_RISK_CONFIG):
        self.config = config

    def validate(self, state: RiskState, allocation_eur: float) -> None:
        if allocation_eur <= 0:
            raise ValueError("La asignación debe ser mayor que cero")

        if state.allocated_capital_eur < 0:
            raise ValueError("El capital asignado no puede ser negativo")

        if state.open_positions < 0:
            raise ValueError("Las posiciones abiertas no pueden ser negativas")

        if state.daily_loss_eur >= self.config.max_daily_loss_eur:
            raise ValueError("Límite de pérdida diaria alcanzado")

        if state.total_loss_eur >= self.config.max_total_loss_eur:
            raise ValueError("Límite de pérdida total alcanzado")

        if state.open_positions >= self.config.max_open_positions:
            raise ValueError("Límite de posiciones abiertas alcanzado")

        if allocation_eur > self.config.max_allocation_per_strategy_eur:
            raise ValueError("Asignación superior al límite por estrategia")

        if (
            state.allocated_capital_eur + allocation_eur
            > self.config.max_capital_eur
        ):
            raise ValueError("Se superaría el capital máximo permitido")

