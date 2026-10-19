import argparse
import json
import sys
from decimal import Decimal

from app.execution.signal_router import (
    BINANCE_SPOT_TESTNET_URL,
    SignalRouter,
)
from app.market_data.binance_client import BinanceMarketClient
from app.storage.repository import finish_run, get_latest_candles
from app.strategies.base import StrategyContext
from app.strategies.coordinator import StrategyCoordinator
from app.strategies.ema_cross import EmaCrossStrategy
from app.strategies.rsi import RsiStrategy


def execute_once(
    *,
    symbol: str = "BTCUSDT",
    interval: str = "1m",
    limit: int = 100,
    fast: int = 9,
    slow: int = 21,
    rsi_period: int = 14,
    budget_usdt: Decimal = Decimal(20),
) -> dict:
    symbol = symbol.upper()
    candles = get_latest_candles(
        symbol=symbol,
        interval=interval,
        limit=limit,
    )
    context = StrategyContext(
        symbol=symbol,
        interval=interval,
        candles=candles,
    )
    coordinator = StrategyCoordinator(
        [
            EmaCrossStrategy(
                fast_period=fast,
                slow_period=slow,
            ),
            RsiStrategy(period=rsi_period),
        ]
    )
    signals_by_strategy = coordinator.run(context, mode="testnet")

    client = BinanceMarketClient(base_url=BINANCE_SPOT_TESTNET_URL)
    try:
        router = SignalRouter(
            client=client,
            quote_budget_usdt=budget_usdt,
        )
        run_id = coordinator.last_run_id
        if run_id is None:
            raise RuntimeError("No se pudo asociar el run de estrategias.")
        try:
            executions = router.route_signals(
                signals_by_strategy,
                bot_run_id=run_id,
            )
        except Exception as exc:
            finish_run(
                run_id,
                status="failed",
                details=f"Error de ejecución: {type(exc).__name__}: {exc}",
            )
            raise
    finally:
        client.close()

    return {
        "environment": "binance_spot_testnet",
        "symbol": symbol,
        "interval": interval,
        "candles": len(candles),
        "budget_usdt": str(budget_usdt),
        "strategies": {
            strategy_key: [
                {
                    "symbol": signal.symbol,
                    "side": signal.side,
                    "reason": signal.reason,
                    "generated_at": signal.generated_at.isoformat(),
                }
                for signal in signals
            ]
            for strategy_key, signals in signals_by_strategy.items()
        },
        "executions": [
            {
                "strategy": result.strategy_key,
                "symbol": result.symbol,
                "status": result.status,
                "reason": result.reason,
                "order_id": result.order_id,
                "position_id": result.position_id,
            }
            for result in executions
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ejecuta estrategias y órdenes BUY en Binance Spot Testnet."
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="1m")
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

    if not 1 <= args.limit <= 5000:
        parser.error("--limit debe estar entre 1 y 5000.")
    if not args.budget_usdt.is_finite() or args.budget_usdt <= 0:
        parser.error("--budget-usdt debe ser positivo y finito.")

    output = execute_once(
        symbol=args.symbol,
        interval=args.interval,
        limit=args.limit,
        fast=args.fast,
        slow=args.slow,
        rsi_period=args.rsi_period,
        budget_usdt=args.budget_usdt,
    )
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
