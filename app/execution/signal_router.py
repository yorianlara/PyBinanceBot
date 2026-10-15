"""Enrutador de señales: conecta estrategias con riesgo y ejecución."""

import logging
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN

from app.execution.order_executor import OrderExecutor, OrderExecutionError
from app.risk.persistent_manager import PersistentRiskManager, RiskRejected
from app.strategies.base import StrategySignal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExecutionResult:
    """Resultado del enrutamiento de una señal."""

    strategy_key: str
    signal: StrategySignal
    order_id: int | None
    position_id: int | None
    status: str
    reason: str


class SignalRouter:
    """Enruta señales de estrategias a través del gestor de riesgo
    hacia el motor de ejecución.

    Flujo por señal BUY:
    1. Obtiene precio actual del par
    2. Calcula cantidad según la asignación en EUR
    3. Pide al gestor de riesgo que reserve capital
    4. Si aprobada, ejecuta la orden en Binance
    """

    def __init__(
        self,
        risk: PersistentRiskManager,
        executor: OrderExecutor,
        allocation_eur: Decimal = Decimal("20"),
        eur_to_usdt: Decimal = Decimal("1.08"),
        quantity_precision: int = 5,
    ):
        if not allocation_eur.is_finite() or allocation_eur <= 0:
            raise ValueError(
                "La asignación en EUR debe ser positiva y finita."
            )

        if not eur_to_usdt.is_finite() or eur_to_usdt <= 0:
            raise ValueError(
                "El tipo de cambio EUR/USDT debe ser positivo y finito."
            )

        self.risk = risk
        self.executor = executor
        self.allocation_eur = allocation_eur
        self.eur_to_usdt = eur_to_usdt
        self.quantity_precision = quantity_precision

    def _calculate_quantity(self, price: Decimal) -> Decimal:
        """Calcula la cantidad a comprar según precio y asignación."""
        allocation_usdt = self.allocation_eur * self.eur_to_usdt
        raw_quantity = allocation_usdt / price
        step = Decimal(10) ** -self.quantity_precision
        return raw_quantity.quantize(step, rounding=ROUND_DOWN)

    def route_signal(
        self,
        strategy_key: str,
        signal: StrategySignal,
    ) -> ExecutionResult:
        """Enruta una señal individual: riesgo → ejecución."""

        # Solo BUY en Spot
        if signal.side != "BUY":
            return ExecutionResult(
                strategy_key=strategy_key,
                signal=signal,
                order_id=None,
                position_id=None,
                status="skipped",
                reason=(
                    f"Señal {signal.side} ignorada "
                    "(solo BUY en Spot)."
                ),
            )

        # Obtener precio actual
        try:
            price = self.executor.get_current_price(signal.symbol)
        except Exception as exc:
            return ExecutionResult(
                strategy_key=strategy_key,
                signal=signal,
                order_id=None,
                position_id=None,
                status="error",
                reason=f"No se pudo obtener el precio: {exc}",
            )

        # Calcular cantidad
        quantity = self._calculate_quantity(price)

        if quantity <= 0:
            return ExecutionResult(
                strategy_key=strategy_key,
                signal=signal,
                order_id=None,
                position_id=None,
                status="rejected",
                reason="Cantidad calculada es cero o negativa.",
            )

        # Reservar a través del gestor de riesgo
        try:
            order_id = self.risk.reserve_order(
                strategy_key=strategy_key,
                symbol=signal.symbol,
                side="BUY",
                order_type="MARKET",
                quantity=quantity,
                allocation_eur=self.allocation_eur,
            )
        except RiskRejected as exc:
            logger.info(
                "Riesgo rechazó señal de %s: %s",
                strategy_key,
                exc,
            )
            return ExecutionResult(
                strategy_key=strategy_key,
                signal=signal,
                order_id=None,
                position_id=None,
                status="rejected",
                reason=str(exc),
            )

        # Ejecutar en Binance
        try:
            position_id = self.executor.execute_market_buy(
                order_id, signal.symbol, quantity,
            )
        except (OrderExecutionError, RiskRejected) as exc:
            logger.error(
                "Error ejecutando señal de %s: %s",
                strategy_key,
                exc,
            )
            return ExecutionResult(
                strategy_key=strategy_key,
                signal=signal,
                order_id=order_id,
                position_id=None,
                status="error",
                reason=str(exc),
            )

        logger.info(
            "Señal de %s ejecutada: orden %d → posición %d",
            strategy_key,
            order_id,
            position_id,
        )

        return ExecutionResult(
            strategy_key=strategy_key,
            signal=signal,
            order_id=order_id,
            position_id=position_id,
            status="executed",
            reason=(
                f"Compra ejecutada: {quantity} {signal.symbol} "
                f"@ ~{price}"
            ),
        )

    def route_all(
        self,
        signals_by_strategy: dict[str, list[StrategySignal]],
    ) -> list[ExecutionResult]:
        """Enruta todas las señales del coordinador."""
        results: list[ExecutionResult] = []

        for strategy_key, signals in signals_by_strategy.items():
            for signal in signals:
                result = self.route_signal(strategy_key, signal)
                results.append(result)

        return results

