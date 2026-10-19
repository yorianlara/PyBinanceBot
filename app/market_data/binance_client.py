
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

    def get_order(self, symbol: str, order_id: int | str) -> dict:
        return self._signed_get(
            "/api/v3/order",
            {
                "symbol": symbol.upper(),
                "orderId": order_id,
            },
        )

    def get_my_trades(
        self,
        symbol: str,
        order_id: int,
    ) -> list[dict]:
        trades = self._signed_get(
            "/api/v3/myTrades",
            {
                "symbol": symbol.upper(),
                "orderId": order_id,
            },
        )
        return trades

    def _signed_post(
        self,
        path: str,
        params: dict | None = None,
    ) -> dict:
        if not self.api_key or not self.api_secret:
            raise RuntimeError(
                "Faltan las credenciales de Binance Testnet"
            )

        payload = dict(params or {})
        payload["timestamp"] = int(time.time() * 1000)
        payload["recvWindow"] = 5000

        query = urlencode(payload)
        signature = hmac.new(
            self.api_secret.encode(),
            query.encode(),
            hashlib.sha256,
        ).hexdigest()

        response = self.client.post(
            path,
            params={**payload, "signature": signature},
        )
        response.raise_for_status()
        return response.json()

    def place_order(
        self,
        *,
        symbol: str,
        side: str,
        order_type: str,
        quantity: str,
        client_order_id: str | None = None,
    ) -> dict:
        params: dict = {
            "symbol": symbol.upper(),
            "side": side.upper(),
            "type": order_type.upper(),
            "quantity": quantity,
        }

        if client_order_id:
            params["newClientOrderId"] = client_order_id

        return self._signed_post("/api/v3/order", params)

    def get_klines(
        self,
        symbol: str = "BTCUSDT",
        interval: str = "1m",
        limit: int = 100,
    ) -> list[list]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit debe estar entre 1 y 1000.")

        response = self.client.get(
            "/api/v3/klines",
            params={
                "symbol": symbol.upper(),
                "interval": interval,
                "limit": limit,
            },
        )
        response.raise_for_status()
        return response.json()

    def get_exchange_symbols(self, quote_asset: str = "USDT") -> list[str]:
        """Obtiene la lista de pares de trading activos para Spot en Binance."""
        fallback = [
            "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT",
            "ADAUSDT", "XRPUSDT", "DOGEUSDT", "AVAXUSDT",
            "DOTUSDT", "LINKUSDT", "MATICUSDT", "LTCUSDT",
        ]
        try:
            response = self.client.get("/api/v3/exchangeInfo")
            response.raise_for_status()
            data = response.json()
            symbols = [
                s["symbol"]
                for s in data.get("symbols", [])
                if s.get("status") == "TRADING"
                and s.get("quoteAsset") == quote_asset.upper()
                and s.get("isSpotTradingAllowed", True)
            ]
            if not symbols:
                return fallback

            # Priorizar los pares más líquidos al inicio
            priority = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "ADAUSDT", "XRPUSDT"]
            ordered = [s for s in priority if s in symbols]
            remaining = sorted([s for s in symbols if s not in priority])
            return ordered + remaining
        except Exception:
            return fallback

    def close(self):
        self.client.close()

