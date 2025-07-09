// Global variables
let refreshInterval = 5000;
let chartsTimer, infoTimer, strategyTimer;
let currentTab = 'fut';
let indicatorToggles = {
    'fast_ema': true,
    'slow_ema': true,
    'vwap': true,
    'rsi': true,
    'atr': true,
    'macd': true
};

// ==== Helper functions for indicators ====
function calculateEMA(values, period) {
    const k = 2 / (period + 1);
    let ema = [];
    let prev;
    values.forEach((val, idx) => {
        if (idx === 0) {
            prev = val;
            ema.push(val);
        } else {
            const current = val * k + prev * (1 - k);
            ema.push(current);
            prev = current;
        }
    });
    return ema;
}

function calculateMACD(values, fast = 12, slow = 26, signal = 9) {
    const fastEma = calculateEMA(values, fast);
    const slowEma = calculateEMA(values, slow);
    const macdLine = fastEma.map((v, i) => v - slowEma[i]);
    const signalLine = calculateEMA(macdLine, signal);
    const histogram = macdLine.map((v, i) => v - signalLine[i]);
    return { macdLine, signalLine, histogram };
}

// Tab management
function showMainTab(tab) {
    document.getElementById('main-dashboard').style.display = (tab === 'dashboard') ? '' : 'none';
    document.getElementById('main-trade').style.display = (tab === 'trade') ? '' : 'none';
    document.getElementById('main-strategy').style.display = (tab === 'strategy') ? '' : 'none';
    document.getElementById('main-settings').style.display = (tab === 'settings') ? '' : 'none';
    
    document.getElementById('nav-dashboard').classList.toggle('active', tab === 'dashboard');
    document.getElementById('nav-trade').classList.toggle('active', tab === 'trade');
    document.getElementById('nav-strategy').classList.toggle('active', tab === 'strategy');
    document.getElementById('nav-settings').classList.toggle('active', tab === 'settings');
}

function showTab(tab) {
    document.getElementById('tab-fut').classList.remove('active');
    document.getElementById('tab-ce').classList.remove('active');
    document.getElementById('tab-pe').classList.remove('active');
    document.getElementById('content-fut').classList.remove('active');
    document.getElementById('content-ce').classList.remove('active');
    document.getElementById('content-pe').classList.remove('active');
    
    document.getElementById('tab-' + tab).classList.add('active');
    document.getElementById('content-' + tab).classList.add('active');
    currentTab = tab;
    
    if (window.lastOhlcData) {
        updateLtpVolOi(window.lastOhlcData, currentTab);
    }
}

// Data fetching
async function fetchOHLC() {
    const interval = document.getElementById('ohlc-interval').value;
    const res = await fetch('/ohlc?interval=' + encodeURIComponent(interval));
    return await res.json();
}

async function fetchInfo() {
    const res = await fetch('/info');
    return await res.json();
}

async function fetchStrategyStatus() {
    const res = await fetch('/strategy_status');
    return await res.json();
}

// UI updates
function updateInfoBoxes(info) {
    if (info.fut) {
        document.getElementById('fut-symbol').textContent = info.fut.symbol || '';
        document.getElementById('fut-expiry').textContent = info.fut.expiry || '';
        document.getElementById('fut-lotsize').textContent = info.fut.lotsize || '';
    }
    if (info.ce) {
        document.getElementById('ce-symbol').textContent = info.ce.symbol || '';
        document.getElementById('ce-strike').textContent = info.ce.strike ? ((Number(info.ce.strike) / 100).toFixed(2)) : '';
        document.getElementById('ce-expiry').textContent = info.ce.expiry || '';
        document.getElementById('ce-lotsize').textContent = info.ce.lotsize || '';
    }
    if (info.pe) {
        document.getElementById('pe-symbol').textContent = info.pe.symbol || '';
        document.getElementById('pe-strike').textContent = info.pe.strike ? ((Number(info.pe.strike) / 100).toFixed(2)) : '';
        document.getElementById('pe-expiry').textContent = info.pe.expiry || '';
        document.getElementById('pe-lotsize').textContent = info.pe.lotsize || '';
    }
}

