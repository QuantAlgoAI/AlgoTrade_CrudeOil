console.log("app.js loaded and running");

// === SETTINGS TAB DYNAMIC FORM ===
async function loadStrategyParams() {
  try {
    const res = await fetch('/api/strategy_params_schema');
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || res.statusText);
    const params = data.params;
    const formDiv = document.getElementById('settings-form');
    if (!formDiv) return;
    formDiv.innerHTML = '';
    params.forEach(p => {
      const row = document.createElement('div');
      row.className = 'form-group row mb-2';
      row.innerHTML = `
        <label class="col-sm-4 col-form-label">${p.name}</label>
        <div class="col-sm-8">
          <input type="${p.type === 'bool' || p.type === 'boolean' ? 'checkbox' : 'number'}" 
                 class="form-control" id="param-${p.name}" 
                 ${p.type === 'bool' || p.type === 'boolean' ? (p.current ? 'checked' : '') : `value="${p.current}"`}>
        </div>`;
      formDiv.appendChild(row);
    });
  } catch (err) {
    console.error('Unable to load params', err);
  }
}

async function saveStrategyParams() {
  try {
    const formDiv = document.getElementById('settings-form');
    if (!formDiv) return;
    const inputs = formDiv.querySelectorAll('input');
    const payload = {};
    inputs.forEach(inp => {
      const key = inp.id.replace('param-','');
      if (inp.type === 'checkbox') {
        payload[key] = inp.checked;
      } else {
        payload[key] = Number(inp.value);
      }
    });
    const res = await fetch('/api/update_strategy_params', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (res.ok) {
      alert('Parameters updated');
    } else {
      alert('Update error: '+ (data.error||res.statusText));
    }
  } catch(err) {
    alert('Update error: '+ err);
  }
}

// === PRESET JSON LOADER ===
function handlePresetFile(event) {
  const file = event.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = function(e) {
    try {
      const params = JSON.parse(e.target.result);
      const formDiv = document.getElementById('settings-form');
      if (!formDiv) return;
      Object.entries(params).forEach(([k,v]) => {
        const inp = document.getElementById('param-'+k);
        if (!inp) return;
        if (inp.type === 'checkbox') {
          inp.checked = Boolean(v);
        } else {
          inp.value = v;
        }
      });
      alert('Preset loaded – review & press Save');
    } catch(err) {
      alert('Invalid JSON preset');
    }
  };
  reader.readAsText(file);
}

document.addEventListener('DOMContentLoaded', () => {
  // Load params when Settings tab is opened
  const settingsTabBtn = document.getElementById('tab-settings');
  if (settingsTabBtn) {
    settingsTabBtn.addEventListener('click', loadStrategyParams);
  }
  const saveBtn = document.getElementById('btn-save-params');
  if (saveBtn) {
    saveBtn.addEventListener('click', saveStrategyParams);
  }
  // Initial load if user lands on settings first
  if (document.getElementById('settings-form')) loadStrategyParams();

  // File input for presets
  const presetInput = document.getElementById('preset-file');
  if (presetInput) presetInput.addEventListener('change', handlePresetFile);

});

// Initialize Socket.IO connection
const socket = io();

// WebSocket connection status
socket.on('connect', () => {
    console.log('Connected to WebSocket');
});

socket.on('disconnect', () => {
    console.log('Disconnected from WebSocket');
});

// === Global Variables ===
let refreshInterval = 5000;
let chartsTimer, infoTimer, strategyTimer;
let currentTab = 'fut';
let charts = {
    fut: { ohlc: null, volume: null },
    ce: { ohlc: null, volume: null },
    pe: { ohlc: null, volume: null }
};
let indicatorToggles = {
    'fast_ema': true,
    'slow_ema': true,
    'vwap': true,
    'rsi': true,
    'atr': true
};

// === Store latest info and market data ===
let instrumentInfo = {}; // filled by fetchInfo()

// === Store latest market data for all instruments ===
let latestMarketData = {
    FUT: { ltp: '--', volume: '--', oi: '--' },
    CE: { ltp: '--', volume: '--', oi: '--' },
    PE: { ltp: '--', volume: '--', oi: '--' }
};

// === Update LTP/Volume/OI UI for a given type ===
function updateLtpVolOiUI(type) {
    const d = latestMarketData[type];
    document.getElementById('ltp-val').textContent =
        (typeof d.ltp === 'number' && !isNaN(d.ltp)) ? d.ltp.toFixed(2) : '--';
    document.getElementById('vol-val').textContent =
        (typeof d.volume === 'number' && !isNaN(d.volume)) ? d.volume : '--';
    document.getElementById('oi-val').textContent =
        (typeof d.oi === 'number' && !isNaN(d.oi)) ? d.oi : '--';
}

// === WebSocket: Market data updates ===
socket.on('market_data', (data) => {
    if (data.type && latestMarketData[data.type]) {
        latestMarketData[data.type] = {
            ltp: data.ltp,
            volume: data.volume,
            oi: data.oi
        };
    }
    // If the tick is for the current tab, update the UI
    if (data.type === currentTab.toUpperCase()) {
        updateLtpVolOiUI(data.type);
    }
    updateCharts();
});

// === On tab switch, show latest value for that instrument ===
function showTab(tab) {
    ['fut', 'ce', 'pe'].forEach(t => {
        document.getElementById(`tab-${t}`).classList.toggle('active', t === tab);
        document.getElementById(`content-${t}`).classList.toggle('active', t === tab);
    });
    currentTab = tab;
    updateLtpVolOiUI(tab.toUpperCase());
    updateCharts();
}

// Strategy updates
socket.on('strategy_update', (data) => {
    console.log('Received strategy update:', data);
    const prefix = data.type.toLowerCase();
    if (data.indicators) {
        Object.entries(data.indicators).forEach(([key, value]) => {
            const elementId = `${prefix}-${key.replace('_', '-')}`;
            const element = document.getElementById(elementId);
            if (element) {
                element.textContent = format2(value);
            }
        });
    }
});

// Log all JS errors globally
window.onerror = function(message, source, lineno, colno, error) {
    console.error('Global JS Error:', message, 'at', source + ':' + lineno + ':' + colno, error);
};

// === Initialization ===
document.addEventListener('DOMContentLoaded', () => {
    // Initialize charts
    initializeCharts();
    
    // Start data refresh
    startDataRefresh();
    
    // Initialize form handlers
    initializeFormHandlers();

    // Fetch strategy status immediately on load
    fetchStrategyStatus().then(updateStrategyStatus);
});

// === Navigation Functions ===
function showMainTab(tab) {
    const tabs = ['dashboard', 'trade', 'strategy', 'settings'];
    tabs.forEach(t => {
        document.getElementById(`main-${t}`).style.display = (t === tab) ? '' : 'none';
        document.getElementById(`nav-${t}`).classList.toggle('active', t === tab);
    });
}

// === Data Fetching Functions ===
async function fetchOHLC() {
    const interval = document.getElementById('ohlc-interval').value;
    try {
        const res = await fetch('/ohlc?interval=' + encodeURIComponent(interval));
        return await res.json();
    } catch (error) {
        console.error('Error fetching OHLC data:', error);
        return { fut: [], ce: [], pe: [] };
    }
}

async function fetchInfo() {
    try {
        const res = await fetch('/info');
        return await res.json();
    } catch (error) {
        console.error('Error fetching info:', error);
        return {};
    }
}

async function fetchStrategyStatus() {
    try {
        const res = await fetch('/strategy_status');
        return await res.json();
    } catch (error) {
        console.error('Error fetching strategy status:', error);
        return {};
    }
}

// === UI Update Functions ===
function updateInfoBoxes(info) {
    instrumentInfo = info;
    const updateElement = (id, value) => {
        const element = document.getElementById(id);
        if (element) element.textContent = value || '';
    };

    if (info.fut) {
        updateElement('fut-symbol', info.fut.symbol);
        updateElement('fut-expiry', info.fut.expiry);
        updateElement('fut-lotsize', info.fut.lotsize);
    }
    if (info.ce) {
        updateElement('ce-symbol', info.ce.symbol);
        updateElement('ce-strike', info.ce.strike ? ((Number(info.ce.strike) / 100).toFixed(2)) : '');
        updateElement('ce-expiry', info.ce.expiry);
        updateElement('ce-lotsize', info.ce.lotsize);
    }
    if (info.pe) {
        updateElement('pe-symbol', info.pe.symbol);
        updateElement('pe-strike', info.pe.strike ? ((Number(info.pe.strike) / 100).toFixed(2)) : '');
        updateElement('pe-expiry', info.pe.expiry);
        updateElement('pe-lotsize', info.pe.lotsize);
    }
}

function format2(val) {
    if (val === undefined || val === null || val === "--") return "--";
    if (typeof val === "number") return val.toFixed(2);
    if (!isNaN(val)) return Number(val).toFixed(2);
    return val;
}

function updateStrategyStatus(status) {
    console.log("updateStrategyStatus called", status);
    const updateIndicators = (prefix, data) => {
        if (!data || !data.indicators) {
            console.warn(`No indicators data for ${prefix}`);
            return;
        }
        
        const indicators = data.indicators;
        const elementMappings = {
            [`${prefix}-fast-ema`]: indicators.fast_ema,
            [`${prefix}-slow-ema`]: indicators.slow_ema,
            [`${prefix}-rsi`]: indicators.rsi,
            [`${prefix}-vwap`]: indicators.vwap,
            [`${prefix}-atr`]: indicators.atr,
            [`${prefix}-regime`]: indicators.market_regime
        };
        
        // Update each indicator element if it exists
        Object.entries(elementMappings).forEach(([elementId, value]) => {
            const element = document.getElementById(elementId);
            if (element) {
                element.textContent = format2(value);
            }
        });
        
        // Log for debugging
        console.log(`Updated ${prefix} indicators:`, indicators);
    };

    // Update CE and PE indicators
    if (status.ce) updateIndicators('ce', status.ce);
    if (status.pe) updateIndicators('pe', status.pe);
}

// === Chart Functions ===
function initializeCharts() {
    // Initialize a single chart container for each type
    ['fut', 'ce', 'pe'].forEach(type => {
        const container = document.getElementById('chart-' + type);
        if (container) {
            Plotly.newPlot(container, [], {
                template: 'plotly_dark',
                paper_bgcolor: '#1E2430',
                plot_bgcolor: '#1E2430',
                font: { color: '#E0E0E0' }
            }, { responsive: true });
        }
    });

    // Add event listener for interval changes
    const intervalSelector = document.getElementById('ohlc-interval');
    if (intervalSelector) {
        intervalSelector.addEventListener('change', () => {
            console.log(`Interval changed to: ${intervalSelector.value}. Updating charts.`);
            updateCharts();
        });
    }
}

function updateCharts() {
    fetchOHLC().then(data => {
        const tab = currentTab;
        let ohlcArr = data[tab] || [];
        if (!ohlcArr.length) {
            Plotly.purge(`chart-${tab}`);
            return;
        }
        // Prepare data arrays
        const x = ohlcArr.map(d => d.timestamp);
        const open = ohlcArr.map(d => d.open);
        const high = ohlcArr.map(d => d.high);
        const low = ohlcArr.map(d => d.low);
        const close = ohlcArr.map(d => d.close);
        const volume = ohlcArr.map(d => d.volume);
        // Indicator lines
        const fastEma = ohlcArr.map(d => d.fast_ema);
        const slowEma = ohlcArr.map(d => d.slow_ema);
        const vwap = ohlcArr.map(d => d.vwap);
        const rsi = ohlcArr.map(d => d.rsi);

        // Calculate volume colors based on price movement
        const volumeColors = ohlcArr.map((d, i) => {
            if (i === 0) return d.close >= d.open ? '#26A69A' : '#EF5350';
            return d.close >= ohlcArr[i-1].close ? '#26A69A' : '#EF5350';
        });

        // Traces: OHLC, indicators, and volume
        const traces = [
            {
                x, open, high, low, close,
                type: 'candlestick',
                name: 'OHLC',
                increasing: { line: { color: '#26A69A' }, fillcolor: '#26A69A' },
                decreasing: { line: { color: '#EF5350' }, fillcolor: '#EF5350' },
                xaxis: 'x', yaxis: 'y',
                hovertemplate: 'O: %{open}<br>H: %{high}<br>L: %{low}<br>C: %{close}<extra></extra>'
            },
            {
                x, y: fastEma, type: 'scatter', mode: 'lines', name: 'Fast EMA', 
                line: { color: '#00BCD4', width: 2 }, 
                xaxis: 'x', yaxis: 'y', 
                visible: indicatorToggles.fast_ema ? true : 'legendonly',
                hovertemplate: 'Fast EMA: %{y:.2f}<extra></extra>'
            },
            {
                x, y: slowEma, type: 'scatter', mode: 'lines', name: 'Slow EMA', 
                line: { color: '#FF9800', width: 2 }, 
                xaxis: 'x', yaxis: 'y', 
                visible: indicatorToggles.slow_ema ? true : 'legendonly',
                hovertemplate: 'Slow EMA: %{y:.2f}<extra></extra>'
            },
            {
                x, y: vwap, type: 'scatter', mode: 'lines', name: 'VWAP', 
                line: { color: '#AB47BC', width: 2, dash: 'dot' }, 
                xaxis: 'x', yaxis: 'y', 
                visible: indicatorToggles.vwap ? true : 'legendonly',
                hovertemplate: 'VWAP: %{y:.2f}<extra></extra>'
            },
            {
                x, y: volume, type: 'bar', name: 'Volume', 
                marker: { color: volumeColors, opacity: 0.8 }, 
                xaxis: 'x', yaxis: 'y2',
                hovertemplate: 'Volume: %{y:,.0f}<extra></extra>'
            },
            {
                x, y: rsi, type: 'scatter', mode: 'lines', name: 'RSI',
                line: { color: '#7E57C2', width: 2 },
                xaxis: 'x', yaxis: 'y3',
                visible: indicatorToggles.rsi ? true : 'legendonly',
                hovertemplate: 'RSI: %{y:.2f}<extra></extra>'
            }
        ];

        const layout = {
            template: 'plotly_dark',
            paper_bgcolor: '#1E2430',
            plot_bgcolor: '#1E2430',
            font: { color: '#E0E0E0', family: 'Roboto, sans-serif' },
            width: 1200,
            height: 700,
            margin: { t: 30, l: 60, r: 30, b: 20 },
            xaxis: { 
                domain: [0, 1], 
                anchor: 'y',
                rangeslider: { visible: false },
                showgrid: true,
                gridcolor: '#2f3a4a',
                type: 'date',
                tickformat: '%H:%M:%S',
                hoverformat: '%Y-%m-%d %H:%M:%S'
            },
            yaxis: { 
                domain: [0.45, 1],
                anchor: 'x',
                title: { text: 'Price', standoff: 5 },
                showgrid: true,
                gridcolor: '#2f3a4a',
                automargin: true,
                tickformat: '.2f'
            },
            yaxis2: { 
                domain: [0.25, 0.4],
                anchor: 'x',
                title: { text: 'Volume', standoff: 5 },
                showgrid: true,
                gridcolor: '#2f3a4a',
                automargin: true,
                tickformat: ',.0f'
            },
            yaxis3: {
                domain: [0, 0.2],
                anchor: 'x',
                title: { text: 'RSI', standoff: 5 },
                showgrid: true,
                gridcolor: '#2f3a4a',
                automargin: true,
                tickformat: '.2f'
            },
            showlegend: true,
            legend: { 
                bgcolor: '#1E2430',
                font: { color: '#E0E0E0' },
                orientation: 'h',
                x: 0.5,
                xanchor: 'center',
                y: 1.05,
                yanchor: 'bottom'
            },
            hovermode: 'x unified',
            hoverlabel: {
                bgcolor: '#1E2430',
                font: { color: '#E0E0E0' }
            }
        };

        Plotly.newPlot(`chart-${tab}`, traces, layout, {
            responsive: true,
            displayModeBar: true,
            displaylogo: false,
            modeBarButtonsToRemove: ['lasso2d', 'select2d'],
            toImageButtonOptions: {
                format: 'png',
                filename: `${tab}_chart`,
                height: 600,
                width: 1200,
                scale: 2
            }
        });
    });
}

// === Info Functions ===
function updateInfo() {
    fetch('/info')
        .then(response => response.json())
        .then(data => {
            // Update futures info
            if (data.fut) {
                const futSymbol = document.getElementById('fut-symbol');
                if (futSymbol) futSymbol.textContent = data.fut.symbol || '--';
                const futExpiry = document.getElementById('fut-expiry');
                if (futExpiry) futExpiry.textContent = data.fut.expiry || '--';
                const futLotsize = document.getElementById('fut-lotsize');
                if (futLotsize) futLotsize.textContent = data.fut.lotsize || '--';
                const futLtp = document.getElementById('fut-ltp');
                if (futLtp) futLtp.textContent = data.fut.ltp || '--';
                const futVolume = document.getElementById('fut-volume');
                if (futVolume) futVolume.textContent = data.fut.volume || '--';
                const futOi = document.getElementById('fut-oi');
                if (futOi) futOi.textContent = data.fut.oi || '--';
            }
            
            // Update CE info
            if (data.ce) {
                const ceSymbol = document.getElementById('ce-symbol');
                if (ceSymbol) ceSymbol.textContent = data.ce.symbol || '--';
                const ceStrike = document.getElementById('ce-strike');
                // Convert strike from paise to rupees with proper formatting
                if (ceStrike) {
                    const strikeInRupees = data.ce.strike ? (Number(data.ce.strike) / 100).toFixed(2) : '--';
                    ceStrike.textContent = strikeInRupees !== '--' ? '₹' + strikeInRupees : '--';
                }
                const ceExpiry = document.getElementById('ce-expiry');
                if (ceExpiry) ceExpiry.textContent = data.ce.expiry || '--';
                const ceLotsize = document.getElementById('ce-lotsize');
                if (ceLotsize) ceLotsize.textContent = data.ce.lotsize || '--';
                const ceLtp = document.getElementById('ce-ltp');
                if (ceLtp) ceLtp.textContent = data.ce.ltp || '--';
                const ceVolume = document.getElementById('ce-volume');
                if (ceVolume) ceVolume.textContent = data.ce.volume || '--';
                const ceOi = document.getElementById('ce-oi');
                if (ceOi) ceOi.textContent = data.ce.oi || '--';
            }
            
            // Update PE info
            if (data.pe) {
                const peSymbol = document.getElementById('pe-symbol');
                if (peSymbol) peSymbol.textContent = data.pe.symbol || '--';
                const peStrike = document.getElementById('pe-strike');
                // Convert strike from paise to rupees with proper formatting
                if (peStrike) {
                    const strikeInRupees = data.pe.strike ? (Number(data.pe.strike) / 100).toFixed(2) : '--';
                    peStrike.textContent = strikeInRupees !== '--' ? '₹' + strikeInRupees : '--';
                }
                const peExpiry = document.getElementById('pe-expiry');
                if (peExpiry) peExpiry.textContent = data.pe.expiry || '--';
                const peLotsize = document.getElementById('pe-lotsize');
                if (peLotsize) peLotsize.textContent = data.pe.lotsize || '--';
                const peLtp = document.getElementById('pe-ltp');
                if (peLtp) peLtp.textContent = data.pe.ltp || '--';
                const peVolume = document.getElementById('pe-volume');
                if (peVolume) peVolume.textContent = data.pe.volume || '--';
                const peOi = document.getElementById('pe-oi');
                if (peOi) peOi.textContent = data.pe.oi || '--';
            }
            
            // Update account info
            if (data.account) {
                const accBal = document.getElementById('account-balance');
                if (accBal) accBal.textContent = data.account.balance || '--';
                const accPnl = document.getElementById('account-pnl');
                if (accPnl) accPnl.textContent = data.account.pnl || '--';
            }
        })
        .catch(error => console.error('Error updating info:', error));
}

// Comment out updatePositions and updateTrades if endpoints are not implemented
// function updatePositions() { ... }
// function updateTrades() { ... }

// === Form Handlers ===
function initializeFormHandlers() {
    // Track user modifications to prevent auto-overwriting
    ['fast-ema-period', 'slow-ema-period', 'rsi-period', 'atr-period', 'vwap-period'].forEach(id => {
        const element = document.getElementById(id);
        if (element) {
            element.addEventListener('input', function() {
                this.dataset.userModified = 'true';
            });
            element.addEventListener('focus', function() {
                this.dataset.userModified = 'true';
            });
        }
    });

    // Order form
    const orderForm = document.getElementById('order-form');
    if (orderForm) {
        orderForm.addEventListener('submit', async function(e) {
            e.preventDefault();
            const instrument = document.getElementById('order-instrument').value;
            const side = document.querySelector('input[name="transaction"]:checked').value;
            const orderType = document.getElementById('order-type').value;
            const qty = parseInt(document.getElementById('order-qty').value);
            const price = parseFloat(document.getElementById('order-price').value) || null;

            // Quick mapping – in real app you should fetch token + tradingSymbol from backend.
            const mapping = {
                fut: { symbol_token: 'FUT', trading_symbol: 'CRUDEOILFUT' },
                ce: { symbol_token: 'CE', trading_symbol: 'CRUDEOILCE' },
                pe: { symbol_token: 'PE', trading_symbol: 'CRUDEOILPE' },
            };
            const map = mapping[instrument] || {};
            const tradingSymbol = (instrumentInfo[instrument] && instrumentInfo[instrument].symbol) || map.trading_symbol;

            const payload = {
                symbol_token: map.symbol_token,
                trading_symbol: tradingSymbol,
                side: side,
                qty: qty,
                order_type: orderType,
                price: price,
            };
            console.log('Sending order', payload);
            try {
                const res = await fetch('/api/trade', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (res.ok) {
                    alert('Order placed: ' + data.order_id);
                    // Add to orders history table immediately
                    const now = new Date().toLocaleTimeString();
                    const row = document.createElement('tr');
                    row.innerHTML = `
                        <td>${now}</td>
                        <td>${map.trading_symbol}</td>
                        <td>${side}</td>
                        <td>${qty}</td>
                        <td>${orderType === 'MARKET' ? '--' : (price || '--')}</td>
                        <td>${data.order_id}</td>`;
                    const ordersTbl = document.getElementById('orders-table');
                    if (ordersTbl) ordersTbl.prepend(row);

                    // Add to open positions table
                    const posRow = document.createElement('tr');
                    posRow.setAttribute('data-order-id', data.order_id);
                    posRow.innerHTML = `
                        <td>${map.trading_symbol}</td>
                        <td>${side}</td>
                        <td>${qty}</td>
                        <td>${orderType === 'MARKET' ? '--' : (price || '--')}</td>
                        <td>--</td>
                        <td>--</td>
                        <td><button class="btn btn-sm btn-danger" onclick="closePosition('${data.order_id}', '${map.trading_symbol}', ${qty}, ${price || null})">Close</button></td>`;
                    const posTbl = document.getElementById('positions-table');
                    if (posTbl) posTbl.prepend(posRow);
                } else {
                    alert('Order error: ' + (data.error || res.statusText));
                }
            } catch (err) {
                console.error('Order error', err);
                alert('Order error: ' + err);
            }
        });
    }

    // Strategy form
    document.getElementById('strategy-form').addEventListener('submit', function(e) {
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
            use_atr: document.getElementById('use-atr').checked
        };
        
        console.log('Sending strategy parameters:', params);
        
        fetch('/strategy_params', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(params)
        })
        .then(response => response.json())
        .then(data => {
            if (data.status === 'ok') {
                alert('Strategy parameters updated successfully');
                // Clear user modification flags after successful update
                ['fast-ema-period', 'slow-ema-period', 'rsi-period', 'atr-period', 'vwap-period'].forEach(id => {
                    const element = document.getElementById(id);
                    if (element) element.dataset.userModified = 'false';
                });
            } else {
                alert('Error updating strategy parameters');
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert('Error updating strategy parameters');
        });
    });
}

// === Refresh Functions ===
function startDataRefresh() {
    // Initial updates
    updateCharts();
    updateInfo();
    // updatePositions();
    // updateTrades();
    
    // Set refresh intervals
    chartsTimer = setInterval(updateCharts, refreshInterval);
    infoTimer = setInterval(updateInfo, 1000);
    // strategyTimer = setInterval(() => {
    //     updatePositions();
    //     updateTrades();
    // }, 5000);
}

function updateIntervals() {
    const chartInterval = parseInt(document.getElementById('ohlc-interval').value);
    const infoInterval = parseInt(document.getElementById('info-interval').value);
    
    // Clear existing intervals
    clearInterval(chartsTimer);
    clearInterval(infoTimer);
    
    // Set new intervals
    chartsTimer = setInterval(updateCharts, chartInterval);
    infoTimer = setInterval(updateInfo, infoInterval);
    
    alert('Refresh intervals updated successfully');
}

function updateApiSettings() {
    const settings = {
        api_key: document.getElementById('api-key').value,
        client_code: document.getElementById('client-code').value,
        password: document.getElementById('password').value,
        totp_secret: document.getElementById('totp-secret').value
    };
    
    // Save to localStorage
    Object.entries(settings).forEach(([key, value]) => {
        if (value) localStorage.setItem(key, value);
    });
    
    alert('API settings updated successfully');
}

// === Event Listeners ===
document.addEventListener('DOMContentLoaded', function() {
    // Initialize indicator toggles
    ['show-fast-ema', 'show-slow-ema', 'show-vwap', 'show-rsi', 'show-atr'].forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.addEventListener('change', function() {
                const key = id.replace('show-', '').replace('-', '_');
                indicatorToggles[key] = this.checked;
                updateCharts();
            });
        }
    });

    // Strategy tab click
    const navStrat = document.getElementById('nav-strategy');
    if (navStrat) navStrat.addEventListener('click', loadStrategyParams);

    // Add CSS for chart containers
    const style = document.createElement('style');
    style.textContent = `
        .chart-container {
            background: #1E2430;
            border-radius: 4px;
            box-shadow: 0 2px 4px rgba(0, 0, 0, 0.2);
        }
        .chart-ohlc {
            margin-bottom: 4px;
        }
        .chart-volume {
            margin-top: 4px;
        }
        .js-plotly-plot .plotly .modebar {
            background: #1E2430 !important;
        }
        .js-plotly-plot .plotly .modebar-btn path {
            fill: #E0E0E0 !important;
        }
    `;
    document.head.appendChild(style);
});

