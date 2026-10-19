"""Recolector de métricas y datos en tiempo real para el Dashboard."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
import logging

from app.execution.exit_config import DEFAULT_EXIT_CONFIG, ExitConfig
from app.market_data.binance_client import BinanceMarketClient
from app.risk.config import DEFAULT_RISK_CONFIG
from app.risk.persistent_manager import PersistentRiskManager
from app.storage.database import check_connection
from app.storage.repository import (
    get_open_positions,
    get_recent_orders,
    get_recent_positions,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DashboardPosition:
    """Detalle de una posición enriquecida con precios en vivo."""

    id: int
    symbol: str
    status: str
    side: str
    quantity: Decimal
    entry_price: Decimal
    current_price: Decimal | None
    unrealized_pnl_eur: Decimal | None
    unrealized_pnl_pct: Decimal | None
    stop_loss_price: Decimal
    take_profit_price: Decimal
    opened_at: datetime
    age_hours: Decimal


@dataclass(frozen=True)
class DashboardData:
    """Estructura agregada de datos para el Dashboard."""

    timestamp: datetime
    db_connected: bool
    binance_connected: bool
    risk_summary: dict
    open_positions: list[DashboardPosition]
    recent_closed_positions: list[dict]
    recent_orders: list[dict]
    binance_balances: list[dict]
    active_strategies: list[dict]


def collect_dashboard_data(
    binance_client: BinanceMarketClient | None = None,
    risk_manager: PersistentRiskManager | None = None,
    exit_config: ExitConfig | None = None,
    eur_to_usdt: Decimal = Decimal("1.08"),
) -> DashboardData:
    """Recopila el estado completo del sistema: riesgo, posiciones, órdenes y Binance."""
    now = datetime.now(timezone.utc)
    config_exit = exit_config or DEFAULT_EXIT_CONFIG
    db_ok = check_connection()

    # 1. Conexión y saldos de Binance
    binance_ok = False
    binance_balances: list[dict] = []
    prices_cache: dict[str, Decimal] = {}
    client = binance_client or BinanceMarketClient()
    should_close_client = binance_client is None

    try:
        if client.ping():
            binance_ok = True
            try:
                binance_balances = client.get_balances()
            except Exception:
                binance_balances = []
    except Exception as exc:
        logger.debug("Binance no disponible: %s", exc)
        binance_ok = False

    # 2. Resumen del Gestor de Riesgo
    risk_mgr = risk_manager or PersistentRiskManager(config=DEFAULT_RISK_CONFIG)
    risk_summary = {
        "max_capital_eur": Decimal(str(DEFAULT_RISK_CONFIG.max_capital_eur)),
        "max_allocation_per_strategy_eur": Decimal(
            str(DEFAULT_RISK_CONFIG.max_allocation_per_strategy_eur)
        ),
        "max_open_positions": DEFAULT_RISK_CONFIG.max_open_positions,
        "daily_pnl_eur": Decimal("0"),
        "total_pnl_eur": Decimal("0"),
        "max_daily_loss_eur": Decimal(str(DEFAULT_RISK_CONFIG.max_daily_loss_eur)),
        "max_total_loss_eur": Decimal(str(DEFAULT_RISK_CONFIG.max_total_loss_eur)),
        "active_allocations": 0,
        "total_allocated_eur": Decimal("0"),
        "trading_allowed": True,
        "allocations": [],
    }

    if db_ok:
        try:
            risk_summary = risk_mgr.get_account_summary()
        except Exception as exc:
            logger.error("Error obteniendo resumen de riesgo: %s", exc)

    # 3. Posiciones abiertas enriquecidas con precios en vivo
    open_pos_list: list[DashboardPosition] = []
    recent_closed: list[dict] = []
    recent_orders_list: list[dict] = []

    if db_ok:
        try:
            positions = get_open_positions()
            for pos in positions:
                # Obtener precio actual
                curr_price: Decimal | None = None
                if binance_ok:
                    if pos.symbol not in prices_cache:
                        try:
                            p_info = client.get_price(pos.symbol)
                            prices_cache[pos.symbol] = Decimal(str(p_info["price"]))
                        except Exception:
                            prices_cache[pos.symbol] = pos.entry_price
                    curr_price = prices_cache.get(pos.symbol)

                # Calcular PnL flotante
                unrealized_pnl_eur: Decimal | None = None
                unrealized_pnl_pct: Decimal | None = None
                if curr_price is not None and curr_price > 0 and pos.entry_price > 0:
                    diff_usdt = (curr_price - pos.entry_price) * pos.quantity
                    unrealized_pnl_eur = (diff_usdt / eur_to_usdt).quantize(Decimal("0.01"))
                    unrealized_pnl_pct = (
                        (curr_price - pos.entry_price) / pos.entry_price * Decimal("100")
                    ).quantize(Decimal("0.01"))

                # Niveles de Stop-Loss y Take-Profit
                sl_factor = Decimal("1") - (abs(config_exit.stop_loss_pct) / Decimal("100"))
                tp_factor = Decimal("1") + (abs(config_exit.take_profit_pct) / Decimal("100"))
                sl_price = (pos.entry_price * sl_factor).quantize(Decimal("0.01"))
                tp_price = (pos.entry_price * tp_factor).quantize(Decimal("0.01"))

                # Antigüedad de la posición
                pos_time = pos.opened_at
                if pos_time.tzinfo is None:
                    pos_time = pos_time.replace(tzinfo=timezone.utc)
                age_hrs = (
                    Decimal(str((now - pos_time).total_seconds())) / Decimal("3600")
                ).quantize(Decimal("0.1"))

                open_pos_list.append(
                    DashboardPosition(
                        id=pos.id,
                        symbol=pos.symbol,
                        status=pos.status,
                        side=pos.side,
                        quantity=pos.quantity,
                        entry_price=pos.entry_price,
                        current_price=curr_price,
                        unrealized_pnl_eur=unrealized_pnl_eur,
                        unrealized_pnl_pct=unrealized_pnl_pct,
                        stop_loss_price=sl_price,
                        take_profit_price=tp_price,
                        opened_at=pos_time,
                        age_hours=age_hrs,
                    )
                )

            # Posiciones recientes cerradas
            all_recent = get_recent_positions(limit=8)
            for p in all_recent:
                if p.status == "closed":
                    recent_closed.append(
                        {
                            "id": p.id,
                            "symbol": p.symbol,
                            "quantity": p.quantity,
                            "entry_price": p.entry_price,
                            "exit_price": p.exit_price,
                            "realized_pnl": p.realized_pnl,
                            "opened_at": p.opened_at,
                            "closed_at": p.closed_at,
                        }
                    )

            # Órdenes recientes
            orders = get_recent_orders(limit=8)
            for o in orders:
                recent_orders_list.append(
                    {
                        "id": o.id,
                        "symbol": o.symbol,
                        "side": o.side,
                        "order_type": o.order_type,
                        "quantity": o.quantity,
                        "status": o.status,
                        "submitted_at": o.submitted_at,
                        "exchange_order_id": o.exchange_order_id,
                    }
                )
        except Exception as exc:
            logger.error("Error consultando datos de almacenamiento: %s", exc)

    if should_close_client:
        try:
            client.close()
        except Exception:
            pass

    # 4. Estrategias configuradas
    active_strategies = [
        {
            "name": "EMA Cross (9/21)",
            "type": "Tendencia",
            "desc": "Detecta cruces de media móvil rápida vs lenta",
            "max_alloc_eur": Decimal("20.00"),
        },
        {
            "name": "RSI Reversión (14)",
            "type": "Reversión",
            "desc": "Opera rebotes en sobreventa (<30) y sobrecompra (>70)",
            "max_alloc_eur": Decimal("20.00"),
        },
        {
            "name": "Bollinger Breakout (20/2.0)",
            "type": "Ruptura",
            "desc": "Aprovecha rupturas de volatilidad sobre la banda superior",
            "max_alloc_eur": Decimal("20.00"),
        },
        {
            "name": "MACD Impulso (12/26/9)",
            "type": "Momento",
            "desc": "Sigue la aceleración de tendencia y cruces de señal",
            "max_alloc_eur": Decimal("20.00"),
        },
    ]

    return DashboardData(
        timestamp=now,
        db_connected=db_ok,
        binance_connected=binance_ok,
        risk_summary=risk_summary,
        open_positions=open_pos_list,
        recent_closed_positions=recent_closed,
        recent_orders=recent_orders_list,
        binance_balances=binance_balances,
        active_strategies=active_strategies,
    )

