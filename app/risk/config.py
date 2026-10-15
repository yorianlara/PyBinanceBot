
from dataclasses import dataclass


@dataclass(frozen=True)
class RiskConfig:
    max_capital_eur: float = 100.0
    max_allocation_per_strategy_eur: float = 20.0
    max_open_positions: int = 1
    max_daily_loss_eur: float = 2.0
    max_total_loss_eur: float = 5.0


DEFAULT_RISK_CONFIG = RiskConfig()