function updateLtpVolOi(data, tab) {
    let arr;
    switch(tab) {
        case 'fut': arr = data.fut; break;
        case 'ce': arr = data.ce; break;
        case 'pe': arr = data.pe; break;
    }
    
    if (arr && arr.length > 0) {
        let last = arr[arr.length - 1];
        document.getElementById('ltp-val').textContent = last.close !== undefined ? Number(last.close).toFixed(2) : '--';
        document.getElementById('vol-val').textContent = last.volume !== undefined ? last.volume : '--';
        document.getElementById('oi-val').textContent = last.oi !== undefined ? last.oi : '--';
    } else {
        document.getElementById('ltp-val').textContent = '--';
        document.getElementById('vol-val').textContent = '--';
        document.getElementById('oi-val').textContent = '--';
    }
}

function format2(val) {
    if (val === undefined || val === null || val === "--") return "--";
    if (typeof val === "number") return val.toFixed(2);
    if (!isNaN(val)) return Number(val).toFixed(2);
    return val;
}

function updateStrategyStatus(status) {
    // Support both old (flat) and new (nested) API shapes
    const ce = status.ce_indicators || (status.ce && status.ce.indicators) || {};
    const pe = status.pe_indicators || (status.pe && status.pe.indicators) || {};

    // CE fields
    document.getElementById('ce-fast-ema').textContent = format2(ce.fast_ema);
    document.getElementById('ce-slow-ema').textContent = format2(ce.slow_ema);
    document.getElementById('ce-rsi').textContent = ce.rsi ?? "--";
    document.getElementById('ce-vwap').textContent = format2(ce.vwap);
    document.getElementById('ce-macd').textContent = ce.macd ?? "--";
    document.getElementById('ce-atr').textContent = format2(ce.atr);
    document.getElementById('ce-regime').textContent = ce.market_regime ?? "--";

    // PE fields
    document.getElementById('pe-fast-ema').textContent = format2(pe.fast_ema);
    document.getElementById('pe-slow-ema').textContent = format2(pe.slow_ema);
    document.getElementById('pe-rsi').textContent = pe.rsi ?? "--";
    document.getElementById('pe-vwap').textContent = format2(pe.vwap);
    document.getElementById('pe-macd').textContent = pe.macd ?? "--";
    document.getElementById('pe-atr').textContent = format2(pe.atr);
    document.getElementById('pe-regime').textContent = pe.market_regime ?? "--";
}

