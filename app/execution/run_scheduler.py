import argparse
import json
import logging
import sys
from collections.abc import Callable
from decimal import Decimal
from functools import partial
from threading import Event

from app.execution.run_execution import execute_once
from app.market_data.candle_collector import collect_candles

LOGGER = logging.getLogger(__name__)

INTERVAL_SECONDS = {
    "1m": 60,
    "3m": 3 * 60,
    "5m": 5 * 60,
    "15m": 15 * 60,
    "30m": 30 * 60,
    "1h": 60 * 60,
    "2h": 2 * 60 * 60,
    "4h": 4 * 60 * 60,
    "6h": 6 * 60 * 60,
    "8h": 8 * 60 * 60,
    "12h": 12 * 60 * 60,
    "1d": 24 * 60 * 60,
    "3d": 3 * 24 * 60 * 60,
    "1w": 7 * 24 * 60 * 60,
}


def run_cycle(
    *,
    symbol: str,
    interval: str,
    limit: int,
    fast: int,
    slow: int,
    rsi_period: int,
    budget_usdt: Decimal,
) -> dict:
    saved_candles = collect_candles(
        symbol=symbol,
        interval=interval,
        limit=limit,
    )
    result = execute_once(
        symbol=symbol,
        interval=interval,
        limit=limit,
        fast=fast,
        slow=slow,
        rsi_period=rsi_period,
        budget_usdt=budget_usdt,
    )
    result["candles_updated"] = saved_candles
    return result


def run_forever(
    run_cycle_callback: Callable[[], dict],
    *,
    interval_seconds: int,
    stop_event: Event | None = None,
) -> None:
    if interval_seconds <= 0:
        raise ValueError("El periodo del scheduler debe ser positivo.")

    stop_event = stop_event or Event()
    LOGGER.info(
        "Scheduler activo: primera ejecución en %s segundos.",
        interval_seconds,
    )
    while not stop_event.wait(interval_seconds):
        try:
            result = run_cycle_callback()
        except Exception:
            LOGGER.exception(
                "Falló el ciclo del scheduler; se reintentará en el siguiente."
            )
            continue
        LOGGER.info(
            "Ciclo completado: %s",
            json.dumps(result, ensure_ascii=False),
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Actualiza velas cerradas y ejecuta estrategias periódicamente "
            "en Binance Spot Testnet."
        )
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument(
        "--interval",
        default="1m",
        help="Intervalo de vela y frecuencia del scheduler (p. ej. 1m, 5m, 1h).",
    )
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--fast", type=int, default=9)
    parser.add_argument("--slow", type=int, default=21)
    parser.add_argument("--rsi-period", type=int, default=14)
    parser.add_argument(
        "--budget-usdt",
        type=Decimal,
        default=Decimal(20),
        help="Presupuesto máximo por entrada BUY (predeterminado: 20 USDT).",
    )
    args = parser.parse_args()

    if args.interval not in INTERVAL_SECONDS:
        parser.error(
            "--interval debe ser uno de: "
            + ", ".join(INTERVAL_SECONDS)
            + " (el intervalo mensual no tiene una duración fija)."
        )
    if not 1 <= args.limit <= 1000:
        parser.error("--limit debe estar entre 1 y 1000.")
    if not args.budget_usdt.is_finite() or args.budget_usdt <= 0:
        parser.error("--budget-usdt debe ser positivo y finito.")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cycle = partial(
        run_cycle,
        symbol=args.symbol,
        interval=args.interval,
        limit=args.limit,
        fast=args.fast,
        slow=args.slow,
        rsi_period=args.rsi_period,
        budget_usdt=args.budget_usdt,
    )
    try:
        run_forever(
            cycle,
            interval_seconds=INTERVAL_SECONDS[args.interval],
        )
    except KeyboardInterrupt:
        LOGGER.info("Scheduler detenido.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
