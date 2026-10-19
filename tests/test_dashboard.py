"""Tests para el módulo de Dashboard y monitorización."""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.dashboard.cli import render_dashboard
from app.dashboard.collector import DashboardData, DashboardPosition, collect_dashboard_data
from app.storage.models import TradePosition


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_dummy_dashboard_data(
    open_positions=None,
    db_connected=True,
    binance_connected=True,
    trading_allowed=True,
) -> DashboardData:
    return DashboardData(
        timestamp=datetime(2026, 10, 19, 14, 0, tzinfo=timezone.utc),
        db_connected=db_connected,
        binance_connected=binance_connected,
        risk_summary={
            "max_capital_eur": Decimal("100.00"),
            "max_allocation_per_strategy_eur": Decimal("20.00"),
            "max_open_positions": 1,
            "daily_pnl_eur": Decimal("0.50"),
            "total_pnl_eur": Decimal("1.20"),
            "max_daily_loss_eur": Decimal("2.00"),
            "max_total_loss_eur": Decimal("5.00"),
            "active_allocations": len(open_positions or []),
            "total_allocated_eur": Decimal("20.00") if open_positions else Decimal("0.00"),
            "trading_allowed": trading_allowed,
            "allocations": [],
        },
        open_positions=open_positions or [],
        recent_closed_positions=[],
        recent_orders=[],
        binance_balances=[{"asset": "USDT", "free": "150.00", "locked": "0.00"}],
        active_strategies=[
            {
                "name": "EMA Cross (9/21)",
                "type": "Tendencia",
                "desc": "Detecta cruces de medias",
                "max_alloc_eur": Decimal("20.00"),
            }
        ],
    )


# ---------------------------------------------------------------------------
# Tests: Collector
# ---------------------------------------------------------------------------

class TestDashboardCollector:
    @patch("app.dashboard.collector.check_connection", return_value=False)
    def test_collects_safely_when_db_down(self, mock_check_db):
        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_client.get_balances.return_value = []

        data = collect_dashboard_data(binance_client=mock_client)

        assert not data.db_connected
        assert data.binance_connected
        assert data.risk_summary["max_capital_eur"] == Decimal("100")
        assert len(data.active_strategies) == 4

    @patch("app.dashboard.collector.check_connection", return_value=True)
    @patch("app.dashboard.collector.get_open_positions")
    @patch("app.dashboard.collector.get_recent_positions", return_value=[])
    @patch("app.dashboard.collector.get_recent_orders", return_value=[])
    def test_collects_with_open_position(
        self,
        mock_orders,
        mock_recent,
        mock_get_open,
        mock_check_db,
    ):
        mock_pos = TradePosition(
            id=1,
            symbol="BTCUSDT",
            side="long",
            status="open",
            quantity=Decimal("0.00036"),
            entry_price=Decimal("60000"),
        )
        mock_pos.opened_at = datetime.now(timezone.utc)
        mock_get_open.return_value = [mock_pos]

        mock_client = MagicMock()
        mock_client.ping.return_value = True
        mock_client.get_price.return_value = {"price": "63000"}
        mock_client.get_balances.return_value = []

        mock_risk = MagicMock()
        mock_risk.get_account_summary.return_value = {
            "max_capital_eur": Decimal("100"),
            "daily_pnl_eur": Decimal("0"),
            "total_pnl_eur": Decimal("0"),
            "trading_allowed": True,
            "active_allocations": 1,
        }

        data = collect_dashboard_data(
            binance_client=mock_client,
            risk_manager=mock_risk,
        )

        assert len(data.open_positions) == 1
        pos = data.open_positions[0]
        assert pos.symbol == "BTCUSDT"
        assert pos.current_price == Decimal("63000")
        assert pos.unrealized_pnl_pct == Decimal("5.00")
        assert pos.stop_loss_price == Decimal("58200.00")
        assert pos.take_profit_price == Decimal("63000.00")


# ---------------------------------------------------------------------------
# Tests: Renderer
# ---------------------------------------------------------------------------

class TestDashboardRenderer:
    def test_renders_empty_positions_view(self):
        data = _make_dummy_dashboard_data()
        output = render_dashboard(data)

        assert "PANEL DE CONTROL" in output
        assert "Binance Testnet: 🟢 ONLINE" in output
        assert "PostgreSQL: 🟢 CONECTADA" in output
        assert "Capital Máximo:      100.00 €" in output
        assert "No hay posiciones abiertas" in output

    def test_renders_open_position_details(self):
        pos = DashboardPosition(
            id=10,
            symbol="BTCUSDT",
            status="open",
            side="long",
            quantity=Decimal("0.00036"),
            entry_price=Decimal("60000.00"),
            current_price=Decimal("61200.00"),
            unrealized_pnl_eur=Decimal("0.40"),
            unrealized_pnl_pct=Decimal("2.00"),
            stop_loss_price=Decimal("58200.00"),
            take_profit_price=Decimal("63000.00"),
            opened_at=datetime.now(timezone.utc),
            age_hours=Decimal("1.5"),
        )
        data = _make_dummy_dashboard_data(open_positions=[pos])
        output = render_dashboard(data)

        assert "BTCUSDT" in output
        assert "60000.00 USDT" in output
        assert "61200.00 USDT" in output
        assert "+0.40 €" in output
        assert "Stop-Loss: 58200.00 USDT" in output
        assert "Take-Profit: 63000.00 USDT" in output

    def test_renders_blocked_trading_status(self):
        data = _make_dummy_dashboard_data(trading_allowed=False)
        output = render_dashboard(data)

        assert "🛑 BLOQUEADO" in output

