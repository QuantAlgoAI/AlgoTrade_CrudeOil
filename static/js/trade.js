// trade.js – Handles Trade page interactions

console.log("trade.js loaded");

document.addEventListener('DOMContentLoaded', () => {
    // Fetch instrument info once so we can map tokens / symbols
    let instrumentInfo = {};
    fetch('/info')
        .then(res => res.json())
        .then(data => instrumentInfo = data)
        .catch(err => console.warn('Unable to fetch instrument info', err));

    const orderForm = document.getElementById('order-form');
    if (orderForm) {
        orderForm.addEventListener('submit', async function (e) {
            e.preventDefault();

            const instrument = document.getElementById('order-instrument').value;
            const side = document.querySelector('input[name="transaction"]:checked').value;
            const orderType = document.getElementById('order-type').value;
            const qty = parseInt(document.getElementById('order-qty').value);
            const priceInput = document.getElementById('order-price').value;
            const price = priceInput ? parseFloat(priceInput) : null;

            // Fallback mapping if /info not yet populated
            const staticMapping = {
                fut: { symbol_token: 'FUT', trading_symbol: 'CRUDEOILFUT' },
                ce: { symbol_token: 'CE', trading_symbol: 'CRUDEOILCE' },
                pe: { symbol_token: 'PE', trading_symbol: 'CRUDEOILPE' },
            };

            const map = staticMapping[instrument] || {};
            const tradingSymbol = (instrumentInfo[instrument] && instrumentInfo[instrument].symbol) || map.trading_symbol;

            const payload = {
                symbol_token: map.symbol_token,
                trading_symbol: tradingSymbol,
                side: side,
                qty: qty,
                order_type: orderType,
                price: price,
            };

            console.log('Placing order', payload);
            try {
                const res = await fetch('/api/trade', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (res.ok) {
                    alert('Order placed: ' + data.order_id);
                    appendOrderHistory(data.order_id, tradingSymbol, side, qty, price, orderType);
                    appendOpenPosition(data.order_id, tradingSymbol, side, qty, price, orderType);
                } else {
                    alert('Order error: ' + (data.error || res.statusText));
                }
            } catch (err) {
                console.error('Order error', err);
                alert('Order error: ' + err);
            }
        });
    }
});

function appendOrderHistory(orderId, symbol, side, qty, price, orderType) {
    const now = new Date().toLocaleTimeString();
    const row = document.createElement('tr');
    row.innerHTML = `
        <td>${now}</td>
        <td>${symbol}</td>
        <td>${side}</td>
        <td>${qty}</td>
        <td>${orderType === 'MARKET' ? '--' : (price || '--')}</td>
        <td>${orderId}</td>`;
    const ordersTbl = document.getElementById('orders-table');
    if (ordersTbl) ordersTbl.prepend(row);
}

function appendOpenPosition(orderId, symbol, side, qty, price, orderType) {
    const posRow = document.createElement('tr');
    posRow.setAttribute('data-order-id', orderId);
    posRow.innerHTML = `
        <td>${symbol}</td>
        <td>${side}</td>
        <td>${qty}</td>
        <td>${orderType === 'MARKET' ? '--' : (price || '--')}</td>
        <td>--</td>
        <td>--</td>
        <td><button class="btn btn-sm btn-danger" onclick="closePosition('${orderId}', '${symbol}', ${qty}, ${price || null})">Close</button></td>`;
    const posTbl = document.getElementById('positions-table');
    if (posTbl) posTbl.prepend(posRow);
}

// Placeholder – integrate with backend close endpoint if available
async function closePosition(orderId, symbol, qty, price) {
    if (!confirm('Close position ' + orderId + '?')) return;
    try {
        const res = await fetch('/api/close_position', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ order_id: orderId, symbol, qty, price })
        });
        const data = await res.json();
        if (res.ok) {
            alert('Position closed');
            // Remove row from positions table
            const row = document.querySelector(`tr[data-order-id="${orderId}"]`);
            if (row) row.remove();
        } else {
            alert('Close error: ' + (data.error || res.statusText));
        }
    } catch (err) {
        console.error('Close position error', err);
        alert('Close error: ' + err);
    }
}
