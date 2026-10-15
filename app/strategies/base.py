from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Protocol, Sequence

from app.storage.models import MarketCandle


@dataclass(frozen=True)
class StrategyContext:
    symbol: str
    interval: str
    candles: Sequence[MarketCandle]


@dataclass(frozen=True)
class StrategySignal:
    symbol: str
    side: Literal["BUY", "SELL"]
    reason: str
    generated_at: datetime


class Strategy(Protocol):
    @property
    def key(self) -> str:
        ...

    def generate_signals(
        self,
        context: StrategyContext,
    ) -> list[StrategySignal]:
        ...