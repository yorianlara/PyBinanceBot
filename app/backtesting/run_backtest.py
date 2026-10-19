import argparse
import json
import sys
from dataclasses import asdict
from decimal import Decimal

from app.backtesting.engine import Backtester
from app.storage.repository import get_latest_candles
from app.strategies.base import Strategy
from app.strategies.ema_cross import EmaCrossStrategy
from app.strategies.rsi import RsiStrategy


def make_strategy(
    key: str,
    *,
    fast: int,
    slow: int,
    rsi_period: int,
) -> Strategy:
    if key == "ema":
        return EmaCrossStrategy(fast_period=fast, slow_period=slow)
    if key == "rsi":
        return RsiStrategy(period=rsi_period)
    raise ValueError(f"Estrategia no soportada: {key}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Ejecuta un backtest spot long-only sobre velas cerradas "
            "almacenadas localmente. Usa fills fraccionales teóricos; no modela "
            "liquidez ni filtros de lote de Binance y nunca envía órdenes."
        )
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="1m")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--strategy", choices=("ema", "rsi"), default="ema")
    parser.add_argument("--fast", type=int, default=9)
    parser.add_argument("--slow", type=int, default=21)
    parser.add_argument("--rsi-period", type=int, default=14)
    parser.add_argument(
        "--capital-usdt",
        type=Decimal,
        default=Decimal(1000),
    )
    parser.add_argument(
        "--trade-amount-usdt",
        type=Decimal,
        default=Decimal(20),
    )
    parser.add_argument(
        "--fee-bps",
        type=Decimal,
        default=Decimal(10),
        help="Comisión por lado en puntos básicos (10 bps = 0,1%%).",
    )
    parser.add_argument(
        "--slippage-bps",
        type=Decimal,
        default=Decimal(5),
        help="Slippage aplicado por lado en puntos básicos.",
    )
    args = parser.parse_args()

    if not 1 <= args.limit <= 5000:
        parser.error("--limit debe estar entre 1 y 5000.")
    try:
        strategy = make_strategy(
            args.strategy,
            fast=args.fast,
            slow=args.slow,
            rsi_period=args.rsi_period,
        )
        backtester = Backtester(
            starting_cash=args.capital_usdt,
            trade_amount=args.trade_amount_usdt,
            fee_bps=args.fee_bps,
            slippage_bps=args.slippage_bps,
        )
    except ValueError as exc:
        parser.error(str(exc))

    candles = get_latest_candles(
        symbol=args.symbol,
        interval=args.interval,
        limit=args.limit,
    )
    result = backtester.run(strategy, candles)
    print(json.dumps(asdict(result), indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
