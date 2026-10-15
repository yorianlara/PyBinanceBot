"""Motor principal (Engine): ciclo autónomo de trading multi-estrategia."""

import argparse
import logging
import signal
import sys
import time
from dataclasses import dataclass, field
from decimal import Decimal

from app.execution.exit_config import DEFAULT_EXIT_CONFIG, ExitConfig
from app.execution.exit_manager import ExitManager, ExitResult
from app.execution.order_executor import OrderExecutor
from app.execution.signal_router import ExecutionResult, SignalRouter
from app.market_data.binance_client import BinanceMarketClient
from app.market_data.candle_collector import collect_candles
from app.risk.config import DEFAULT_RISK_CONFIG, RiskConfig
from app.risk.persistent_manager import PersistentRiskManager
from app.storage.repository import get_latest_candles, get_open_positions
from app.strategies.base import Strategy, StrategyContext, StrategySignal
from app.strategies.bollinger import BollingerBreakoutStrategy
from app.strategies.coordinator import StrategyCoordinator
from app.strategies.ema_cross import EmaCrossStrategy
from app.strategies.macd import MacdStrategy
from app.strategies.rsi import RsiStrategy

logger = logging.getLogger("trading_engine")


@dataclass(frozen=True)
class EngineCycleResult:
    """Resumen de ejecución de un ciclo del motor."""

    timestamp: float
    candles_saved: dict[str, int]
    exit_results: list[ExitResult]
    signals_generated: dict[str, dict[str, list[StrategySignal]]]
    execution_results: list[ExecutionResult]


class TradingEngine:
    """Motor central que orquesta:
    1. Recolección de datos de mercado
    2. Supervisión y cierre de salidas (Stop Loss / Take Profit / Timeout)
    3. Evaluación de señales multi-estrategia
    4. Enrutamiento controlado por gestor de riesgo
    """

    def __init__(
        self,
        coordinator: StrategyCoordinator,
        risk_manager: PersistentRiskManager,
        executor: OrderExecutor,
        router: SignalRouter,
        exit_manager: ExitManager,
        symbols: list[str] | None = None,
        interval: str = "1m",
        candles_limit: int = 100,
    ):
        self.coordinator = coordinator
        self.risk_manager = risk_manager
        self.executor = executor
        self.router = router
        self.exit_manager = exit_manager
        self.symbols = [s.upper() for s in (symbols or ["BTCUSDT"])]
        self.interval = interval
        self.candles_limit = candles_limit
        self._running = False

    def run_once(self) -> EngineCycleResult:
        """Ejecuta un ciclo completo de evaluación y operación."""
        cycle_start = time.time()
        logger.info("Iniciando ciclo de trading...")

        # 1. Recolectar velas recientes de Binance
        candles_saved: dict[str, int] = {}
        for symbol in self.symbols:
            try:
                count = collect_candles(
                    symbol=symbol,
                    interval=self.interval,
                    limit=self.candles_limit,
                )
                candles_saved[symbol] = count
            except Exception as exc:
                logger.error("Error recolectando velas para %s: %s", symbol, exc)
                candles_saved[symbol] = 0

        # 2. Evaluar y ejecutar salidas (Stop Loss, Take Profit, Timeout)
        exit_results: list[ExitResult] = []
        try:
            exit_results = self.exit_manager.check_and_exit_positions()
            for er in exit_results:
                if er.closed:
                    logger.info(
                        "Posición cerrada en ciclo: %s %s PnL=%s EUR (%s)",
                        er.position_id,
                        er.symbol,
                        er.realized_pnl_eur,
                        er.decision.reason,
                    )
        except Exception as exc:
            logger.error("Error en supervisión de salidas: %s", exc)

        # 3. Generar señales con el coordinador multi-estrategia
        signals_generated: dict[str, dict[str, list[StrategySignal]]] = {}
        all_buy_signals: dict[str, list[StrategySignal]] = {}

        for symbol in self.symbols:
            try:
                candles = get_latest_candles(
                    symbol=symbol,
                    interval=self.interval,
                    limit=self.candles_limit,
                )
                if not candles:
                    logger.warning("No hay velas almacenadas para %s", symbol)
                    continue

                context = StrategyContext(
                    symbol=symbol,
                    interval=self.interval,
                    candles=candles,
                )
                strategy_signals = self.coordinator.run(context)
                signals_generated[symbol] = strategy_signals

                # Evaluar señales SELL contra posiciones abiertas actuales
                open_positions = get_open_positions(symbol=symbol)
                for strat_key, signals in strategy_signals.items():
                    buy_list = []
                    for sig in signals:
                        if sig.side == "SELL":
                            for pos in open_positions:
                                exit_res = self.exit_manager.check_signal_exit(pos, sig)
                                if exit_res and exit_res.closed:
                                    exit_results.append(exit_res)
                        elif sig.side == "BUY":
                            buy_list.append(sig)

                    if buy_list:
                        all_buy_signals[strat_key] = (
                            all_buy_signals.get(strat_key, []) + buy_list
                        )
            except Exception as exc:
                logger.error("Error evaluando estrategias para %s: %s", symbol, exc)

        # 4. Enrutar señales BUY a través del gestor de riesgo y ejecutar
        execution_results: list[ExecutionResult] = []
        if all_buy_signals:
            try:
                execution_results = self.router.route_all(all_buy_signals)
                for ex_res in execution_results:
                    logger.info(
                        "Resultado enrutamiento [%s - %s]: %s (%s)",
                        ex_res.strategy_key,
                        ex_res.signal.symbol,
                        ex_res.status,
                        ex_res.reason,
                    )
            except Exception as exc:
                logger.error("Error enrutando señales: %s", exc)

        duration = time.time() - cycle_start
        logger.info(
            "Ciclo completado en %.2fs. Velas: %s | Salidas: %d | Órdenes: %d",
            duration,
            candles_saved,
            len([e for e in exit_results if e.closed]),
            len([x for x in execution_results if x.status == "executed"]),
        )

        return EngineCycleResult(
            timestamp=cycle_start,
            candles_saved=candles_saved,
            exit_results=exit_results,
            signals_generated=signals_generated,
            execution_results=execution_results,
        )

    def run_loop(
        self,
        poll_interval_seconds: int = 60,
        max_iterations: int | None = None,
    ) -> None:
        """Bucle continuo con manejo seguro de interrupciones."""
        self._running = True

        def _handle_exit(signum, frame):
            logger.info("Señal de parada recibida (%s). Cerrando bucle...", signum)
            self._running = False

        signal.signal(signal.SIGINT, _handle_exit)
        signal.signal(signal.SIGTERM, _handle_exit)

        iteration = 0
        logger.info(
            "Iniciando bucle del motor (intervalo: %ds, símbolos: %s)...",
            poll_interval_seconds,
            self.symbols,
        )

        while self._running:
            iteration += 1
            try:
                self.run_once()
            except Exception as exc:
                logger.exception("Error inesperado en ciclo %d: %s", iteration, exc)

            if max_iterations is not None and iteration >= max_iterations:
                logger.info("Alcanzado número máximo de iteraciones (%d).", max_iterations)
                break

            if self._running:
                time.sleep(poll_interval_seconds)

        logger.info("Motor detenido correctamente.")


