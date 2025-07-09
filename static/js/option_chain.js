document.addEventListener('DOMContentLoaded', () => {
    const tbody = document.querySelector('#option-chain-table tbody');
const refreshStatus = document.getElementById('refresh-status');
const trendCtx = document.getElementById('oi-trend').getContext('2d');
const statsDiv = document.getElementById('chain-stats');
let countdown = 10;
let timer;
const bufferSize = 10;
const ceTotals = [];
const peTotals = [];
let chart;
    const expiryBadge = document.getElementById('expiry-badge');

    async function fetchChain() {
        clearInterval(timer);
        countdown = 10;
        try {
            const res = await fetch('/api/option_chain');
            const data = await res.json();
            renderTable(data.rows);
            updateTrend(data.rows);
            if (data.stats) {
                const s = data.stats;
                statsDiv.textContent = `PCR: ${s.pcr.toFixed(2)} | ATM PCR: ${s.atm_pcr.toFixed(2)}  |  Trend: ${s.trend}  |  Support: ${s.support}  |  Resistance: ${s.resistance}`;
            }
            expiryBadge.textContent = data.expiry;
            startCountdown();
        } catch (err) {
            console.error(err);
            startCountdown();
        }
    }

    function renderTable(rows) {
        // find ATM (row with min |LTP - Strike|)
        let atmStrike = null;
        if (rows.length) {
            const futLtp = rows.reduce((acc, r) => acc + r['Strike'], 0) / rows.length; // rough midpoint
            atmStrike = rows.reduce((prev, r) => Math.abs(r.Strike - futLtp) < Math.abs(prev - futLtp) ? r.Strike : prev, rows[0].Strike);
        }

        tbody.innerHTML = '';
        const maxOi = Math.max(...rows.map(r => Math.max(r['CE OI'], r['PE OI'])));
        rows.forEach(r => {
            const tr = document.createElement('tr');
            // compute heatmap classes based on OI magnitude
            
            const ceRatio = r['CE OI'] / maxOi;
            const peRatio = r['PE OI'] / maxOi;
            const ceBg = `rgba(0,255,0,${ceRatio*0.6+0.2})`;
            const peBg = `rgba(255,0,0,${peRatio*0.6+0.2})`;
            tr.innerHTML = `
                
                <td>${r.Strike}</td>
                <td style="background:${ceBg}">${r['CE OI']}</td>
                <td>${r['CE IV%']}</td>
                <td>${r['CE LTP']}</td>
                <td>${r['CE Vol']}</td>
                <td>${r['CE Δ']}</td>
                <td>${r['Γ']}</td>
                <td>${r['Θ']}</td>
                <td>${r['Vega']}</td>
                <td style="background:${peBg}">${r['PE OI']}</td>
                <td>${r['PE IV%']}</td>
                <td>${r['PE LTP']}</td>
                <td>${r['PE Vol']}</td>
                <td>${r['PE Γ']}</td>
                <td>${r['PE Θ']}</td>
                <td>${r['PE Vega']}</td>
                <td>${r['PE Δ']}</td>
            `;
            if (atmStrike && r.Strike === atmStrike) tr.classList.add('table-active');
            tbody.appendChild(tr);
        });
    }

    function startCountdown() {
        timer = setInterval(() => {
            countdown--;
            refreshStatus.textContent = `Last update ${new Date().toLocaleTimeString()} — next in ${countdown}s`;
            if (countdown === 0) fetchChain();
        }, 1000);
    }

    function updateTrend(rows) {
        const ceTot = rows.reduce((sum, r) => sum + r['CE OI'], 0);
        const peTot = rows.reduce((sum, r) => sum + r['PE OI'], 0);
        ceTotals.push(ceTot);
        peTotals.push(peTot);
        if (ceTotals.length > bufferSize) {
            ceTotals.shift();
            peTotals.shift();
        }
        if (!chart) {
            chart = new Chart(trendCtx, {
                type: 'line',
                data: {
                    labels: ceTotals.map((_, i) => i + 1),
                    datasets: [
                        { label: 'CE OI', data: ceTotals, borderColor: 'lime', tension: 0.3, fill: false },
                        { label: 'PE OI', data: peTotals, borderColor: 'red', tension: 0.3, fill: false }
                    ]
                },
                options: {
                    plugins: { legend: { display: false } },
                    scales: { x: { display: false }, y: { display: false } }
                }
            });
        } else {
            chart.data.labels = ceTotals.map((_, i) => i + 1);
            chart.data.datasets[0].data = ceTotals;
            chart.data.datasets[1].data = peTotals;
            chart.update();
        }
    }

    fetchChain();
    startCountdown();
});
