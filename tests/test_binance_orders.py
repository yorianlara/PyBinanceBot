from app.market_data.binance_client import BinanceMarketClient


def test_get_order_uses_signed_endpoint(monkeypatch):
    client = BinanceMarketClient()
    captured = {}

    def fake_signed_get(path, params=None):
        captured["path"] = path
        captured["params"] = params
        return {"symbol": "BTCUSDT", "status": "PARTIALLY_FILLED"}

    monkeypatch.setattr(client, "_signed_get", fake_signed_get)

    try:
        result = client.get_order("btcusdt", 12345)

        assert result["status"] == "PARTIALLY_FILLED"
        assert captured["path"] == "/api/v3/order"
        assert captured["params"] == {
            "symbol": "BTCUSDT",
            "orderId": 12345,
        }
    finally:
        client.close()


def test_get_my_trades_uses_signed_endpoint(monkeypatch):
    client = BinanceMarketClient()
    captured = {}
    expected = [
        {
            "id": 987,
            "orderId": 12345,
            "qty": "0.001",
            "price": "50000.00",
            "commission": "0.000001",
            "commissionAsset": "BTC",
        }
    ]

    def fake_signed_get(path, params=None):
        captured["path"] = path
        captured["params"] = params
        return expected

    monkeypatch.setattr(client, "_signed_get", fake_signed_get)

    try:
        result = client.get_my_trades("btcusdt", 12345)

        assert result == expected
        assert captured["path"] == "/api/v3/myTrades"
        assert captured["params"] == {
            "symbol": "BTCUSDT",
            "orderId": 12345,
        }
    finally:
        client.close()
