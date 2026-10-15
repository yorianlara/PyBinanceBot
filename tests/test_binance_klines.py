
from unittest.mock import Mock

import pytest

from app.market_data.binance_client import BinanceMarketClient


def test_get_klines_returns_candles():
    client = BinanceMarketClient()
    response = Mock()
    response.json.return_value = [[
        1760000000000, "100", "110", "90", "105", "12",
        1760000059999, "1200", 10, "5", "500", "0",
    ]]
    response.raise_for_status.return_value = None
    client.client.get = Mock(return_value=response)

    try:
        candles = client.get_klines("BTCUSDT", "1m", 1)

        assert len(candles) == 1
        assert candles[0][4] == "105"
        client.client.get.assert_called_once()
    finally:
        client.close()


def test_get_klines_rejects_invalid_limit():
    client = BinanceMarketClient()

    try:
        with pytest.raises(ValueError):
            client.get_klines(limit=0)
    finally:
        client.close()
