
from app.market_data.binance_client import BinanceMarketClient


def test_binance_testnet_connection():
    client = BinanceMarketClient()

    try:
        assert client.ping() is True

        price = client.get_price("BTCUSDT")

        assert price["symbol"] == "BTCUSDT"
        assert float(price["price"]) > 0
    finally:
        client.close()
