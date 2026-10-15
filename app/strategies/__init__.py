"""Catálogo de estrategias disponibles para el sistema multi-bot."""

from app.strategies.base import Strategy, StrategyContext, StrategySignal
from app.strategies.bollinger import BollingerBreakoutStrategy
from app.strategies.coordinator import StrategyCoordinator
from app.strategies.ema_cross import EmaCrossStrategy
from app.strategies.macd import MacdStrategy
from app.strategies.rsi import RsiStrategy

__all__ = [
    "BollingerBreakoutStrategy",
    "EmaCrossStrategy",
    "MacdStrategy",
    "RsiStrategy",
    "Strategy",
    "StrategyContext",
    "StrategyCoordinator",
    "StrategySignal",
]

