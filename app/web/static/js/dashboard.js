// Multi-Bot Trading Dashboard Client Script
document.addEventListener('DOMContentLoaded', () => {
    initModal();
    initStrategyParamSelector();
    initBotToggles();
    initBotDeletions();
    initCreateBotForm();
    initRunCycleButton();
    startStatusPolling();
});

function showNotification(message, type = 'info', durationMs = 4000) {
    const bar = document.getElementById('notification-bar');
    if (!bar) return;

    bar.className = `notification-bar notice-${type}`;
    bar.textContent = message;
    bar.classList.remove('hidden');

    if (window._notifTimeout) clearTimeout(window._notifTimeout);
    window._notifTimeout = setTimeout(() => {
        bar.classList.add('hidden');
    }, durationMs);
}

// Modal handling
function initModal() {
    const modal = document.getElementById('create-bot-modal');
    const openBtn = document.getElementById('btn-open-modal');
    const closeBtn = document.getElementById('btn-close-modal');
    const cancelBtn = document.getElementById('btn-cancel-modal');

    if (!modal || !openBtn) return;

    openBtn.addEventListener('click', () => {
        modal.classList.remove('hidden');
    });

    const closeModal = () => modal.classList.add('hidden');

    if (closeBtn) closeBtn.addEventListener('click', closeModal);
    if (cancelBtn) cancelBtn.addEventListener('click', closeModal);

    modal.addEventListener('click', (e) => {
        if (e.target === modal) closeModal();
    });
}

// Strategy parameters switcher in creation form
function initStrategyParamSelector() {
    const typeSelect = document.getElementById('bot-type');
    if (!typeSelect) return;

    const paramGroups = {
        'ema_cross': document.getElementById('params-ema_cross'),
        'rsi': document.getElementById('params-rsi'),
        'bollinger': document.getElementById('params-bollinger'),
        'macd': document.getElementById('params-macd'),
    };

    typeSelect.addEventListener('change', () => {
        const val = typeSelect.value;
        Object.entries(paramGroups).forEach(([type, el]) => {
            if (el) {
                if (type === val) {
                    el.classList.remove('hidden');
                } else {
                    el.classList.add('hidden');
                }
            }
        });
    });
}

// Bot Toggle Switch
function initBotToggles() {
    const tableBody = document.getElementById('bots-table-body');
    if (!tableBody) return;

    tableBody.addEventListener('change', async (e) => {
        if (!e.target.classList.contains('bot-toggle')) return;

        const toggle = e.target;
        const key = toggle.getAttribute('data-key');
        const originalState = !toggle.checked;

        toggle.disabled = true;

        try {
            const resp = await fetch(`/api/bots/${encodeURIComponent(key)}/toggle`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
            });
            const data = await resp.json();

            if (!resp.ok || !data.success) {
                throw new Error(data.error || 'Error al cambiar estado');
            }

            const stateText = data.is_active ? 'ACTIVADO' : 'DESACTIVADO';
            showNotification(`Bot '${data.name}' ${stateText} con éxito`, 'success');
            updateActiveBotsCounter();
        } catch (err) {
            console.error('Toggle error:', err);
            toggle.checked = originalState;
            showNotification(`Error: ${err.message}`, 'error');
        } finally {
            toggle.disabled = false;
        }
    });
}

// Bot Deletion
function initBotDeletions() {
    const tableBody = document.getElementById('bots-table-body');
    if (!tableBody) return;

    tableBody.addEventListener('click', async (e) => {
        const btn = e.target.closest('.btn-delete-bot');
        if (!btn) return;

        const key = btn.getAttribute('data-key');
        if (!confirm(`¿Eliminar la configuración del bot '${key}'?`)) {
            return;
        }

        btn.disabled = true;

        try {
            const resp = await fetch(`/api/bots/${encodeURIComponent(key)}`, {
                method: 'DELETE',
            });
            const data = await resp.json();

            if (!resp.ok || !data.success) {
                throw new Error(data.error || 'No se pudo eliminar');
            }

            const row = document.getElementById(`bot-row-${key}`);
            if (row) row.remove();

            showNotification(`Bot '${key}' eliminado correctamente`, 'success');
            updateActiveBotsCounter();
        } catch (err) {
            console.error('Delete error:', err);
            showNotification(`Error al eliminar: ${err.message}`, 'error');
            btn.disabled = false;
        }
    });
}

