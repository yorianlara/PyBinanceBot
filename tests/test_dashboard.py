import logging
import sys

from app import dashboard
from app.dashboard import create_app


def test_dashboard_page_renders_without_database_request():
    app = create_app()

    response = app.test_client().get("/")

    assert response.status_code == 200
    assert b"Resumen de trading" in response.data
    assert b"BINANCE SPOT TESTNET" in response.data
    assert b"/api/dashboard" in response.data


def test_dashboard_api_returns_read_only_data_for_requested_market():
    calls = []
    data = {
        "updated_at": "2026-10-19T17:00:00+01:00",
        "market": {"symbol": "ETHUSDT", "interval": "5m", "candles": []},
        "summary": {"open_positions": 0},
        "positions": [],
        "orders": [],
        "runs": [],
    }

    def loader(*, symbol, interval):
        calls.append((symbol, interval))
        return data

    response = create_app(data_loader=loader).test_client().get(
        "/api/dashboard?symbol=ETHUSDT&interval=5m"
    )

    assert response.status_code == 200
    assert response.json == data
    assert response.headers["Cache-Control"] == "no-store"
    assert calls == [("ETHUSDT", "5m")]


def test_dashboard_api_rejects_invalid_query_values():
    app = create_app()
    client = app.test_client()

    invalid_symbol = client.get("/api/dashboard?symbol=bad%20symbol")
    invalid_interval = client.get("/api/dashboard?interval=1M")

    assert invalid_symbol.status_code == 400
    assert invalid_interval.status_code == 400
    assert "error" in invalid_symbol.json
    assert "error" in invalid_interval.json


def test_dashboard_api_logs_internal_failure_without_exposing_details(caplog):
    def fail_loader(*, symbol, interval):
        raise RuntimeError("private database detail")

    app = create_app(data_loader=fail_loader)
    with caplog.at_level(logging.ERROR):
        response = app.test_client().get("/api/dashboard")

    assert response.status_code == 500
    assert response.json == {"error": "No se pudieron cargar los datos."}
    assert "private database detail" not in response.get_data(as_text=True)
    assert "private database detail" in caplog.text


def test_main_binds_to_requested_host_and_disables_debug(monkeypatch):
    calls = []

    class FakeApp:
        def run(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(dashboard, "create_app", FakeApp)
    monkeypatch.setattr(sys, "argv", ["dashboard", "--host", "0.0.0.0", "--port", "5050"])

    assert dashboard.main() == 0
    assert calls == [
        {
            "host": "0.0.0.0",
            "port": 5050,
            "debug": False,
            "use_reloader": False,
        }
    ]
