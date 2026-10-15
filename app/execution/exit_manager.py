"""Gestión de salida: evaluación y cierre de posiciones abiertas según reglas de riesgo."""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from app.execution.exit_config import DEFAULT_EXIT_CONFIG, ExitConfig
from app.execution.order_executor import OrderExecutionError, OrderExecutor
from app.risk.persistent_manager import PersistentRiskManager, RiskRejected
from app.storage.models import TradePosition
from app.storage.repository import get_open_positions
from app.strategies.base import StrategySignal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExitDecision:
    """Evaluación de si una posición debe ser cerrada."""

    position_id: int
    symbol: str
    should_close: bool
    reason: str  # "stop_loss", "take_profit", "timeout", "hold", "signal_sell"
    current_price: Decimal
    entry_price: Decimal
    pnl_pct: Decimal


@dataclass(frozen=True)
class ExitResult:
    """Resultado del cierre de una posición."""

    position_id: int
    symbol: str
    decision: ExitDecision
    closed: bool
    exit_price: Decimal | None = None
    realized_pnl_eur: Decimal | None = None
    error: str | None = None


class ExitManager:
    """Supervisa posiciones abiertas y ejecuta cierres automáticos
    según reglas de stop-loss, take-profit, timeout y señales SELL.
    """

    def __init__(
        self,
        risk: PersistentRiskManager,
        executor: OrderExecutor,
        config: ExitConfig | None = None,
        eur_to_usdt: Decimal = Decimal("1.08"),
    ):
        if not eur_to_usdt.is_finite() or eur_to_usdt <= 0:
            raise ValueError("El tipo de cambio EUR/USDT debe ser positivo y finito.")

        self.risk = risk
        self.executor = executor
        self.config = config or DEFAULT_EXIT_CONFIG
        self.eur_to_usdt = eur_to_usdt

    def evaluate_position(
        self,
        position: TradePosition,
        current_price: Decimal,
        current_time: datetime | None = None,
    ) -> ExitDecision:
        """Determina si una posición abierta debe cerrarse según stop-loss, take-profit o timeout."""
        entry_price = position.entry_price

        if entry_price <= 0:
            raise ValueError(f"Precio de entrada inválido en posición {position.id}: {entry_price}")

        pnl_pct = ((current_price - entry_price) / entry_price) * Decimal("100")

        # 1. Stop-Loss
        if pnl_pct <= -abs(self.config.stop_loss_pct):
            return ExitDecision(
                position_id=position.id,
                symbol=position.symbol,
                should_close=True,
                reason="stop_loss",
                current_price=current_price,
                entry_price=entry_price,
                pnl_pct=pnl_pct,
            )

        # 2. Take-Profit
        if pnl_pct >= abs(self.config.take_profit_pct):
            return ExitDecision(
                position_id=position.id,
                symbol=position.symbol,
                should_close=True,
                reason="take_profit",
                current_price=current_price,
                entry_price=entry_price,
                pnl_pct=pnl_pct,
            )

        # 3. Timeout
        now = current_time or datetime.now(timezone.utc)
        opened_at = position.opened_at
        if opened_at is not None:
            if opened_at.tzinfo is None:
                opened_at = opened_at.replace(tzinfo=timezone.utc)

            age_seconds = Decimal(str((now - opened_at).total_seconds()))
            age_hours = age_seconds / Decimal("3600")

            if age_hours >= Decimal(str(self.config.max_hours)):
                return ExitDecision(
                    position_id=position.id,
                    symbol=position.symbol,
                    should_close=True,
                    reason="timeout",
                    current_price=current_price,
                    entry_price=entry_price,
                    pnl_pct=pnl_pct,
                )

        return ExitDecision(
            position_id=position.id,
            symbol=position.symbol,
            should_close=False,
            reason="hold",
            current_price=current_price,
            entry_price=entry_price,
            pnl_pct=pnl_pct,
        )

    def close_position(
        self,
        position: TradePosition,
        decision: ExitDecision,
    ) -> ExitResult:
        """Ejecuta la venta en Binance y registra el cierre con el gestor de riesgo."""
        try:
            exit_price = self.executor.execute_market_sell(
                symbol=position.symbol,
                quantity=position.quantity,
            )
        except OrderExecutionError as exc:
            logger.error(
                "Fallo al ejecutar venta para posición %d (%s): %s",
                position.id,
                position.symbol,
                exc,
            )
            return ExitResult(
                position_id=position.id,
                symbol=position.symbol,
                decision=decision,
                closed=False,
                error=f"Error al vender en Binance: {exc}",
            )

        # Cálculo de PnL realizado
        pnl_usdt = (exit_price - position.entry_price) * position.quantity
        realized_pnl_eur = (pnl_usdt / self.eur_to_usdt).quantize(Decimal("0.01"))

        try:
            self.risk.close_position(
                position.id,
                exit_price=exit_price,
                realized_pnl_eur=realized_pnl_eur,
            )
        except (RiskRejected, ValueError) as exc:
            logger.error(
                "Error registrando cierre en gestor de riesgo para posición %d: %s",
                position.id,
                exc,
            )
            return ExitResult(
                position_id=position.id,
                symbol=position.symbol,
                decision=decision,
                closed=False,
                exit_price=exit_price,
                realized_pnl_eur=realized_pnl_eur,
                error=f"Error en gestor de riesgo: {exc}",
            )

        logger.info(
            "Posición %d cerrada (%s). Salida=%s PnL=%s EUR Razón=%s",
            position.id,
            position.symbol,
            exit_price,
            realized_pnl_eur,
            decision.reason,
        )

        return ExitResult(
            position_id=position.id,
            symbol=position.symbol,
            decision=decision,
            closed=True,
            exit_price=exit_price,
            realized_pnl_eur=realized_pnl_eur,
        )

    def check_and_exit_positions(
        self,
        positions: list[TradePosition] | None = None,
        current_time: datetime | None = None,
    ) -> list[ExitResult]:
        """Evalúa y cierra todas las posiciones abiertas según las reglas configuradas."""
        if positions is None:
            positions = get_open_positions()

        results: list[ExitResult] = []

        for position in positions:
            try:
                current_price = self.executor.get_current_price(position.symbol)
            except Exception as exc:
                logger.error(
                    "No se pudo obtener precio para %s (posición %d): %s",
                    position.symbol,
                    position.id,
                    exc,
                )
                decision = ExitDecision(
                    position_id=position.id,
                    symbol=position.symbol,
                    should_close=False,
                    reason="price_error",
                    current_price=Decimal("0"),
                    entry_price=position.entry_price,
                    pnl_pct=Decimal("0"),
                )
                results.append(
                    ExitResult(
                        position_id=position.id,
                        symbol=position.symbol,
                        decision=decision,
                        closed=False,
                        error=f"Error de precio: {exc}",
                    )
                )
                continue

            decision = self.evaluate_position(
                position,
                current_price=current_price,
                current_time=current_time,
            )

            if decision.should_close:
                result = self.close_position(position, decision)
                results.append(result)
            else:
                results.append(
                    ExitResult(
                        position_id=position.id,
                        symbol=position.symbol,
                        decision=decision,
                        closed=False,
                    )
                )

        return results

    def check_signal_exit(
        self,
        position: TradePosition,
        signal: StrategySignal,
    ) -> ExitResult | None:
        """Cierra una posición si una estrategia genera una señal SELL para el mismo símbolo."""
        if signal.side != "SELL" or signal.symbol.upper() != position.symbol.upper():
            return None

        try:
            current_price = self.executor.get_current_price(position.symbol)
        except Exception:
            current_price = position.entry_price

        pnl_pct = ((current_price - position.entry_price) / position.entry_price) * Decimal("100")

        decision = ExitDecision(
            position_id=position.id,
            symbol=position.symbol,
            should_close=True,
            reason=f"signal_sell: {signal.reason}",
            current_price=current_price,
            entry_price=position.entry_price,
            pnl_pct=pnl_pct,
        )

        return self.close_position(position, decision)

