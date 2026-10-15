
import hashlib
import hmac
import os
import time
from urllib.parse import urlencode

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
        self.api_key = os.getenv("BINANCE_API_KEY", "")
        self.api_secret = os.getenv("BINANCE_API_SECRET", "")
        self.client = httpx.Client(
            base_url=self.base_url,
            timeout=10.0,
            headers={"X-MBX-APIKEY": self.api_key},
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

    def _signed_get(self, path: str, params: dict | None = None) -> dict:
        if not self.api_key or not self.api_secret:
            raise RuntimeError("Faltan las credenciales de Binance Testnet")

        payload = dict(params or {})
        payload["timestamp"] = int(time.time() * 1000)
        payload["recvWindow"] = 5000

        query = urlencode(payload)
        signature = hmac.new(
            self.api_secret.encode(),
            query.encode(),
            hashlib.sha256,
        ).hexdigest()

        response = self.client.get(
            path,
            params={**payload, "signature": signature},
        )
        response.raise_for_status()
        return response.json()

    def get_account(self) -> dict:
        return self._signed_get("/api/v3/account")

    def get_balances(self) -> list[dict]:
        account = self.get_account()
        return [
            balance
            for balance in account["balances"]
            if float(balance["free"]) > 0
            or float(balance["locked"]) > 0
        ]

    def close(self):
        self.client.close()

