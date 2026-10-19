from decimal import ROUND_CEILING, ROUND_DOWN, Decimal, InvalidOperation
from typing import Protocol
from uuid import uuid4

import httpx

from app.execution.models import ExecutionResult
from app.market_data.binance_client import BinanceMarketClient
from app.risk.persistent_manager import PersistentRiskManager, RiskRejected
from app.risk.signal_evaluator import evaluate_signals
from app.strategies.base import StrategySignal

BINANCE_SPOT_TESTNET_URL = "https://testnet.binance.vision"


class ExecutionClient(Protocol):
    base_url: str

    def get_symbol_info(self, symbol: str) -> dict: ...

    def get_price(self, symbol: str) -> dict: ...

    def get_balances(self) -> list[dict]: ...

    def create_order(self, **kwargs) -> dict: ...

    def cancel_order(self, symbol: str, order_id: int | str) -> dict: ...

    def get_order(self, symbol: str, order_id: int | str) -> dict: ...

    def get_my_trades(self, symbol: str, order_id: int) -> list[dict]: ...


class RiskController(Protocol):
    def reserve_order(self, **kwargs) -> int: ...

    def mark_submitted(self, order_id: int, exchange_order_id: str) -> None: ...

    def reconcile_fills(
        self,
        order_id: int,
        exchange_order: dict,
        exchange_trades: list[dict],
    ) -> None: ...

    def convert_to_position(
        self,
        order_id: int,
        *,
        quantity: Decimal,
        entry_price: Decimal,
        base_asset: str | None = None,
        quote_asset: str | None = None,
    ) -> int: ...


class ExitManagerController(Protocol):
    def close_for_signal(
        self,
        *,
        strategy_key: str,
        symbol: str,
        bot_run_id: int | None = None,
    ) -> ExecutionResult: ...


