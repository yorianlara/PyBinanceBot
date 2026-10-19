from dataclasses import dataclass
from typing import Literal

ExecutionStatus = Literal[
    "opened",
    "partially_filled",
    "exited",
    "partially_exited",
    "pending",
    "rejected",
]


@dataclass(frozen=True)
class ExecutionResult:
    strategy_key: str
    symbol: str
    status: ExecutionStatus
    reason: str
    order_id: int | None = None
    position_id: int | None = None