// === Strategy Parameter Management ===
async function loadStrategyParams() {
    try {
        const res = await fetch('/strategy_params');
        const params = await res.json();
        
        // Only load parameters if they haven't been modified by the user
        // Check if any input field has been focused or changed
        const hasUserChanges = ['fast-ema-period', 'slow-ema-period', 'rsi-period', 'atr-period', 'vwap-period']
            .some(id => {
                const element = document.getElementById(id);
                return element && (element.dataset.userModified === 'true');
            });
        
        if (!hasUserChanges) {
            ['fast-ema', 'slow-ema', 'rsi', 'atr', 'vwap'].forEach(param => {
                const element = document.getElementById(`${param}-period`);
                if (element) element.value = params[`${param.replace('-', '_')}_period`];
            });

            ['fast-ema', 'slow-ema', 'rsi', 'atr', 'vwap'].forEach(param => {
                const element = document.getElementById(`use-${param}`);
                if (element) element.checked = params[`use_${param.replace('-', '_')}`];
            });
        }
    } catch (error) {
        console.error('Error loading strategy parameters:', error);
    }
}

// === Timer Management ===
function setTimers() {
    [chartsTimer, infoTimer, strategyTimer].forEach(timer => {
        if (timer) clearInterval(timer);
    });

    chartsTimer = setInterval(updateCharts, refreshInterval);
    infoTimer = setInterval(updateInfo, refreshInterval);
}

