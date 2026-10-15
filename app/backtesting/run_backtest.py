"""CLI para ejecutar simulaciones históricas y comparar estrategias."""

import argparse
import sys
from datetime import datetime, timezone
from decimal import Decimal

from app.backtesting.engine import BacktestEngine
from app.execution.exit_config import ExitConfig
from app.market_data.binance_client import BinanceMarketClient
from app.storage.models import MarketCandle
from app.storage.repository import get_latest_candles
from app.strategies.bollinger import BollingerBreakoutStrategy
from app.strategies.ema_cross import EmaCrossStrategy
from app.strategies.macd import MacdStrategy
from app.strategies.rsi import RsiStrategy


def _fetch_binance_candles(
    symbol: str = "BTCUSDT",
    interval: str = "1h",
    limit: int = 500,
) -> list[MarketCandle]:
    """Obtiene velas históricas directamente de Binance Testnet/API."""
    client = BinanceMarketClient()
    try:
        klines = client.get_klines(symbol=symbol, interval=interval, limit=limit)
    finally:
        client.close()

    candles = []
    for k in klines:
        o_time = datetime.fromtimestamp(int(k[0]) / 1000, tz=timezone.utc)
        c_time = datetime.fromtimestamp(int(k[6]) / 1000, tz=timezone.utc)
        candles.append(
            MarketCandle(
                symbol=symbol.upper(),
                interval=interval,
                open_time=o_time,
                close_time=c_time,
                open=Decimal(k[1]),
                high=Decimal(k[2]),
                low=Decimal(k[3]),
                close=Decimal(k[4]),
                volume=Decimal(k[5]),
                is_closed=True,
            )
        )
    return candles


def run_comparison(
    candles: list[MarketCandle],
    initial_capital: Decimal = Decimal("100"),
    allocation_per_trade: Decimal = Decimal("20"),
    sl_pct: Decimal = Decimal("3"),
    tp_pct: Decimal = Decimal("5"),
) -> None:
    """Ejecuta y compara cada estrategia por separado y luego combinadas."""
    exit_config = ExitConfig(
        stop_loss_pct=sl_pct,
        take_profit_pct=tp_pct,
        max_hours=48,
    )

    strategies = [
        ("EMA Cross (9/21)", EmaCrossStrategy(fast_period=9, slow_period=21)),
        ("RSI Reversión (14)", RsiStrategy(period=14)),
        ("Bollinger Breakout (20/2.0)", BollingerBreakoutStrategy(period=20, std_dev_multiplier=Decimal("2.0"))),
        ("MACD (12/26/9)", MacdStrategy(fast_period=12, slow_period=26, signal_period=9)),
    ]

    print("\n" + "=" * 80)
    print(f"📊 INFORME DE BACKTESTING — {candles[0].symbol} ({candles[0].interval})")
    print(f"Velas analizadas: {len(candles)} | Periodo: {candles[0].open_time:%Y-%m-%d} a {candles[-1].close_time:%Y-%m-%d}")
    print(f"Capital inicial: {initial_capital:.2f} € | Asignación por trade: {allocation_per_trade:.2f} €")
    print(f"Parámetros de salida: Stop-Loss={sl_pct}% | Take-Profit={tp_pct}%")
    print("=" * 80)
    print(
        f"{'Estrategia':<28} {'Trades':<8} {'Win Rate':<10} {'PnL (EUR)':<12} {'Retorno':<10} {'Drawdown':<10}"
    )
    print("-" * 80)

    all_strats = []
    for label, strat in strategies:
        engine = BacktestEngine(
            strategies=[strat],
            initial_capital_eur=initial_capital,
            allocation_per_trade_eur=allocation_per_trade,
            exit_config=exit_config,
        )
        report = engine.run(candles)
        all_strats.append(strat)

        pnl_str = f"{report.total_pnl_eur:+.2f} €"
        ret_str = f"{report.return_on_capital_pct:+.2f}%"
        win_str = f"{report.win_rate_pct:.1f}% ({report.winning_trades}/{report.total_trades})"
        dd_str = f"{report.max_drawdown_pct:.2f}%"

        print(
            f"{label:<28} {report.total_trades:<8} {win_str:<10} {pnl_str:<12} {ret_str:<10} {dd_str:<10}"
        )

    # Evaluación combinada (Multi-Bot Portfolio)
    print("-" * 80)
    portfolio_engine = BacktestEngine(
        strategies=all_strats,
        initial_capital_eur=initial_capital,
        allocation_per_trade_eur=allocation_per_trade,
        exit_config=exit_config,
    )
    port_report = portfolio_engine.run(candles)

    pnl_str = f"{port_report.total_pnl_eur:+.2f} €"
    ret_str = f"{port_report.return_on_capital_pct:+.2f}%"
    win_str = f"{port_report.win_rate_pct:.1f}% ({port_report.winning_trades}/{port_report.total_trades})"
    dd_str = f"{port_report.max_drawdown_pct:.2f}%"

    print(
        f"{'PORTAFOLIO MULTI-BOT (4)':<28} {port_report.total_trades:<8} {win_str:<10} {pnl_str:<12} {ret_str:<10} {dd_str:<10}"
    )
    print("=" * 80 + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ejecuta backtesting de estrategias sobre datos de Binance o base de datos."
    )
    parser.add_argument("--symbol", default="BTCUSDT", help="Par a simular (ej: BTCUSDT)")
    parser.add_argument("--interval", default="1h", help="Intervalo de velas (ej: 15m, 1h, 4h)")
    parser.add_argument("--limit", type=int, default=500, help="Número de velas a simular")
    parser.add_argument("--capital", type=float, default=100.0, help="Capital inicial en EUR")
    parser.add_argument("--alloc", type=float, default=20.0, help="Asignación por operación en EUR")
    parser.add_argument("--sl", type=float, default=3.0, help="Porcentaje de Stop-Loss")
    parser.add_argument("--tp", type=float, default=5.0, help="Porcentaje de Take-Profit")
    parser.add_argument(
        "--source",
        choices=["binance", "db"],
        default="binance",
        help="Origen de las velas (binance o db)",
    )
    args = parser.parse_args()

    print(f"Cargando velas para {args.symbol} ({args.interval}) desde {args.source}...")

    if args.source == "db":
        candles = get_latest_candles(symbol=args.symbol, interval=args.interval, limit=args.limit)
        if not candles:
            print(f"No se encontraron velas en la base de datos para {args.symbol} {args.interval}.")
            return 1
    else:
        try:
            candles = _fetch_binance_candles(
                symbol=args.symbol,
                interval=args.interval,
                limit=args.limit,
            )
        except Exception as exc:
            print(f"Error descargando datos de Binance: {exc}")
            return 1

    run_comparison(
        candles=candles,
        initial_capital=Decimal(str(args.capital)),
        allocation_per_trade=Decimal(str(args.alloc)),
        sl_pct=Decimal(str(args.sl)),
        tp_pct=Decimal(str(args.tp)),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