// Chart plotting
function plotOHLC(data, chartId, title) {
    let macdAdded = false;
    let macdMin = -1, macdMax = 1;
    if (!data.length) return;
    
    const x = data.map(d => d.timestamp);
    const open = data.map(d => d.open);
    const high = data.map(d => d.high);
    const low = data.map(d => d.low);
    const close = data.map(d => (d.close !== undefined ? Number(d.close) : null));
    const volume = data.map(d => d.volume);
    
    const traces = [{
        x, open, high, low, close,
        type: 'candlestick',
        xaxis: 'x',
        yaxis: 'y',
        name: 'Price',
        increasing: {line: {color: '#26a69a'}},
        decreasing: {line: {color: '#ef5350'}},
        hovertemplate: 'Time: %{x}<br>Open: %{open}<br>High: %{high}<br>Low: %{low}<br>Close: %{close}<extra></extra>'
    }];
    
    // Add volume
    traces.push({
        x, y: volume,
        type: 'bar',
        yaxis: 'y4',
        name: 'Volume',
        marker: {color: 'rgba(38,166,154,0.4)'},
        opacity: 0.5,
        hovertemplate: 'Time: %{x}<br>Volume: %{y}<extra></extra>'
    });
    
    // Add indicators if enabled
    if (indicatorToggles.fast_ema) {
        traces.push({
            x, y: data.map(d => d.fast_ema),
            type: 'scatter',
            mode: 'lines',
            name: 'Fast EMA',
            line: {color: '#ffd600', width: 2},
            yaxis: 'y',
            hovertemplate: 'Time: %{x}<br>Fast EMA: %{y:.2f}<extra></extra>'
        });
    }
    
    if (indicatorToggles.slow_ema) {
        traces.push({
            x, y: data.map(d => d.slow_ema),
            type: 'scatter',
            mode: 'lines',
            name: 'Slow EMA',
            line: {color: '#00b8d4', width: 2, dash: 'dot'},
            yaxis: 'y',
            hovertemplate: 'Time: %{x}<br>Slow EMA: %{y:.2f}<extra></extra>'
        });
    }
    
    if (indicatorToggles.vwap) {
        traces.push({
            x, y: data.map(d => d.vwap),
            type: 'scatter',
            mode: 'lines',
            name: 'VWAP',
            line: {color: '#ff9100', width: 2, dash: 'dash'},
            yaxis: 'y',
            hovertemplate: 'Time: %{x}<br>VWAP: %{y:.2f}<extra></extra>'
        });
    }
    // === RSI ===
    if (indicatorToggles.rsi && data[0].rsi !== undefined) {
        traces.push({
            x,
            y: data.map(d => d.rsi),
            type: 'scatter',
            mode: 'lines',
            name: 'RSI',
            line: {color: '#bb86fc', width: 2},
            yaxis: 'y2',
            hovertemplate: 'Time: %{x}<br>RSI: %{y:.2f}<extra></extra>'
        });
    }

    // === MACD ===
    if (indicatorToggles.macd && close.filter(v => v !== null && !isNaN(v)).length > 20) {
        const cleanedClose = [];
        close.forEach((v, i) => {
            if (v === null || isNaN(v)) {
                cleanedClose.push(i > 0 ? cleanedClose[i - 1] : 0);
            } else {
                cleanedClose.push(v);
            }
        });
        const { macdLine, signalLine, histogram } = calculateMACD(cleanedClose);
        // update dynamic MACD range
        macdMin = Math.min(...histogram, ...macdLine, ...signalLine);
        macdMax = Math.max(...histogram, ...macdLine, ...signalLine);
        traces.push({
            x,
            y: macdLine,
            type: 'scatter',
            mode: 'lines',
            name: 'MACD',
            line: {color: '#03dac6', width: 2},
            yaxis: 'y3',
            hovertemplate: 'Time: %{x}<br>MACD: %{y:.2f}<extra></extra>'
        });
        traces.push({
            x,
            y: signalLine,
            type: 'scatter',
            mode: 'lines',
            name: 'Signal',
            line: {color: '#cf6679', width: 2, dash: 'dot'},
            yaxis: 'y3',
            hovertemplate: 'Time: %{x}<br>Signal: %{y:.2f}<extra></extra>'
        });
        traces.push({
            x,
            y: histogram,
            type: 'bar',
            name: 'Histogram',
            marker: {color: histogram.map(v => v >= 0 ? '#4caf50' : '#f44336')},
            yaxis: 'y3',
            opacity: 0.5,
            hovertemplate: 'Time: %{x}<br>Hist: %{y:.2f}<extra></extra>'
        });
        macdAdded = true;
    }
    
    const layout = {
        template: 'plotly_dark',
        xaxis: {
            title: 'Time',
            rangeslider: {visible: true, bgcolor: '#23272f'},
            rangeselector: {
                buttons: [
                    {count: 5, label: '5m', step: 'minute', stepmode: 'backward'},
                    {count: 15, label: '15m', step: 'minute', stepmode: 'backward'},
                    {count: 1, label: '1h', step: 'hour', stepmode: 'backward'},
                    {step: 'all'}
                ]
            },
            type: 'date',
            color: '#eee',
            gridcolor: '#333',
        },
        yaxis: {
            title: 'Price',
            domain: [0.6, 1],
            color: '#eee',
            gridcolor: '#333'
        },
        yaxis2: {
            title: 'RSI',
            domain: [0.4, 0.6],
            color: '#eee',
            gridcolor: '#333'
        },
        yaxis3: {
            title: 'MACD',
            visible: true,
            domain: [0.2, 0.4],
            range: [macdMin * 1.2, macdMax * 1.2],
            color: '#eee',
            gridcolor: '#333'
        },
        yaxis4: {
            title: 'Volume',
            domain: [0, 0.2],
            color: '#eee',
            gridcolor: '#333'
        },
        legend: {orientation: 'h', font: {color: '#eee'}},
        title: {text: title, font: {color: '#fff'}},
        margin: {t: 40, l: 50, r: 50, b: 40},
        hovermode: 'x unified',
        plot_bgcolor: '#181a20',
        paper_bgcolor: '#181a20',
        height: 800
    };
    
    // Ensure RSI & MACD axes render even if no data yet
    // Placeholder for RSI
    if (indicatorToggles.rsi) {
        traces.push({
            x: [x[0] || new Date()],
            y: [50],
            yaxis: 'y2',
            mode: 'lines',
            type: 'scatter',
            name: 'RSI placeholder',
            showlegend: false,
            hoverinfo: 'skip',
            line: {color: 'rgba(0,0,0,0)'}
        });
    }

    // Placeholder for MACD
    if (indicatorToggles.macd && !macdAdded) {
        traces.push({
            x: [x[0] || new Date()],
            y: [0],
            yaxis: 'y3',
            mode: 'lines',
            type: 'scatter',
            name: 'MACD placeholder',
            showlegend: false,
            hoverinfo: 'skip',
            line: {color: 'rgba(0,0,0,0)'}
        });
    }

    Plotly.newPlot(chartId, traces, layout, {responsive: true});
}

