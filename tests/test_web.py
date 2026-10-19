"""Pruebas para el dashboard Web Flask y sus endpoints REST."""

from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone
import pytest

from app.dashboard.collector import DashboardData
from app.engine import EngineCycleResult
from app.strategies.registry import BotRegistry
from app.web.app import create_app


@pytest.fixture
def test_registry(tmp_path: Path) -> BotRegistry:
    return BotRegistry(storage_path=tmp_path / "test_bots.json")


@pytest.fixture
def mock_collector() -> MagicMock:
    collector = MagicMock()
    mock_data = DashboardData(
        timestamp=datetime.now(timezone.utc),
        db_connected=True,
        binance_connected=True,
        binance_balances=[{"asset": "USDT", "free": "95.50", "locked": "0.0"}],
        risk_summary={
            "trading_allowed": True,
            "daily_pnl_eur": Decimal("0.50"),
            "total_pnl_eur": Decimal("1.20"),
            "max_daily_loss_eur": Decimal("2.00"),
            "max_total_loss_eur": Decimal("5.00"),
            "max_capital_eur": Decimal("100.00"),
            "max_allocation_per_strategy_eur": Decimal("20.00"),
            "open_positions_count": 0,
        },
        open_positions=[],
        recent_closed_positions=[],
        recent_orders=[],
        active_strategies=[],
    )
    collector.collect.return_value = mock_data
    return collector


@pytest.fixture
def client(test_registry: BotRegistry, mock_collector: MagicMock):
    app = create_app(registry=test_registry, collector=mock_collector)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_index_route_renders_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Multi-Bot Trading Dashboard" in resp.data
    assert b"Ecosistema de Bots" in resp.data
    assert b"ema_cross_9_21" in resp.data


def test_api_status_endpoint(client):
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["binance_connected"] is True
    assert data["db_connected"] is True
    assert len(data["bots"]) == 4
    assert data["risk_summary"]["trading_allowed"] is True
    assert data["risk_summary"]["daily_pnl_eur"] == 0.50


def test_api_toggle_bot(client, test_registry: BotRegistry):
    bot_key = "ema_cross_9_21"
    assert test_registry.get_bot(bot_key).is_active is True

    # 1. Toggle off
    resp = client.post(f"/api/bots/{bot_key}/toggle")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["key"] == bot_key
    assert data["is_active"] is False
    assert test_registry.get_bot(bot_key).is_active is False

    # 2. Toggle on
    resp = client.post(f"/api/bots/{bot_key}/toggle")
    assert resp.status_code == 200
    assert resp.get_json()["is_active"] is True
    assert test_registry.get_bot(bot_key).is_active is True


def test_api_toggle_nonexistent_bot(client):
    resp = client.post("/api/bots/nonexistent_bot_key/toggle")
    assert resp.status_code == 404
    data = resp.get_json()
    assert data["success"] is False
    assert "no encontrado" in data["error"]


def test_api_create_bot_success(client, test_registry: BotRegistry):
    payload = {
        "name": "Bot RSI Agresivo",
        "strategy_type": "rsi",
        "symbol": "BTCUSDT",
        "allocation_eur": 18.5,
        "parameters": {
            "period": 10,
            "oversold": 20,
            "overbought": 80,
        },
    }

    resp = client.post("/api/bots", json=payload)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["bot"]["name"] == "Bot RSI Agresivo"
    assert data["bot"]["strategy_type"] == "rsi"
    assert data["bot"]["is_active"] is True

    # Comprobar que existe en el registry
    created_key = data["bot"]["key"]
    assert test_registry.get_bot(created_key) is not None


def test_api_create_bot_validation_error(client):
    # Nombre vacío
    payload = {
        "name": "",
        "strategy_type": "rsi",
        "symbol": "BTCUSDT",
        "allocation_eur": 20.0,
    }
    resp = client.post("/api/bots", json=payload)
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["success"] is False


def test_api_delete_bot(client, test_registry: BotRegistry):
    bot_key = "macd_12_26_9"
    assert test_registry.get_bot(bot_key) is not None

    resp = client.delete(f"/api/bots/{bot_key}")
    assert resp.status_code == 200
    assert resp.get_json()["success"] is True
    assert test_registry.get_bot(bot_key) is None

    # Eliminar inexistente -> 404
    resp_again = client.delete(f"/api/bots/{bot_key}")
    assert resp_again.status_code == 404


def test_api_run_engine_cycle_success(client):
    mock_result = EngineCycleResult(
        timestamp=1000.0,
        candles_saved={"BTCUSDT": 100},
        exit_results=[],
        signals_generated={"BTCUSDT": {"ema_cross": []}},
        execution_results=[],
    )

    with patch("app.web.app.build_default_engine") as mock_build:
        mock_engine = MagicMock()
        mock_engine.run_once.return_value = mock_result
        mock_build.return_value = mock_engine

        resp = client.post("/api/engine/run-cycle")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["candles_saved"] == {"BTCUSDT": 100}
        assert data["exits_count"] == 0
        assert data["orders_count"] == 0


def test_api_run_engine_cycle_no_active_bots(client, test_registry: BotRegistry):
    # Desactivar todos los bots
    for b in test_registry.list_bots():
        test_registry.set_bot_active(b.key, False)

    resp = client.post("/api/engine/run-cycle")
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["success"] is False
    assert "No hay ningún bot activo" in data["error"]


def test_api_symbols_endpoint(client):
    resp = client.get("/api/symbols")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert isinstance(data["symbols"], list)
    assert len(data["symbols"]) > 0
    assert "BTCUSDT" in data["symbols"]


def test_index_renders_symbol_select_dropdown(client):
    resp = client.get("/")
    assert resp.status_code == 200
    # Comprobar que existe el elemento select y opciones
    assert b'<select id="bot-symbol" name="symbol"' in resp.data
    assert b'<option value="BTCUSDT"' in resp.data

