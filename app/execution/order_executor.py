"""Motor de ejecución: envía órdenes a Binance y gestiona su ciclo de vida."""

import logging
from decimal import Decimal

from app.market_data.binance_client import BinanceMarketClient
from app.risk.persistent_manager import PersistentRiskManager, RiskRejected

logger = logging.getLogger(__name__)


class OrderExecutionError(Exception):
    """Error durante la ejecución de una orden en Binance."""


class OrderExecutor:
    """Envía órdenes a Binance y gestiona el ciclo completo:
    reserva → envío → reconciliación → posición.
    """

    def __init__(
        self,
        client: BinanceMarketClient,
        risk: PersistentRiskManager,
    ):
        self.client = client
        self.risk = risk

    def get_current_price(self, symbol: str) -> Decimal:
        """Obtiene el precio actual de un par en Binance."""
        data = self.client.get_price(symbol)
        price = Decimal(str(data["price"]))

        if not price.is_finite() or price <= 0:
            raise OrderExecutionError(
                f"Precio inválido para {symbol}: {data['price']}"
            )

        return price

    def execute_market_buy(
        self,
        order_id: int,
        symbol: str,
        quantity: Decimal,
        client_order_id: str | None = None,
    ) -> int:
        """Ejecuta una compra MARKET en Binance.

        Ciclo completo:
        1. Envía la orden a Binance
        2. Marca como enviada en nuestro sistema
        3. Obtiene y reconcilia las ejecuciones (fills)
        4. Convierte a posición abierta

        Devuelve el ID de la posición creada.
        Libera la reserva si Binance rechaza la orden.
        """

        # 1. Enviar orden a Binance
        try:
            response = self.client.place_order(
                symbol=symbol,
                side="BUY",
                order_type="MARKET",
                quantity=str(quantity),
                client_order_id=client_order_id,
            )
        except Exception as exc:
            logger.error("Error al enviar orden a Binance: %s", exc)
            self.risk.release_order(order_id, status="rejected")
            raise OrderExecutionError(
                f"Binance rechazó la orden: {exc}"
            ) from exc

        exchange_order_id = str(response["orderId"])

        # 2. Marcar como enviada
        self.risk.mark_submitted(order_id, exchange_order_id)

        # 3. Obtener estado y ejecuciones de Binance
        exchange_order = self.client.get_order(
            symbol, exchange_order_id,
        )
        exchange_trades = self.client.get_my_trades(
            symbol, int(exchange_order_id),
        )

        # 4. Reconciliar ejecuciones
        self.risk.reconcile_fills(
            order_id, exchange_order, exchange_trades,
        )

        # 5. Si completada, convertir a posición
        if exchange_order["status"] == "FILLED":
            position_id = self.risk.convert_to_position(
                order_id,
                quantity=quantity,
                entry_price=Decimal("0"),
            )

            logger.info(
                "Orden %d ejecutada → posición %d (%s %s)",
                order_id,
                position_id,
                quantity,
                symbol,
            )
            return position_id

        raise OrderExecutionError(
            f"La orden {order_id} no se ejecutó completamente. "
            f"Estado Binance: {exchange_order['status']}"
        )

    def execute_market_sell(
        self,
        symbol: str,
        quantity: Decimal,
        client_order_id: str | None = None,
    ) -> Decimal:
        """Ejecuta una venta MARKET en Binance para cerrar posición.
        Devuelve el precio medio de salida obtenido.
        """
        try:
            response = self.client.place_order(
                symbol=symbol,
                side="SELL",
                order_type="MARKET",
                quantity=str(quantity),
                client_order_id=client_order_id,
            )
        except Exception as exc:
            logger.error("Error al enviar orden SELL a Binance: %s", exc)
            raise OrderExecutionError(
                f"Binance rechazó la orden de venta: {exc}"
            ) from exc

        exchange_order_id = str(response["orderId"])

        # Consultar trades para obtener precio real de ejecución
        trades = self.client.get_my_trades(symbol, int(exchange_order_id))
        if trades:
            total_qty = sum(Decimal(str(t["qty"])) for t in trades)
            total_quote = sum(
                Decimal(str(t["qty"])) * Decimal(str(t["price"]))
                for t in trades
            )
            if total_qty > 0:
                avg_price = total_quote / total_qty
                logger.info(
                    "Venta completada para %s %s: precio medio=%s",
                    quantity,
                    symbol,
                    avg_price,
                )
                return avg_price

        # Fallback a detalles de orden
        order_info = self.client.get_order(symbol, exchange_order_id)
        executed_qty = Decimal(str(order_info.get("executedQty", "0")))
        cummulative_quote = Decimal(str(order_info.get("cummulativeQuoteQty", "0")))
        if executed_qty > 0 and cummulative_quote > 0:
            avg_price = cummulative_quote / executed_qty
            logger.info(
                "Venta completada para %s %s: precio medio=%s (desde order)",
                quantity,
                symbol,
                avg_price,
            )
            return avg_price

        fallback_price = self.get_current_price(symbol)
        logger.warning(
            "No se pudieron calcular fills para orden %s; usando precio actual: %s",
            exchange_order_id,
            fallback_price,
        )
        return fallback_price