// === Initialize Application ===
setTimers();
updateCharts();
updateInfo();

// Add this at the end of the file or after other timers
setInterval(() => {
    fetchStrategyStatus().then(updateStrategyStatus);
}, 5000); // every 5 seconds

// === Chart Configuration ===
const chartConfig = {
    responsive: true,
    displayModeBar: true,
    displaylogo: false,
    modeBarButtonsToRemove: ['lasso2d', 'select2d'],
    toImageButtonOptions: {
        format: 'png',
        filename: 'chart',
        height: 500,
        width: 1200,
        scale: 2
    }
};

const chartLayout = {
    template: 'plotly_dark',
    paper_bgcolor: '#1E2430',
    plot_bgcolor: '#1E2430',
    font: {
        color: '#E0E0E0',
        family: 'Roboto, sans-serif'
    },
    xaxis: {
        gridcolor: '#2f3a4a',
        zerolinecolor: '#2f3a4a',
        type: 'date',
        rangeslider: { visible: false }
    },
    yaxis: {
        gridcolor: '#2f3a4a',
        zerolinecolor: '#2f3a4a'
    },
    showlegend: true,
    legend: {
        bgcolor: '#1E2430',
        font: { color: '#E0E0E0' }
    }
};

// Update chart colors
const chartColors = {
    candlestick: {
        increasing: { line: { color: '#26A69A' }, fillcolor: '#26A69A' },
        decreasing: { line: { color: '#EF5350' }, fillcolor: '#EF5350' }
    },
    volume: {
        increasing: '#26A69A',
        decreasing: '#EF5350'
    },
    indicators: {
        fast_ema: '#00BCD4',
        slow_ema: '#FF9800',
        vwap: '#AB47BC'
    }
};

