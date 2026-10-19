import argparse
import json
import sys

from app.storage.repository import get_latest_candles
from app.strategies.base import StrategyContext
from app.strategies.coordinator import StrategyCoordinator
from app.strategies.ema_cross import EmaCrossStrategy
from app.strategies.rsi import RsiStrategy


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ejecuta estrategias sobre velas almacenadas."
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="1m")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--fast", type=int, default=9)
    parser.add_argument("--slow", type=int, default=21)
    parser.add_argument("--rsi-period", type=int, default=14)
    args = parser.parse_args()

    if not 1 <= args.limit <= 5000:
        parser.error("--limit debe estar entre 1 y 5000.")

    ema_strategy = EmaCrossStrategy(
        fast_period=args.fast,
        slow_period=args.slow,
    )

    rsi_strategy = RsiStrategy(
        period=args.rsi_period,
    )

    candles = get_latest_candles(
        symbol=args.symbol,
        interval=args.interval,
        limit=args.limit,
    )

    context = StrategyContext(
        symbol=args.symbol.upper(),
        interval=args.interval,
        candles=candles,
    )

    coordinator = StrategyCoordinator([
        ema_strategy,
        rsi_strategy,
    ])
    
    results = coordinator.run(context, mode="testnet")

    output = {
        "symbol": args.symbol.upper(),
        "interval": args.interval,
        "candles": len(candles),
        "strategies": {},
    }

    for key, signals in results.items():
        output["strategies"][key] = [
            {
                "symbol": signal.symbol,
                "side": signal.side,
                "reason": signal.reason,
                "generated_at": signal.generated_at.isoformat(),
            }
            for signal in signals
        ]

    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())