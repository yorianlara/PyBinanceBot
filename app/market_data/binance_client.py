
import os

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv(
    "BINANCE_BASE_URL",
    "https://testnet.binance.vision",
).rstrip("/")


class BinanceMarketClient:
    def __init__(self, base_url: str = BASE_URL):
        self.base_url = base_url
        self.client = httpx.Client(
            base_url=self.base_url,
            timeout=10.0,
        )

    def ping(self) -> bool:
        response = self.client.get("/api/v3/ping")
        response.raise_for_status()
        return response.json() == {}

    def get_price(self, symbol: str = "BTCUSDT") -> dict:
        response = self.client.get(
            "/api/v3/ticker/price",
            params={"symbol": symbol.upper()},
        )
        response.raise_for_status()
        return response.json()

    def close(self):
        self.client.close()
