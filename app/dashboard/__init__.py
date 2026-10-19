"""Módulo de monitorización y Dashboard del sistema multi-bot."""

from app.dashboard.cli import main, render_dashboard
from app.dashboard.collector import DashboardData, collect_dashboard_data

__all__ = [
    "DashboardData",
    "collect_dashboard_data",
    "main",
    "render_dashboard",
]

