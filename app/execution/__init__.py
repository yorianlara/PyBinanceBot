"""Motor de ejecución: conecta señales de estrategia con Binance."""

from app.execution.order_executor import OrderExecutionError, OrderExecutor
from app.execution.signal_router import ExecutionResult, SignalRouter

__all__ = [
    "OrderExecutionError",
    "OrderExecutor",
    "ExecutionResult",
    "SignalRouter",
]

