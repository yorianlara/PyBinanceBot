"""Aplicación Flask para el Dashboard interactivo y gestión dinámica de bots."""

import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any

from flask import Flask, jsonify, render_template, request

from app.dashboard.collector import collect_dashboard_data
from app.engine import build_default_engine
from app.market_data.binance_client import BinanceMarketClient
from app.strategies.registry import BotRegistry

logger = logging.getLogger(__name__)

_CACHED_SYMBOLS: list[str] = []


def get_available_symbols() -> list[str]:
    """Obtiene y almacena en caché los símbolos disponibles para Spot en Binance."""
    global _CACHED_SYMBOLS
    if _CACHED_SYMBOLS:
        return _CACHED_SYMBOLS
    try:
        client = BinanceMarketClient()
        syms = client.get_exchange_symbols()
        if syms:
            _CACHED_SYMBOLS = syms
            return syms
    except Exception as exc:
        logger.warning("No se pudieron obtener símbolos de Binance: %s", exc)

    return [
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT",
        "ADAUSDT", "XRPUSDT", "DOGEUSDT", "AVAXUSDT",
        "DOTUSDT", "LINKUSDT", "MATICUSDT", "LTCUSDT",
    ]


def _json_serialize(obj: Any) -> Any:
    """Serializador seguro para Decimals y datetimes en respuestas JSON."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def create_app(
    bot_registry: BotRegistry | None = None,
    test_config: dict | None = None,
    registry: BotRegistry | None = None,
    collector: Any = None,
) -> Flask:
    """Fábrica de la aplicación Flask."""
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )

    if test_config:
        app.config.update(test_config)

    active_registry = registry or bot_registry or BotRegistry()

    def _get_data():
        if collector is not None:
            if hasattr(collector, "collect"):
                return collector.collect()
            if callable(collector):
                return collector()
            return collector
        return collect_dashboard_data()

    @app.route("/")
    def index():
        """Página principal del Dashboard con gestión de bots."""
        data = _get_data()
        bots = active_registry.list_bots()
        symbols = get_available_symbols()
        return render_template(
            "index.html",
            data=data,
            bots=bots,
            available_symbols=symbols,
        )

    @app.route("/api/symbols")
    def api_symbols():
        """Devuelve los pares de trading disponibles para Spot."""
        return jsonify({"success": True, "symbols": get_available_symbols()})

    @app.route("/api/status")
    def api_status():
        """Devuelve el estado en tiempo real en formato JSON para refresco automático."""
        data = _get_data()
        bots = [b.to_dict() for b in active_registry.list_bots()]

        payload = {
            "timestamp": data.timestamp.isoformat(),
            "db_connected": data.db_connected,
            "binance_connected": data.binance_connected,
            "risk_summary": {
                k: float(v) if isinstance(v, Decimal) else v
                for k, v in data.risk_summary.items()
                if k != "allocations"
            },
            "open_positions": [
                {
                    "id": p.id,
                    "symbol": p.symbol,
                    "quantity": float(p.quantity),
                    "entry_price": float(p.entry_price),
                    "current_price": float(p.current_price) if p.current_price else None,
                    "unrealized_pnl_eur": float(p.unrealized_pnl_eur) if p.unrealized_pnl_eur is not None else None,
                    "unrealized_pnl_pct": float(p.unrealized_pnl_pct) if p.unrealized_pnl_pct is not None else None,
                    "stop_loss_price": float(p.stop_loss_price),
                    "take_profit_price": float(p.take_profit_price),
                    "age_hours": float(p.age_hours),
                }
                for p in data.open_positions
            ],
            "bots": bots,
            "recent_closed_positions": [
                {
                    "id": p["id"],
                    "symbol": p["symbol"],
                    "quantity": float(p["quantity"]),
                    "entry_price": float(p["entry_price"]),
                    "exit_price": float(p.get("exit_price") or 0),
                    "realized_pnl": float(p.get("realized_pnl") or 0),
                }
                for p in data.recent_closed_positions[:5]
            ],
            "binance_balances": data.binance_balances[:6],
        }
        return jsonify(payload)

    @app.route("/api/bots/<key>/toggle", methods=["POST"])
    def toggle_bot(key: str):
        """Activa o desactiva un bot dinámicamente."""
        bot = active_registry.get_bot(key)
        if not bot:
            return jsonify({"success": False, "error": f"Bot '{key}' no encontrado"}), 404

        new_status = not bot.is_active
        updated = active_registry.set_bot_active(key, new_status)
        return jsonify({
            "success": True,
            "key": key,
            "is_active": updated.is_active,
            "name": updated.name,
        })

    @app.route("/api/bots", methods=["POST"])
    def create_bot_endpoint():
        """Crea un nuevo bot con su estrategia y parámetros personalizados."""
        try:
            req_data = request.get_json(silent=True) or request.form.to_dict()

            name = req_data.get("name", "").strip()
            stype = req_data.get("strategy_type", "").strip().lower()
            symbol = req_data.get("symbol", "BTCUSDT").strip().upper()
            alloc_eur = Decimal(str(req_data.get("allocation_eur", 20.0)))

            # Procesar parámetros específicos
            raw_params = req_data.get("parameters", {})
            if isinstance(raw_params, str):
                try:
                    raw_params = json.loads(raw_params)
                except Exception:
                    raw_params = {}

            # Extraer si vienen aplanados del formulario
            if stype == "ema_cross":
                params = {
                    "fast_period": int(req_data.get("fast_period", raw_params.get("fast_period", 9))),
                    "slow_period": int(req_data.get("slow_period", raw_params.get("slow_period", 21))),
                }
            elif stype == "rsi":
                params = {
                    "period": int(req_data.get("period", raw_params.get("period", 14))),
                    "oversold": float(req_data.get("oversold", raw_params.get("oversold", 30))),
                    "overbought": float(req_data.get("overbought", raw_params.get("overbought", 70))),
                }
            elif stype == "bollinger":
                params = {
                    "period": int(req_data.get("period", raw_params.get("period", 20))),
                    "std_dev_multiplier": float(req_data.get("std_dev_multiplier", raw_params.get("std_dev_multiplier", 2.0))),
                }
            elif stype == "macd":
                params = {
                    "fast_period": int(req_data.get("fast_period", raw_params.get("fast_period", 12))),
                    "slow_period": int(req_data.get("slow_period", raw_params.get("slow_period", 26))),
                    "signal_period": int(req_data.get("signal_period", raw_params.get("signal_period", 9))),
                }
            else:
                return jsonify({"success": False, "error": f"Tipo de estrategia '{stype}' no soportado"}), 400

            bot = active_registry.create_bot(
                name=name,
                strategy_type=stype,
                symbol=symbol,
                parameters=params,
                allocation_eur=alloc_eur,
            )

            return jsonify({"success": True, "bot": bot.to_dict()})

        except Exception as exc:
            logger.error("Error creando bot: %s", exc)
            return jsonify({"success": False, "error": str(exc)}), 400

    @app.route("/api/bots/<key>", methods=["DELETE"])
    def delete_bot_endpoint(key: str):
        """Elimina un bot del registro."""
        deleted = active_registry.delete_bot(key)
        if not deleted:
            return jsonify({"success": False, "error": f"Bot '{key}' no encontrado"}), 404
        return jsonify({"success": True, "key": key})

    @app.route("/api/engine/run-cycle", methods=["POST"])
    def run_engine_cycle():
        """Ejecuta un ciclo manual bajo demanda usando los bots activos."""
        try:
            active_strats = active_registry.get_active_strategies()
            if not active_strats:
                return jsonify({
                    "success": False,
                    "error": "No hay ningún bot activo para operar.",
                }), 400

            engine = build_default_engine()
            # Asignar únicamente las estrategias activas del registro
            engine.coordinator.strategies = active_strats

            result = engine.run_once()
            signals_count = sum(
                len(sigs)
                for s_dict in result.signals_generated.values()
                for sigs in s_dict.values()
            )

            return jsonify({
                "success": True,
                "active_bots_count": len(active_strats),
                "signals_count": signals_count,
                "candles_saved": result.candles_saved,
                "exits_count": len([e for e in result.exit_results if e.closed]),
                "orders_count": len([x for x in result.execution_results if x.status == "executed"]),
                "details": [
                    {
                        "strategy": r.strategy_key,
                        "symbol": r.signal.symbol,
                        "status": r.status,
                        "reason": r.reason,
                    }
                    for r in result.execution_results
                ],
            })
        except Exception as exc:
            logger.error("Error ejecutando ciclo manual: %s", exc)
            return jsonify({"success": False, "error": str(exc)}), 500

    return app