import hashlib
import hmac
import os
import time
from decimal import Decimal
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
        self._exchange_info_cache: dict | None = None

    # ------------------------------------------------------------------
    # Públicos
    # ------------------------------------------------------------------
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

    def get_exchange_info(self) -> dict:
        """Devuelve exchangeInfo completo. Cachea en memoria."""
        if self._exchange_info_cache is None:
            response = self.client.get("/api/v3/exchangeInfo")
            response.raise_for_status()
            self._exchange_info_cache = response.json()
        return self._exchange_info_cache

    def get_symbol_info(self, symbol: str) -> dict:
        """Bloque del símbolo con sus filtros (LOT_SIZE, NOTIONAL, etc.)."""
        info = self.get_exchange_info()
        target = symbol.upper()
        for entry in info.get("symbols", []):
            if entry.get("symbol") == target:
                return entry
        raise ValueError(f"Símbolo {target} no encontrado en Binance")

    # ------------------------------------------------------------------
    # Firmados — GET
    # ------------------------------------------------------------------
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
        return self._signed_get(
            "/api/v3/myTrades",
            {
                "symbol": symbol.upper(),
                "orderId": order_id,
            },
        )

    # ------------------------------------------------------------------
    # Firmados — POST / DELETE
    # ------------------------------------------------------------------
    def _signed_post(self, path: str, params: dict | None = None) -> dict:
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

        response = self.client.post(
            path,
            params={**payload, "signature": signature},
        )
        response.raise_for_status()
        return response.json()

    def _signed_delete(self, path: str, params: dict | None = None) -> dict:
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

        response = self.client.delete(
            path,
            params={**payload, "signature": signature},
        )
        response.raise_for_status()
        return response.json()

    # ------------------------------------------------------------------
    # Órdenes
    # ------------------------------------------------------------------
    def create_order(
        self,
        *,
        symbol: str,
        side: str,
        type: str,
        quantity: Decimal | str | None = None,
        quote_order_qty: Decimal | str | None = None,
        price: Decimal | str | None = None,
        time_in_force: str | None = None,
        new_client_order_id: str | None = None,
    ) -> dict:
        """
        Crea una orden. Para MARKET usa `quantity` (el RiskManager valida
        cantidad exacta contra fills). `quote_order_qty` se acepta por
        completitud pero NO se usa en el flujo del signal_router.
        """
        params: dict = {
            "symbol": symbol.upper(),
            "side": side.upper(),
            "type": type.upper(),
        }

        if quantity is not None:
            params["quantity"] = str(quantity)
        if quote_order_qty is not None:
            params["quoteOrderQty"] = str(quote_order_qty)
        if price is not None:
            params["price"] = str(price)
        if time_in_force is not None:
            params["timeInForce"] = time_in_force.upper()
        if new_client_order_id is not None:
            if len(new_client_order_id) > 36:
                raise ValueError(
                    "newClientOrderId supera 36 caracteres (límite Binance)."
                )
            params["newClientOrderId"] = new_client_order_id

        return self._signed_post("/api/v3/order", params)

    def cancel_order(self, symbol: str, order_id: int | str) -> dict:
        return self._signed_delete(
            "/api/v3/order",
            {"symbol": symbol.upper(), "orderId": order_id},
        )

    # ------------------------------------------------------------------
    def close(self):
        self.client.close()