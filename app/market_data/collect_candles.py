
import argparse

from app.market_data.candle_collector import collect_candles


def main():
    parser = argparse.ArgumentParser(
        description="Descarga velas cerradas de Binance Testnet."
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="1m")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()

    saved = collect_candles(
        symbol=args.symbol,
        interval=args.interval,
        limit=args.limit,
    )

    print(f"Velas procesadas y guardadas: {saved}")


if __name__ == "__main__":
    main()
