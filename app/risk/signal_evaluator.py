from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from app.strategies.base import StrategySignal


@dataclass(frozen=True)
class SignalDecision:
    strategy_key: str
    symbol: str
    side: str
    status: Literal["candidate", "exit_candidate", "rejected"]
    reason: str


def evaluate_signals(
    signals_by_strategy: Mapping[str, Sequence[StrategySignal]],
) -> list[SignalDecision]:
    decisions: list[SignalDecision] = []

    for strategy_key, signals in signals_by_strategy.items():
        for signal in signals:
            if signal.side == "BUY":
                decisions.append(
                    SignalDecision(
                        strategy_key=strategy_key,
                        symbol=signal.symbol,
                        side=signal.side,
                        status="candidate",
                        reason="Pendiente de validación por el gestor de riesgo",
                    )
                )
            elif signal.side == "SELL":
                decisions.append(
                    SignalDecision(
                        strategy_key=strategy_key,
                        symbol=signal.symbol,
                        side=signal.side,
                        status="exit_candidate",
                        reason="Señal de salida pendiente de cierre.",
                    )
                )
            else:
                decisions.append(
                    SignalDecision(
                        strategy_key=strategy_key,
                        symbol=signal.symbol,
                        side=signal.side,
                        status="rejected",
                        reason=f"Dirección de señal no admitida: {signal.side}",
                    )
                )

    return decisions