// Update functions
async function updateCharts() {
    const data = await fetchOHLC();
    window.lastOhlcData = data;
    plotOHLC(data.fut, 'chart-fut', 'FUTURES OHLC CHART');
    plotOHLC(data.ce, 'chart-ce', 'CALL OHLC CHART');
    plotOHLC(data.pe, 'chart-pe', 'PUT OHLC CHART');
    updateLtpVolOi(data, currentTab);
}

async function updateInfo() {
    const info = await fetchInfo();
    updateInfoBoxes(info);
}

async function updateStrategy() {
    const status = await fetchStrategyStatus();
    updateStrategyStatus(status);
}

// Event listeners
document.addEventListener('DOMContentLoaded', function() {
    // Initialize indicator toggles
        ['show-fast-ema','show-slow-ema','show-vwap','show-rsi','show-atr','show-macd'].forEach(id => {
        const elem = document.getElementById(id);
        if (elem) {
            elem.addEventListener('change', function() {
                const key = id.replace('show-','');
                indicatorToggles[key] = this.checked;
                updateCharts();
            });
        }
    });
    
    // Initialize interval selector
    const intervalSelect = document.getElementById('ohlc-interval');
    if (intervalSelect) {
        intervalSelect.addEventListener('change', updateCharts);
    }
    
    // Initialize strategy form
    const strategyForm = document.getElementById('strategy-form');
    if (strategyForm) {
        strategyForm.addEventListener('submit', async function(e) {
            e.preventDefault();
            const params = {
                fast_ema_period: document.getElementById('fast-ema-period').value,
                slow_ema_period: document.getElementById('slow-ema-period').value,
                rsi_period: document.getElementById('rsi-period').value,
                atr_period: document.getElementById('atr-period').value,
                vwap_period: document.getElementById('vwap-period').value,
                use_fast_ema: document.getElementById('use-fast-ema').checked,
                use_slow_ema: document.getElementById('use-slow-ema').checked,
                use_rsi: document.getElementById('use-rsi').checked,
                use_atr: document.getElementById('use-atr').checked,
                use_vwap: document.getElementById('use-vwap').checked
            };
            
            await fetch('/strategy_params', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(params)
            });
            
            alert('Strategy parameters updated!');
        });
    }
    
    // Start update timers
    setTimers();
    
    // Initial updates
    updateCharts();
    updateInfo();
    updateStrategy();
    
    // Load notification settings
    loadNotificationSettings();
});

// Timer management
function setTimers() {
    if (chartsTimer) clearInterval(chartsTimer);
    if (infoTimer) clearInterval(infoTimer);
    if (strategyTimer) clearInterval(strategyTimer);
    
    chartsTimer = setInterval(updateCharts, refreshInterval);
    infoTimer = setInterval(updateInfo, refreshInterval);
    strategyTimer = setInterval(updateStrategy, refreshInterval);
}

