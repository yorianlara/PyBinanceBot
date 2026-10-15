"""Configuración de reglas de salida para posiciones abiertas."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ExitConfig:
    """Parámetros de salida para posiciones abiertas.

    Con 20 € por estrategia:
    - Stop-loss 3 % ≈ 0.60 € de pérdida
    - Take-profit 5 % ≈ 1.00 € de ganancia
    - Timeout 48 h: cierre forzado por tiempo
    """

    stop_loss_pct: Decimal = Decimal("3")
    take_profit_pct: Decimal = Decimal("5")
    max_hours: int = 48


DEFAULT_EXIT_CONFIG = ExitConfig()

