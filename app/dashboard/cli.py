"""Renderizador en consola (CLI) para el Dashboard de Trading."""

import argparse
import json
import os
import sys
import time
from decimal import Decimal

from app.dashboard.collector import DashboardData, collect_dashboard_data


def render_dashboard(data: DashboardData) -> str:
    """Genera una vista formateada en texto para la consola."""
    lines: list[str] = []
    width = 86

    binance_badge = "🟢 ONLINE" if data.binance_connected else "🔴 OFFLINE"
    db_badge = "🟢 CONECTADA" if data.db_connected else "🟡 DESCONECTADA"

    lines.append("=" * width)
    lines.append(f" 📊 PANEL DE CONTROL — SISTEMA MULTI-BOT (100 €) ".center(width, " "))
    lines.append("=" * width)
    lines.append(
        f" Actualizado: {data.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC'):<28} "
        f"Binance Testnet: {binance_badge:<12} "
        f"PostgreSQL: {db_badge}"
    )
    lines.append("=" * width)

    # 1. Resumen de Riesgo
    risk = data.risk_summary
    is_allowed = risk.get("trading_allowed", True)
    trading_status = "🟢 OPERATIVO (Permitido)" if is_allowed else "🛑 BLOQUEADO (Límite superado)"

    daily_pnl = risk.get("daily_pnl_eur", Decimal("0"))
    total_pnl = risk.get("total_pnl_eur", Decimal("0"))
    daily_pnl_str = f"{daily_pnl:+.2f} €"
    total_pnl_str = f"{total_pnl:+.2f} €"

    lines.append(" 🛡️  ESTADO DEL GESTOR CENTRAL DE RIESGO".ljust(width))
    lines.append("-" * width)
    lines.append(
        f" Capital Máximo:      {risk.get('max_capital_eur', 100):.2f} €"
        f"         Máx. por Estrategia:  {risk.get('max_allocation_per_strategy_eur', 20):.2f} €"
    )
    lines.append(
        f" PnL Hoy (Cierre):    {daily_pnl_str:<10}"
        f"         Freno Pérdida Diaria: -{risk.get('max_daily_loss_eur', 2):.2f} €"
    )
    lines.append(
        f" PnL Acumulado:       {total_pnl_str:<10}"
        f"         Freno Pérdida Total:  -{risk.get('max_total_loss_eur', 5):.2f} €"
    )
    lines.append(
        f" Posiciones Activas:  {risk.get('active_allocations', 0)} / {risk.get('max_open_positions', 1)}"
        f"              Estado Global:        {trading_status}"
    )
    lines.append("=" * width)

    # 2. Estrategias del portafolio
    lines.append(" 🧠  CATÁLOGO MULTI-ESTRATEGIA".ljust(width))
    lines.append("-" * width)
    lines.append(
        f" {'Estrategia':<28} {'Tipo':<14} {'Asignación':<12} {'Función':<30}"
    )
    lines.append("-" * width)
    for s in data.active_strategies:
        lines.append(
            f" {s['name']:<28} {s['type']:<14} {s['max_alloc_eur']:.2f} €      {s['desc'][:28]}"
        )
    lines.append("=" * width)

    # 3. Posiciones Abiertas
    lines.append(f" 💼  POSICIÓN ABIERTA ACTUAL ({len(data.open_positions)})".ljust(width))
    lines.append("-" * width)

    if not data.open_positions:
        lines.append("  (No hay posiciones abiertas activas. El capital está 100% disponible)")
    else:
        for p in data.open_positions:
            curr_str = f"{p.current_price:.2f}" if p.current_price else "N/D"
            pnl_eur_str = f"{p.unrealized_pnl_eur:+.2f} €" if p.unrealized_pnl_eur is not None else "N/D"
            pnl_pct_str = f"{p.unrealized_pnl_pct:+.2f}%" if p.unrealized_pnl_pct is not None else "N/D"
            lines.append(
                f"  Par: {p.symbol} ({p.side.upper()}) | Cantidad: {p.quantity} | Entrada: {p.entry_price:.2f} USDT"
            )
            lines.append(
                f"  Precio Actual: {curr_str} USDT | PnL Flotante: {pnl_eur_str} ({pnl_pct_str}) | Tiempo: {p.age_hours}h"
            )
            lines.append(
                f"  Objetivos Automáticos -> Stop-Loss: {p.stop_loss_price:.2f} USDT (-3%) | Take-Profit: {p.take_profit_price:.2f} USDT (+5%)"
            )
    lines.append("=" * width)

    # 4. Últimas Operaciones Cerradas
    if data.recent_closed_positions:
        lines.append(
            f" 📜  HISTORIAL DE OPERACIONES RECIENTES ({len(data.recent_closed_positions)})".ljust(width)
        )
        lines.append("-" * width)
        lines.append(
            f" {'ID':<5} {'Par':<10} {'Cantidad':<12} {'Entrada':<12} {'Salida':<12} {'PnL (EUR)':<12}"
        )
        lines.append("-" * width)
        for c in data.recent_closed_positions[:5]:
            pnl = c.get("realized_pnl", Decimal("0"))
            pnl_s = f"{pnl:+.2f} €" if pnl is not None else "0.00 €"
            lines.append(
                f" #{c['id']:<4} {c['symbol']:<10} {c['quantity']:<12} "
                f"{c['entry_price']:<12.2f} {c.get('exit_price', 0):<12.2f} {pnl_s:<12}"
            )
        lines.append("=" * width)

    # 5. Saldos Binance Testnet (si disponibles)
    if data.binance_balances:
        lines.append(" 💰  SALDOS DETECTADOS EN BINANCE TESTNET".ljust(width))
        lines.append("-" * width)
        bal_strs = [
            f"{b['asset']}: {float(b['free']):.4f}"
            for b in data.binance_balances
            if float(b["free"]) > 0 or float(b.get("locked", 0)) > 0
        ]
        lines.append("  " + " | ".join(bal_strs[:6]))
        lines.append("=" * width)

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Panel de monitorización en tiempo real del sistema multi-bot.")
    parser.add_argument("--watch", type=int, default=None, help="Segundos entre actualizaciones continuas")
    parser.add_argument("--json", action="store_true", help="Devuelve los datos en formato JSON")
    args = parser.parse_args()

    def _display_once():
        data = collect_dashboard_data()
        if args.json:
            # Serializador JSON seguro para Decimals y datetimes
            def _default(obj):
                if isinstance(obj, Decimal):
                    return float(obj)
                if isinstance(obj, datetime):
                    return obj.isoformat()
                raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

            payload = {
                "timestamp": data.timestamp,
                "db_connected": data.db_connected,
                "binance_connected": data.binance_connected,
                "risk_summary": data.risk_summary,
                "open_positions": [p.__dict__ for p in data.open_positions],
                "recent_closed_positions": data.recent_closed_positions,
                "recent_orders": data.recent_orders,
                "binance_balances": data.binance_balances,
                "active_strategies": data.active_strategies,
            }
            print(json.dumps(payload, default=_default, indent=2))
        else:
            print(render_dashboard(data))

    if args.watch is None:
        _display_once()
        return 0

    # Modo monitor interactivo continuo
    try:
        while True:
            os.system("clear" if os.name == "posix" else "cls")
            _display_once()
            print(f"\n[Presiona Ctrl+C para salir] — Actualizando cada {args.watch}s...")
            time.sleep(args.watch)
    except KeyboardInterrupt:
        print("\nMonitor detenido.")
        return 0


if __name__ == "__main__":
    sys.exit(main())