// Notification Settings Functions
function togglePassword(inputId) {
    const input = document.getElementById(inputId);
    const icon = event.target.closest('button').querySelector('i');
    
    if (input.type === 'password') {
        input.type = 'text';
        icon.classList.remove('fa-eye');
        icon.classList.add('fa-eye-slash');
    } else {
        input.type = 'password';
        icon.classList.remove('fa-eye-slash');
        icon.classList.add('fa-eye');
    }
}

async function testTelegramNotification() {
    const token = document.getElementById('telegram-token').value;
    const chatId = document.getElementById('telegram-chat-id').value;
    
    try {
        const response = await fetch('/test_telegram', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ token, chat_id: chatId })
        });
        
        const result = await response.json();
        if (result.success) {
            alert('Test message sent successfully to Telegram!');
        } else {
            alert('Failed to send test message: ' + result.error);
        }
    } catch (error) {
        alert('Error testing Telegram notification: ' + error.message);
    }
}

async function testEmailNotification() {
    const email = document.getElementById('gmail-user').value;
    const password = document.getElementById('gmail-pass').value;
    
    try {
        const response = await fetch('/test_email', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ email, password })
        });
        
        const result = await response.json();
        if (result.success) {
            alert('Test email sent successfully!');
        } else {
            alert('Failed to send test email: ' + result.error);
        }
    } catch (error) {
        alert('Error testing email notification: ' + error.message);
    }
}

async function saveNotificationSettings() {
    const settings = {
        notifications: {
            orders: document.getElementById('notify-orders').checked,
            alerts: document.getElementById('notify-alerts').checked,
            signals: document.getElementById('notify-signals').checked,
            pnl: document.getElementById('notify-pnl').checked
        },
        telegram: {
            enabled: document.getElementById('enable-telegram').checked,
            token: document.getElementById('telegram-token').value,
            chat_id: document.getElementById('telegram-chat-id').value
        },
        email: {
            enabled: document.getElementById('enable-email').checked,
            user: document.getElementById('gmail-user').value,
            pass: document.getElementById('gmail-pass').value
        }
    };
    
    try {
        const response = await fetch('/save_notification_settings', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(settings)
        });
        
        const result = await response.json();
        if (result.success) {
            alert('Notification settings saved successfully!');
        } else {
            alert('Failed to save settings: ' + result.error);
        }
    } catch (error) {
        alert('Error saving notification settings: ' + error.message);
    }
}

// Toggle notification channel settings visibility (only if Settings tab exists)
const tgToggle = document.getElementById('enable-telegram');
if (tgToggle) {
    tgToggle.addEventListener('change', function() {
        const settings = document.querySelector('.telegram-settings');
        if (settings) settings.style.display = this.checked ? 'block' : 'none';
    });
}
const emailToggle = document.getElementById('enable-email');
if (emailToggle) {
    emailToggle.addEventListener('change', function() {
        const settings = document.querySelector('.email-settings');
        if (settings) settings.style.display = this.checked ? 'block' : 'none';
    });
}

// Load notification settings
async function loadNotificationSettings() {
    try {
        const response = await fetch('/get_notification_settings');
        const settings = await response.json();
        
        // Update Telegram settings
        document.getElementById('enable-telegram').checked = settings.telegram.enabled;
        document.getElementById('telegram-token').value = settings.telegram.token;
        document.getElementById('telegram-chat-id').value = settings.telegram.chat_id;
        
        // Update Email settings
        document.getElementById('enable-email').checked = settings.email.enabled;
        document.getElementById('gmail-user').value = settings.email.user;
        document.getElementById('gmail-pass').value = settings.email.pass;
        
        // Update visibility
        document.querySelector('.telegram-settings').style.display = settings.telegram.enabled ? 'block' : 'none';
        document.querySelector('.email-settings').style.display = settings.email.enabled ? 'block' : 'none';
    } catch (error) {
        console.error('Error loading notification settings:', error);
    }
} 