def build_default_engine(
    symbols: list[str] | None = None,
    interval: str = "1m",
    allocation_eur: Decimal = Decimal("20"),
    eur_to_usdt: Decimal = Decimal("1.08"),
    risk_config: RiskConfig | None = None,
    exit_config: ExitConfig | None = None,
) -> TradingEngine:
    """Construye un TradingEngine con la configuración estándar del sistema."""
    strategies: list[Strategy] = [
        EmaCrossStrategy(fast_period=9, slow_period=21),
        RsiStrategy(period=14),
        BollingerBreakoutStrategy(period=20, std_dev_multiplier=Decimal("2.0")),
        MacdStrategy(fast_period=12, slow_period=26, signal_period=9),
    ]
    coordinator = StrategyCoordinator(strategies)
    risk_manager = PersistentRiskManager(config=risk_config or DEFAULT_RISK_CONFIG)
    binance_client = BinanceMarketClient()
    executor = OrderExecutor(client=binance_client, risk=risk_manager)
    router = SignalRouter(
        risk=risk_manager,
        executor=executor,
        allocation_eur=allocation_eur,
        eur_to_usdt=eur_to_usdt,
    )
    exit_manager = ExitManager(
        risk=risk_manager,
        executor=executor,
        config=exit_config or DEFAULT_EXIT_CONFIG,
        eur_to_usdt=eur_to_usdt,
    )

    return TradingEngine(
        coordinator=coordinator,
        risk_manager=risk_manager,
        executor=executor,
        router=router,
        exit_manager=exit_manager,
        symbols=symbols or ["BTCUSDT"],
        interval=interval,
    )


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Motor de trading multi-estrategia autónomo."
    )
    parser.add_argument(
        "--symbol",
        nargs="+",
        default=["BTCUSDT"],
        help="Símbolos a operar (ej: BTCUSDT ETHUSDT)",
    )
    parser.add_argument("--interval", default="1m", help="Intervalo de velas (ej: 1m, 5m, 1h)")
    parser.add_argument("--poll", type=int, default=60, help="Segundos entre ciclos")
    parser.add_argument("--once", action="store_true", help="Ejecutar un solo ciclo y salir")
    parser.add_argument(
        "--iterations",
        type=int,
        default=None,
        help="Número máximo de ciclos a ejecutar",
    )
    args = parser.parse_args()

    engine = build_default_engine(
        symbols=args.symbol,
        interval=args.interval,
    )

    if args.once:
        engine.run_once()
        return 0

    engine.run_loop(
        poll_interval_seconds=args.poll,
        max_iterations=args.iterations,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