class SignalRouter:
    def __init__(
        self,
        client: ExecutionClient | None = None,
        risk_manager: RiskController | None = None,
        quote_budget_usdt: Decimal = Decimal(20),
        exit_manager: ExitManagerController | None = None,
    ):
        if not quote_budget_usdt.is_finite() or quote_budget_usdt <= 0:
            raise ValueError("El presupuesto USDT debe ser positivo y finito.")

        self.client = client or BinanceMarketClient(base_url=BINANCE_SPOT_TESTNET_URL)
        if self.client.base_url.rstrip("/") != BINANCE_SPOT_TESTNET_URL:
            raise ValueError("La ejecución solo está habilitada en Spot Testnet.")

        self.risk_manager = risk_manager or PersistentRiskManager()
        self.quote_budget_usdt = quote_budget_usdt
        if exit_manager is None:
            from app.execution.exit_manager import PositionExitManager

            exit_manager = PositionExitManager(
                client=self.client,
                risk_manager=self.risk_manager,
            )
        self.exit_manager = exit_manager

    def route_signals(
        self,
        signals_by_strategy: dict[str, list[StrategySignal]],
        bot_run_id: int | None = None,
    ) -> list[ExecutionResult]:
        results: list[ExecutionResult] = []
        decisions = evaluate_signals(signals_by_strategy)
        sides_by_symbol: dict[str, set[str]] = {}
        for decision in decisions:
            if decision.status in {"candidate", "exit_candidate"}:
                sides_by_symbol.setdefault(decision.symbol.upper(), set()).add(
                    decision.side
                )

        for decision in decisions:
            if len(sides_by_symbol.get(decision.symbol.upper(), set())) > 1:
                results.append(
                    ExecutionResult(
                        strategy_key=decision.strategy_key,
                        symbol=decision.symbol,
                        status="rejected",
                        reason=(
                            "Hay señales BUY y SELL simultáneas para este "
                            "símbolo; no se envió ninguna orden."
                        ),
                    )
                )
                continue

            if decision.status == "exit_candidate":
                results.append(
                    self.exit_manager.close_for_signal(
                        strategy_key=decision.strategy_key,
                        symbol=decision.symbol,
                        bot_run_id=bot_run_id,
                    )
                )
                continue

            if decision.status == "rejected":
                results.append(
                    ExecutionResult(
                        strategy_key=decision.strategy_key,
                        symbol=decision.symbol,
                        status="rejected",
                        reason=decision.reason,
                    )
                )
                continue

            results.append(
                self._execute_buy(
                    decision.strategy_key,
                    decision.symbol,
                    bot_run_id,
                )
            )

        return results

    def _execute_buy(
        self,
        strategy_key: str,
        symbol: str,
        bot_run_id: int | None,
    ) -> ExecutionResult:
        symbol = symbol.upper()
        symbol_info = self.client.get_symbol_info(symbol)

        if symbol_info.get("status") != "TRADING":
            raise ValueError(f"El símbolo {symbol} no está habilitado para trading.")
        if symbol_info.get("quoteAsset") != "USDT":
            raise ValueError("El presupuesto de esta fase solo admite pares USDT.")
        if not symbol_info.get("quoteOrderQtyMarketAllowed", False):
            raise ValueError(f"Binance no admite quoteOrderQty MARKET para {symbol}.")

        market_price = self._positive_decimal(
            self.client.get_price(symbol).get("price"),
            f"precio de {symbol}",
        )
        estimated_quantity = self._estimate_quantity(
            symbol_info,
            market_price,
        )
        min_notional = self._minimum_market_notional(symbol_info)
        if min_notional is not None and self.quote_budget_usdt < min_notional:
            raise ValueError(
                f"El presupuesto no alcanza el mínimo de {min_notional} USDT "
                f"para {symbol}."
            )

        eur_usdt_price = self._positive_decimal(
            self.client.get_price("EURUSDT").get("price"),
            "precio de EURUSDT",
        )
        allocation_eur = (self.quote_budget_usdt / eur_usdt_price).quantize(
            Decimal("0.01"), rounding=ROUND_CEILING
        )

        client_order_id = f"bot-{uuid4().hex}"
        try:
            order_id = self.risk_manager.reserve_order(
                strategy_key=strategy_key,
                symbol=symbol,
                side="buy",
                order_type="MARKET",
                quantity=estimated_quantity,
                quote_order_qty=self.quote_budget_usdt,
                allocation_eur=allocation_eur,
                bot_run_id=bot_run_id,
                client_order_id=client_order_id,
            )
        except RiskRejected as exc:
            return ExecutionResult(
                strategy_key=strategy_key,
                symbol=symbol,
                status="rejected",
                reason=str(exc),
            )

        try:
            order_response = self.client.create_order(
                symbol=symbol,
                side="BUY",
                type="MARKET",
                quote_order_qty=self.quote_budget_usdt,
                new_client_order_id=client_order_id,
            )
        except (httpx.HTTPError, RuntimeError) as exc:
            raise RuntimeError(
                f"No se pudo confirmar el envío de la orden {order_id} "
                f"(clientOrderId={client_order_id}); la reserva permanece "
                "activa. No reintentes sin reconciliar primero con Binance."
            ) from exc
        try:
            exchange_order_id = int(order_response["orderId"])
        except (InvalidOperation, KeyError, TypeError, ValueError):
            raise RuntimeError(
                "Binance respondió sin un orderId válido; la reserva de "
                f"riesgo {order_id} (clientOrderId={client_order_id}) se "
                "mantiene activa para evitar duplicados."
            ) from None

        self.risk_manager.mark_submitted(
            order_id,
            str(exchange_order_id),
        )

        exchange_order = self._fetch_and_reconcile(
            order_id,
            symbol,
            exchange_order_id,
        )
        status = exchange_order.get("status")
        if status == "PARTIALLY_FILLED":
            try:
                self.client.cancel_order(symbol, exchange_order_id)
            except (httpx.HTTPError, RuntimeError) as exc:
                raise RuntimeError(
                    f"No se pudo confirmar la cancelación de la orden "
                    f"{order_id} en Binance; la reserva permanece activa."
                ) from exc
            exchange_order = self._fetch_and_reconcile(
                order_id,
                symbol,
                exchange_order_id,
            )
            status = exchange_order.get("status")
            if status == "PARTIALLY_FILLED":
                raise RuntimeError(
                    f"La orden {order_id} sigue parcialmente ejecutada "
                    "después de solicitar su cancelación; requiere "
                    "reconciliación antes de continuar."
                )

        executed_quantity = Decimal(str(exchange_order["executedQty"]))
        if status == "FILLED":
            position_id = self.risk_manager.convert_to_position(
                order_id,
                quantity=estimated_quantity,
                entry_price=market_price,
                base_asset=symbol_info["baseAsset"],
                quote_asset=symbol_info["quoteAsset"],
            )
            return ExecutionResult(
                strategy_key=strategy_key,
                symbol=symbol,
                status="opened",
                reason="Orden MARKET ejecutada y fills reconciliados.",
                order_id=order_id,
                position_id=position_id,
            )
        if status == "PARTIALLY_FILLED":
            position_id = self.risk_manager.convert_to_position(
                order_id,
                quantity=estimated_quantity,
                entry_price=market_price,
                base_asset=symbol_info["baseAsset"],
                quote_asset=symbol_info["quoteAsset"],
            )
            return ExecutionResult(
                strategy_key=strategy_key,
                symbol=symbol,
                status="partially_filled",
                reason=(
                    "La cantidad ejecutada se registró como posición; "
                    "la reserva de riesgo permanece activa."
                ),
                order_id=order_id,
                position_id=position_id,
            )
        if status in {"CANCELED", "EXPIRED", "REJECTED"}:
            if executed_quantity > 0:
                position_id = self.risk_manager.convert_to_position(
                    order_id,
                    quantity=estimated_quantity,
                    entry_price=market_price,
                    base_asset=symbol_info["baseAsset"],
                    quote_asset=symbol_info["quoteAsset"],
                )
                return ExecutionResult(
                    strategy_key=strategy_key,
                    symbol=symbol,
                    status="partially_filled",
                    reason=(
                        "La orden terminó antes de completarse; los fills "
                        "confirmados se registraron como posición."
                    ),
                    order_id=order_id,
                    position_id=position_id,
                )
            return ExecutionResult(
                strategy_key=strategy_key,
                symbol=symbol,
                status="rejected",
                reason=f"Binance terminó la orden con estado {status}.",
                order_id=order_id,
            )
        if status == "NEW":
            return ExecutionResult(
                strategy_key=strategy_key,
                symbol=symbol,
                status="pending",
                reason="Orden enviada, todavía sin ejecuciones.",
                order_id=order_id,
            )

        raise RuntimeError(
            f"Estado de Binance no esperado tras enviar orden: {status!r}. "
            f"La reserva de riesgo {order_id} se mantiene activa."
        )

    def _fetch_and_reconcile(
        self,
        order_id: int,
        symbol: str,
        exchange_order_id: int,
    ) -> dict:
        try:
            exchange_order = self.client.get_order(symbol, exchange_order_id)
            exchange_trades = self.client.get_my_trades(
                symbol,
                exchange_order_id,
            )
            self.risk_manager.reconcile_fills(
                order_id,
                exchange_order,
                exchange_trades,
            )
        except (httpx.HTTPError, RiskRejected, RuntimeError) as exc:
            raise RuntimeError(
                f"No se pudo reconciliar la orden local {order_id} "
                f"(Binance {exchange_order_id}); la reserva permanece activa."
            ) from exc
        return exchange_order

    def _estimate_quantity(
        self,
        symbol_info: dict,
        market_price: Decimal,
    ) -> Decimal:
        filters = {
            item.get("filterType"): item for item in symbol_info.get("filters", [])
        }
        lot_filter = filters.get("MARKET_LOT_SIZE")
        if not self._has_positive_lot_limits(lot_filter):
            lot_filter = filters.get("LOT_SIZE")
        if lot_filter is None:
            raise ValueError("Binance no devolvió filtros de cantidad para el símbolo.")

        min_quantity = self._positive_decimal(
            lot_filter.get("minQty"),
            "cantidad mínima",
        )
        max_quantity = self._positive_decimal(
            lot_filter.get("maxQty"),
            "cantidad máxima",
        )
        step_size = self._positive_decimal(
            lot_filter.get("stepSize"),
            "incremento de cantidad",
        )

        raw_quantity = self.quote_budget_usdt / market_price
        steps = (raw_quantity / step_size).to_integral_value(rounding=ROUND_DOWN)
        quantity = steps * step_size
        if quantity < min_quantity or quantity > max_quantity:
            raise ValueError(
                f"La cantidad calculada {quantity} incumple los límites "
                f"de Binance para el símbolo."
            )
        return quantity

    @staticmethod
    def _has_positive_lot_limits(lot_filter: dict | None) -> bool:
        if lot_filter is None:
            return False
        try:
            values = [
                Decimal(str(lot_filter[key]))
                for key in ("minQty", "maxQty", "stepSize")
            ]
        except (KeyError, TypeError, ValueError):
            return False
        return all(value.is_finite() and value > 0 for value in values)

    @staticmethod
    def _minimum_market_notional(
        symbol_info: dict,
    ) -> Decimal | None:
        filters = {
            item.get("filterType"): item for item in symbol_info.get("filters", [])
        }
        for filter_type, apply_key in (
            ("NOTIONAL", "applyMinToMarket"),
            ("MIN_NOTIONAL", "applyToMarket"),
        ):
            item = filters.get(filter_type)
            if item is None or not item.get(apply_key, True):
                continue
            value = SignalRouter._positive_decimal(
                item.get("minNotional"),
                "notional mínimo",
            )
            return value
        return None

    @staticmethod
    def _positive_decimal(value: object, label: str) -> Decimal:
        try:
            result = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            raise ValueError(f"Binance devolvió un {label} inválido.") from None
        if not result.is_finite() or result <= 0:
            raise ValueError(f"Binance devolvió un {label} inválido.")
        return result
