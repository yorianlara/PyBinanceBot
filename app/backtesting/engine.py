"""Motor de simulación histórica (Backtesting) de estrategias."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Sequence

from app.backtesting.models import BacktestReport, BacktestTrade
from app.execution.exit_config import DEFAULT_EXIT_CONFIG, ExitConfig
from app.storage.models import MarketCandle
from app.strategies.base import Strategy, StrategyContext


class BacktestEngine:
    """Ejecuta simulaciones históricas aplicando reglas de estrategias,
    gestión de riesgo y políticas de salida (Stop-Loss / Take-Profit / Timeout).
    """

    def __init__(
        self,
        strategies: list[Strategy],
        initial_capital_eur: Decimal = Decimal("100"),
        allocation_per_trade_eur: Decimal = Decimal("20"),
        exit_config: ExitConfig | None = None,
        max_open_positions: int = 1,
        eur_to_usdt: Decimal = Decimal("1.08"),
        fee_rate: Decimal = Decimal("0.001"),  # 0.1% comisión Binance
        min_warmup_candles: int = 25,
    ):
        if not strategies:
            raise ValueError("Se requiere al menos una estrategia para el backtest.")
        if initial_capital_eur <= 0 or allocation_per_trade_eur <= 0:
            raise ValueError("El capital y la asignación deben ser positivos.")

        self.strategies = strategies
        self.initial_capital_eur = initial_capital_eur
        self.allocation_per_trade_eur = allocation_per_trade_eur
        self.exit_config = exit_config or DEFAULT_EXIT_CONFIG
        self.max_open_positions = max_open_positions
        self.eur_to_usdt = eur_to_usdt
        self.fee_rate = fee_rate
        self.min_warmup_candles = min_warmup_candles

    def run(self, candles: Sequence[MarketCandle]) -> BacktestReport:
        """Ejecuta la simulación sobre el conjunto de velas históricas."""
        sorted_candles = sorted(candles, key=lambda c: c.open_time)
        symbol = sorted_candles[0].symbol if sorted_candles else "UNKNOWN"
        interval = sorted_candles[0].interval if sorted_candles else "UNKNOWN"

        if len(sorted_candles) < self.min_warmup_candles + 2:
            return BacktestReport(
                strategy_keys=[s.key for s in self.strategies],
                symbol=symbol,
                interval=interval,
                total_candles=len(sorted_candles),
                initial_capital_eur=self.initial_capital_eur,
                final_capital_eur=self.initial_capital_eur,
                total_pnl_eur=Decimal("0"),
                return_on_capital_pct=Decimal("0"),
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                win_rate_pct=Decimal("0"),
                profit_factor=Decimal("0"),
                max_drawdown_eur=Decimal("0"),
                max_drawdown_pct=Decimal("0"),
                trades=[],
            )

        capital_eur = self.initial_capital_eur
        active_position: dict | None = None
        completed_trades: list[BacktestTrade] = []
        equity_peak_eur = capital_eur
        max_drawdown_eur = Decimal("0")
        max_drawdown_pct = Decimal("0")

        stop_loss_pct = abs(self.exit_config.stop_loss_pct) / Decimal("100")
        take_profit_pct = abs(self.exit_config.take_profit_pct) / Decimal("100")

        def _close_position(
            pos: dict,
            exit_price: Decimal,
            exit_time: datetime,
            reason: str,
        ) -> BacktestTrade:
            entry_price = pos["entry_price"]
            qty = pos["quantity"]

            # Valor bruto y comisiones
            entry_val_usdt = qty * entry_price
            exit_val_usdt = qty * exit_price
            fees_usdt = (entry_val_usdt + exit_val_usdt) * self.fee_rate

            net_pnl_usdt = (exit_val_usdt - entry_val_usdt) - fees_usdt
            net_pnl_eur = (net_pnl_usdt / self.eur_to_usdt).quantize(Decimal("0.01"))
            return_pct = (
                (exit_price - entry_price) / entry_price * Decimal("100")
            ).quantize(Decimal("0.01"))

            trade = BacktestTrade(
                strategy_key=pos["strategy_key"],
                symbol=pos["symbol"],
                entry_time=pos["entry_time"],
                exit_time=exit_time,
                entry_price=entry_price,
                exit_price=exit_price,
                quantity=qty,
                allocation_eur=pos["allocation_eur"],
                realized_pnl_eur=net_pnl_eur,
                return_pct=return_pct,
                exit_reason=reason,
            )
            return trade

        # Simulación vela por vela
        for i in range(self.min_warmup_candles, len(sorted_candles)):
            candle = sorted_candles[i]
            c_low = Decimal(str(candle.low))
            c_high = Decimal(str(candle.high))
            c_close = Decimal(str(candle.close))

            # 1. Evaluar salidas en posición activa
            if active_position is not None:
                entry = active_position["entry_price"]
                sl_threshold = entry * (Decimal("1") - stop_loss_pct)
                tp_threshold = entry * (Decimal("1") + take_profit_pct)
                should_close = False
                exit_price = c_close
                exit_reason = "hold"

                # Comprobar Stop-Loss
                if c_low <= sl_threshold:
                    should_close = True
                    exit_price = min(sl_threshold, Decimal(str(candle.open)))
                    exit_reason = "stop_loss"
                # Comprobar Take-Profit
                elif c_high >= tp_threshold:
                    should_close = True
                    exit_price = max(tp_threshold, Decimal(str(candle.open)))
                    exit_reason = "take_profit"
                # Comprobar Timeout
                else:
                    age_hours = Decimal(
                        str(
                            (
                                candle.close_time
                                - active_position["entry_time"]
                            ).total_seconds()
                        )
                    ) / Decimal("3600")
                    if age_hours >= Decimal(str(self.exit_config.max_hours)):
                        should_close = True
                        exit_price = c_close
                        exit_reason = "timeout"

                if should_close:
                    trade = _close_position(
                        active_position,
                        exit_price=exit_price,
                        exit_time=candle.close_time,
                        reason=exit_reason,
                    )
                    completed_trades.append(trade)
                    capital_eur += trade.realized_pnl_eur
                    active_position = None

                    # Actualizar métricas de drawdown
                    if capital_eur > equity_peak_eur:
                        equity_peak_eur = capital_eur
                    drawdown = equity_peak_eur - capital_eur
                    if drawdown > max_drawdown_eur:
                        max_drawdown_eur = drawdown
                        max_drawdown_pct = (
                            drawdown / equity_peak_eur * Decimal("100")
                        ).quantize(Decimal("0.01"))

            # 2. Generar señales de las estrategias
            context = StrategyContext(
                symbol=symbol,
                interval=interval,
                candles=sorted_candles[: i + 1],
            )

            for strategy in self.strategies:
                signals = strategy.generate_signals(context)
                for signal in signals:
                    if (
                        signal.side == "SELL"
                        and active_position is not None
                        and active_position["strategy_key"] == strategy.key
                    ):
                        trade = _close_position(
                            active_position,
                            exit_price=c_close,
                            exit_time=candle.close_time,
                            reason=f"signal_sell: {signal.reason}",
                        )
                        completed_trades.append(trade)
                        capital_eur += trade.realized_pnl_eur
                        active_position = None

                        if capital_eur > equity_peak_eur:
                            equity_peak_eur = capital_eur
                        drawdown = equity_peak_eur - capital_eur
                        if drawdown > max_drawdown_eur:
                            max_drawdown_eur = drawdown
                            max_drawdown_pct = (
                                drawdown / equity_peak_eur * Decimal("100")
                            ).quantize(Decimal("0.01"))

                    elif signal.side == "BUY" and active_position is None:
                        # Verificar reglas de capital
                        alloc_eur = min(self.allocation_per_trade_eur, capital_eur)
                        if alloc_eur > 0:
                            qty = (
                                (alloc_eur * self.eur_to_usdt) / c_close
                            ).quantize(Decimal("0.00001"))
                            active_position = {
                                "strategy_key": strategy.key,
                                "symbol": symbol,
                                "entry_time": candle.close_time,
                                "entry_price": c_close,
                                "quantity": qty,
                                "allocation_eur": alloc_eur,
                            }

        # Cierre forzado de posición residual al final del conjunto de datos
        if active_position is not None:
            last_candle = sorted_candles[-1]
            trade = _close_position(
                active_position,
                exit_price=Decimal(str(last_candle.close)),
                exit_time=last_candle.close_time,
                reason="end_of_data",
            )
            completed_trades.append(trade)
            capital_eur += trade.realized_pnl_eur

        # Cálculo de métricas agregadas
        total_trades = len(completed_trades)
        winning_trades = sum(1 for t in completed_trades if t.realized_pnl_eur > 0)
        losing_trades = sum(1 for t in completed_trades if t.realized_pnl_eur < 0)
        win_rate = (
            (Decimal(winning_trades) / Decimal(total_trades) * Decimal("100")).quantize(
                Decimal("0.01")
            )
            if total_trades > 0
            else Decimal("0")
        )

        gross_profit = sum(
            (t.realized_pnl_eur for t in completed_trades if t.realized_pnl_eur > 0),
            Decimal("0"),
        )
        gross_loss = abs(
            sum(
                (t.realized_pnl_eur for t in completed_trades if t.realized_pnl_eur < 0),
                Decimal("0"),
            )
        )
        profit_factor = (
            (gross_profit / gross_loss).quantize(Decimal("0.01"))
            if gross_loss > 0
            else (Decimal("999.99") if gross_profit > 0 else Decimal("0"))
        )

        total_pnl = capital_eur - self.initial_capital_eur
        return_pct = (
            (total_pnl / self.initial_capital_eur) * Decimal("100")
        ).quantize(Decimal("0.01"))

        return BacktestReport(
            strategy_keys=[s.key for s in self.strategies],
            symbol=symbol,
            interval=interval,
            total_candles=len(sorted_candles),
            initial_capital_eur=self.initial_capital_eur,
            final_capital_eur=capital_eur,
            total_pnl_eur=total_pnl,
            return_on_capital_pct=return_pct,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate_pct=win_rate,
            profit_factor=profit_factor,
            max_drawdown_eur=max_drawdown_eur,
            max_drawdown_pct=max_drawdown_pct,
            trades=completed_trades,
        )