// Bot Creation Form
function initCreateBotForm() {
    const form = document.getElementById('create-bot-form');
    if (!form) return;

    form.addEventListener('submit', async (e) => {
        e.preventDefault();

        const name = document.getElementById('bot-name').value.trim();
        const strategy_type = document.getElementById('bot-type').value;
        const symbol = document.getElementById('bot-symbol').value.trim().toUpperCase();
        const allocation_eur = parseFloat(document.getElementById('bot-alloc').value);

        const parameters = {};

        if (strategy_type === 'ema_cross') {
            parameters.fast_period = parseInt(document.getElementById('fast_period').value, 10);
            parameters.slow_period = parseInt(document.getElementById('slow_period').value, 10);
        } else if (strategy_type === 'rsi') {
            parameters.period = parseInt(document.getElementById('rsi_period').value, 10);
            parameters.oversold = parseFloat(document.getElementById('rsi_oversold').value);
            parameters.overbought = parseFloat(document.getElementById('rsi_overbought').value);
        } else if (strategy_type === 'bollinger') {
            parameters.period = parseInt(document.getElementById('boll_period').value, 10);
            parameters.std_dev_multiplier = parseFloat(document.getElementById('boll_std').value);
        } else if (strategy_type === 'macd') {
            parameters.fast_period = parseInt(document.getElementById('macd_fast').value, 10);
            parameters.slow_period = parseInt(document.getElementById('macd_slow').value, 10);
            parameters.signal_period = parseInt(document.getElementById('macd_signal').value, 10);
        }

        const payload = {
            name,
            strategy_type,
            symbol,
            allocation_eur,
            parameters,
            is_active: true,
        };

        const submitBtn = form.querySelector('button[type="submit"]');
        submitBtn.disabled = true;
        submitBtn.textContent = 'Creando...';

        try {
            const resp = await fetch('/api/bots', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            const data = await resp.json();

            if (!resp.ok || !data.success) {
                throw new Error(data.error || 'Error al crear bot');
            }

            showNotification(`Bot '${name}' creado exitosamente`, 'success');
            document.getElementById('create-bot-modal').classList.add('hidden');
            form.reset();

            // Recargar la página para mostrar el nuevo bot en la tabla
            setTimeout(() => window.location.reload(), 600);
        } catch (err) {
            console.error('Create bot error:', err);
            showNotification(`Error: ${err.message}`, 'error');
        } finally {
            submitBtn.disabled = false;
            submitBtn.textContent = 'Crear Bot';
        }
    });
}

// Run Engine Cycle
function initRunCycleButton() {
    const btn = document.getElementById('btn-run-cycle');
    if (!btn) return;

    btn.addEventListener('click', async () => {
        btn.disabled = true;
        const originalHtml = btn.innerHTML;
        btn.innerHTML = '<span>⏳</span> Ejecutando ciclo...';

        try {
            const resp = await fetch('/api/engine/run-cycle', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
            });
            const data = await resp.json();

            if (!resp.ok || !data.success) {
                throw new Error(data.error || 'Error durante la ejecución del ciclo');
            }

            const activeCount = data.active_bots_count ?? '-';
            const sigCount = data.signals_count ?? 0;
            const ordersCount = data.orders_count ?? 0;
            const exitsCount = data.exits_count ?? 0;
            const msg = `Ciclo completado: ${activeCount} bots activos, ${sigCount} señales, ${exitsCount} salidas, ${ordersCount} órdenes cursadas`;
            showNotification(msg, 'success', 6000);

            // Refrescar status en la UI
            setTimeout(() => refreshDashboardStatus(), 1000);
        } catch (err) {
            console.error('Cycle error:', err);
            showNotification(`Fallo en ciclo: ${err.message}`, 'error');
        } finally {
            btn.disabled = false;
            btn.innerHTML = originalHtml;
        }
    });
}

// Update Active Bots Badge count
function updateActiveBotsCounter() {
    const toggles = document.querySelectorAll('.bot-toggle');
    let active = 0;
    toggles.forEach(t => {
        if (t.checked) active++;
    });

    const badge = document.getElementById('active-bots-badge');
    if (badge) {
        badge.textContent = `${active} activos`;
    }
}

// Status Poller
async function refreshDashboardStatus() {
    try {
        const resp = await fetch('/api/status');
        if (!resp.ok) return;
        const data = await resp.json();

        // Actualizar badges
        const binanceBadge = document.getElementById('binance-badge');
        if (binanceBadge) {
            if (data.binance_connected) {
                binanceBadge.className = 'status-pill status-online';
                binanceBadge.textContent = 'Binance: ONLINE';
            } else {
                binanceBadge.className = 'status-pill status-offline';
                binanceBadge.textContent = 'Binance: OFFLINE';
            }
        }

        const dbBadge = document.getElementById('db-badge');
        if (dbBadge) {
            if (data.db_connected) {
                dbBadge.className = 'status-pill status-online';
                dbBadge.textContent = 'DB: CONECTADA';
            } else {
                dbBadge.className = 'status-pill status-offline';
                dbBadge.textContent = 'DB: DESCONECTADA';
            }
        }

        // Actualizar PnL y Riesgo
        if (data.risk_summary) {
            const daily = document.getElementById('daily-pnl');
            if (daily) {
                const val = data.risk_summary.daily_pnl_eur || 0;
                daily.textContent = `${val >= 0 ? '+' : ''}${val.toFixed(2)} €`;
                daily.className = `metric-value ${val >= 0 ? 'text-success' : 'text-danger'}`;
            }

            const total = document.getElementById('total-pnl');
            if (total) {
                const val = data.risk_summary.total_pnl_eur || 0;
                total.textContent = `${val >= 0 ? '+' : ''}${val.toFixed(2)} €`;
                total.className = `metric-value ${val >= 0 ? 'text-success' : 'text-danger'}`;
            }

            const risk = document.getElementById('risk-status');
            if (risk) {
                const allowed = data.risk_summary.trading_allowed !== false;
                risk.textContent = allowed ? 'OPERATIVO' : 'BLOQUEADO';
                risk.className = `metric-value ${allowed ? 'text-success' : 'text-danger'}`;
            }
        }

        // Contador de posiciones
        const posCount = document.getElementById('pos-count');
        if (posCount && Array.isArray(data.open_positions)) {
            posCount.textContent = data.open_positions.length;
        }

    } catch (e) {
        console.warn('Dashboard poll error:', e);
    }
}

function startStatusPolling() {
    setInterval(refreshDashboardStatus, 10000);
}