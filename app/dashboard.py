import argparse
import logging
import re
import sys
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any

from flask import Flask, jsonify, render_template, request
from sqlalchemy import func, select

from app.storage.database import SessionLocal
from app.storage.models import (
    BotRun,
    MarketCandle,
    RiskSnapshot,
    TradeOrder,
    TradePosition,
)

LOGGER = logging.getLogger(__name__)
DEFAULT_SYMBOL = "BTCUSDT"
DEFAULT_INTERVAL = "1m"
MAX_CHART_CANDLES = 120
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9]{5,30}$")
INTERVAL_PATTERN = re.compile(r"^(1|3|5|15|30)m$|^(1|2|4|6|8|12)h$|^(1|3)d$|^1w$")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _decimal(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def load_dashboard_data(
    *,
    symbol: str = DEFAULT_SYMBOL,
    interval: str = DEFAULT_INTERVAL,
) -> dict[str, Any]:
    symbol = symbol.upper()
    if not SYMBOL_PATTERN.fullmatch(symbol):
        raise ValueError("El símbolo solicitado no es válido.")
    if not INTERVAL_PATTERN.fullmatch(interval):
        raise ValueError("El intervalo solicitado no es válido.")

    with SessionLocal() as session:
        open_positions = session.scalars(
            select(TradePosition)
            .where(TradePosition.status == "open")
            .order_by(TradePosition.opened_at.desc())
            .limit(100)
        ).all()
        open_position_count = session.scalar(
            select(func.count())
            .select_from(TradePosition)
            .where(TradePosition.status == "open")
        )
        realized_pnl = session.scalar(
            select(func.coalesce(func.sum(TradePosition.realized_pnl), 0))
        )
        recent_orders = session.scalars(
            select(TradeOrder)
            .order_by(TradeOrder.submitted_at.desc(), TradeOrder.id.desc())
            .limit(20)
        ).all()
        recent_runs = session.scalars(
            select(BotRun)
            .order_by(BotRun.started_at.desc(), BotRun.id.desc())
            .limit(10)
        ).all()
        risk_snapshot = session.scalar(
            select(RiskSnapshot)
            .order_by(RiskSnapshot.recorded_at.desc(), RiskSnapshot.id.desc())
            .limit(1)
        )
        candles = session.scalars(
            select(MarketCandle)
            .where(
                MarketCandle.symbol == symbol,
                MarketCandle.interval == interval,
                MarketCandle.is_closed.is_(True),
            )
            .order_by(MarketCandle.open_time.desc())
            .limit(MAX_CHART_CANDLES)
        ).all()
        candles.reverse()

    latest_price = candles[-1].close if candles else None
    return {
        "updated_at": datetime.now().astimezone().isoformat(),
        "market": {
            "symbol": symbol,
            "interval": interval,
            "latest_price": _decimal(latest_price),
            "latest_candle_at": _iso(candles[-1].close_time) if candles else None,
            "candles": [
                {
                    "time": _iso(candle.close_time),
                    "open": _decimal(candle.open),
                    "high": _decimal(candle.high),
                    "low": _decimal(candle.low),
                    "close": _decimal(candle.close),
                    "volume": _decimal(candle.volume),
                }
                for candle in candles
            ],
        },
        "summary": {
            "open_positions": int(open_position_count or 0),
            "realized_pnl_eur": _decimal(realized_pnl) or "0",
            "latest_run_status": recent_runs[0].status if recent_runs else None,
            "latest_run_at": _iso(recent_runs[0].started_at) if recent_runs else None,
            "risk_allowed": risk_snapshot.risk_allowed if risk_snapshot else None,
            "daily_pnl_eur": (
                _decimal(risk_snapshot.daily_pnl_eur) if risk_snapshot else None
            ),
        },
        "positions": [
            {
                "id": position.id,
                "symbol": position.symbol,
                "side": position.side,
                "quantity": _decimal(position.quantity),
                "entry_price": _decimal(position.entry_price),
                "realized_pnl": _decimal(position.realized_pnl),
                "opened_at": _iso(position.opened_at),
            }
            for position in open_positions
        ],
        "orders": [
            {
                "id": order.id,
                "symbol": order.symbol,
                "side": order.side,
                "order_type": order.order_type,
                "status": order.status,
                "quantity": _decimal(order.quantity),
                "submitted_at": _iso(order.submitted_at),
            }
            for order in recent_orders
        ],
        "runs": [
            {
                "id": run.id,
                "mode": run.mode,
                "status": run.status,
                "started_at": _iso(run.started_at),
                "finished_at": _iso(run.finished_at),
            }
            for run in recent_runs
        ],
    }


def create_app(
    data_loader: Callable[..., dict[str, Any]] = load_dashboard_data,
) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")

    @app.get("/")
    def dashboard():
        return render_template(
            "dashboard.html",
            symbol=DEFAULT_SYMBOL,
            interval=DEFAULT_INTERVAL,
        )

    @app.get("/api/dashboard")
    def dashboard_data():
        symbol = request.args.get("symbol", DEFAULT_SYMBOL)
        interval = request.args.get("interval", DEFAULT_INTERVAL)
        try:
            response = jsonify(data_loader(symbol=symbol, interval=interval))
            response.headers["Cache-Control"] = "no-store"
            return response
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception:
            LOGGER.exception("No se pudieron cargar los datos del dashboard.")
            return jsonify({"error": "No se pudieron cargar los datos."}), 500

    return app


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dashboard de solo lectura para el bot."
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help=(
            "Interfaz de escucha. Usa 0.0.0.0 para permitir acceso desde la LAN; "
            "no expongas este dashboard sin autenticación a Internet."
        ),
    )
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port debe estar entre 1 y 65535.")
    if not args.host.strip():
        parser.error("--host no puede estar vacío.")
    if args.host == "0.0.0.0":
        LOGGER.warning(
            "Escuchando en todas las interfaces sin autenticación. "
            "Limita el acceso con el firewall a tu red de confianza."
        )

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    create_app().run(
        host=args.host,
        port=args.port,
        debug=False,
        use_reloader=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
