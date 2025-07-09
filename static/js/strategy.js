// static/js/strategy.js
// Handles strategy parameter form and performance data updates

// Utility to POST JSON and return response
async function postJSON(url, data) {
    const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
}

// Submit strategy parameters
function attachStrategyForm() {
    const form = document.getElementById('strategy-form');
    if (!form) return;
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const params = {
            fast_ema_period: parseInt(document.getElementById('fast-ema-period').value),
            slow_ema_period: parseInt(document.getElementById('slow-ema-period').value),
            rsi_period: parseInt(document.getElementById('rsi-period').value),
            vwap_period: parseInt(document.getElementById('vwap-period').value),
            atr_period: parseInt(document.getElementById('atr-period').value),
            use_fast_ema: document.getElementById('use-fast-ema').checked,
            use_slow_ema: document.getElementById('use-slow-ema').checked,
            use_rsi: document.getElementById('use-rsi').checked,
            use_vwap: document.getElementById('use-vwap').checked,
            use_atr: document.getElementById('use-atr').checked,
        };
        try {
            await postJSON('/api/update_strategy_params', params);
            alert('Strategy parameters updated');
        } catch (err) {
            console.error(err);
            alert('Error updating strategy parameters');
        }
    });
}

// Placeholder: Listen for live performance/trade updates via Socket.IO
function initStrategySocket() {
    if (typeof io === 'undefined') return; // socket.io not loaded
    const socket = io();
    socket.on('strategy_performance', (data) => {
        // update performance cards
        document.getElementById('net-pnl').innerText = `₹${data.net_pnl.toFixed(2)}`;
        document.getElementById('win-rate').innerText = `${data.win_rate}%`;
        document.getElementById('total-trades').innerText = data.total_trades;
        document.getElementById('max-drawdown').innerText = `${data.max_dd}%`;
        // TODO: Update Plotly chart if desired
    });
    socket.on('strategy_trade', (trade) => {
        const tbody = document.getElementById('strategy-trades');
        if (!tbody) return;
        const row = document.createElement('tr');
        row.innerHTML = `<td>${trade.time}</td><td>${trade.symbol}</td><td>${trade.signal}</td><td>${trade.entry}</td><td>${trade.exit || '--'}</td><td>${trade.pnl}</td>`;
        tbody.prepend(row);
    });
}

document.addEventListener('DOMContentLoaded', () => {
    attachStrategyForm();
    initStrategySocket();
});