// === Chart Update Functions ===
function updateInstrumentCharts(instrument, data) {
    if (!data || !data.length) {
        console.warn(`No data for ${instrument} charts`);
        return;
    }

    const timestamps = data.map(d => d.timestamp);
    const ohlc = {
        x: timestamps,
        open: data.map(d => d.open),
        high: data.map(d => d.high),
        low: data.map(d => d.low),
        close: data.map(d => d.close),
        type: 'candlestick',
        name: 'OHLC',
        ...chartColors.candlestick
    };

    const volume = {
        x: timestamps,
        y: data.map(d => d.volume),
        type: 'bar',
        name: 'Volume',
        marker: {
            color: data.map((d, i) => 
                (i > 0 ? d.close >= data[i-1].close : d.close >= d.open) 
                    ? chartColors.volume.increasing 
                    : chartColors.volume.decreasing
            )
        }
    };

    // Add indicators
    const traces = [ohlc];
    
    if (data[0].fast_ema !== undefined && indicatorToggles.fast_ema) {
        traces.push({
            x: timestamps,
            y: data.map(d => d.fast_ema),
            type: 'scatter',
            mode: 'lines',
            name: 'Fast EMA',
            line: { color: chartColors.indicators.fast_ema }
        });
    }

    if (data[0].slow_ema !== undefined && indicatorToggles.slow_ema) {
        traces.push({
            x: timestamps,
            y: data.map(d => d.slow_ema),
            type: 'scatter',
            mode: 'lines',
            name: 'Slow EMA',
            line: { color: chartColors.indicators.slow_ema }
        });
    }

    if (data[0].vwap !== undefined && indicatorToggles.vwap) {
        traces.push({
            x: timestamps,
            y: data.map(d => d.vwap),
            type: 'scatter',
            mode: 'lines',
            name: 'VWAP',
            line: { color: chartColors.indicators.vwap }
        });
    }

    const ohlcLayout = {
        ...chartLayout,
        height: 500,
        dragmode: 'zoom',
        margin: { r: 10, t: 25, b: 40, l: 60 },
        title: instrument.toUpperCase()
    };

    const volumeLayout = {
        ...chartLayout,
        height: 150,
        margin: { r: 10, t: 0, b: 20, l: 60 },
        showlegend: false
    };

    Plotly.react(`chart-${instrument}`, traces, ohlcLayout, chartConfig);
}