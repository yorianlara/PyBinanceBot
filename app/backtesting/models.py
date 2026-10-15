"""Modelos de datos y métricas para el motor de backtesting."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class BacktestTrade:
    """Registro de una operación completada en el backtesting."""

    strategy_key: str
    symbol: str
    entry_time: datetime
    exit_time: datetime
    entry_price: Decimal
    exit_price: Decimal
    quantity: Decimal
    allocation_eur: Decimal
    realized_pnl_eur: Decimal
    return_pct: Decimal
    exit_reason: str  # "stop_loss", "take_profit", "timeout", "signal_sell", "end_of_data"


@dataclass(frozen=True)
class BacktestReport:
    """Reporte detallado del resultado del backtest."""

    strategy_keys: list[str]
    symbol: str
    interval: str
    total_candles: int
    initial_capital_eur: Decimal
    final_capital_eur: Decimal
    total_pnl_eur: Decimal
    return_on_capital_pct: Decimal
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: Decimal
    profit_factor: Decimal
    max_drawdown_eur: Decimal
    max_drawdown_pct: Decimal
    trades: list[BacktestTrade] = field(default_factory=list)

