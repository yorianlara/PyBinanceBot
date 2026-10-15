"""Motor de ejecución: conecta señales de estrategia con Binance y gestiona salidas."""

from app.execution.exit_config import DEFAULT_EXIT_CONFIG, ExitConfig
from app.execution.exit_manager import ExitDecision, ExitManager, ExitResult
from app.execution.order_executor import OrderExecutionError, OrderExecutor
from app.execution.signal_router import ExecutionResult, SignalRouter

__all__ = [
    "DEFAULT_EXIT_CONFIG",
    "ExitConfig",
    "ExitDecision",
    "ExitManager",
    "ExitResult",
    "ExecutionResult",
    "OrderExecutionError",
    "OrderExecutor",
    "SignalRouter",
]
