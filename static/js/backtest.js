// Enhanced Backtest JavaScript for unified UI
class BacktestManager {
    constructor() {
        this.currentTaskId = null;
        this.pollTimer = null;
        this.collectionSocket = null;

        this.initializeComponents();
        this.setupEventListeners();
        this.refreshDataStatus();
    }

    initializeComponents() {
        // Initialize date pickers
        this.initializeDatePickers();
        
        // Initialize numeric parameter inputs (no sliders needed)
        // this.initializeSliders();
        
        // Initialize data status refresh
        this.refreshDataStatus();
    }

    initializeDatePickers() {
        const startDate = flatpickr("#start-date", {
            dateFormat: "Y-m-d",
            defaultDate: new Date(Date.now() - 30 * 24 * 60 * 60 * 1000), // 30 days ago
            maxDate: new Date()
        });

        const endDate = flatpickr("#end-date", {
            dateFormat: "Y-m-d",
            defaultDate: new Date(),
            maxDate: new Date()
        });
    }

    initializeSliders() {
        const sliders = [
            { id: 'fast-ema-slider', min: 2, max: 20, start: 9, valueId: 'fast-ema-value' },
            { id: 'slow-ema-slider', min: 5, max: 50, start: 21, valueId: 'slow-ema-value' },
            { id: 'rsi-slider', min: 2, max: 30, start: 14, valueId: 'rsi-value' },
            { id: 'vwap-slider', min: 5, max: 50, start: 20, valueId: 'vwap-value' },
            { id: 'atr-slider', min: 5, max: 30, start: 14, valueId: 'atr-value' }
        ];

        sliders.forEach(slider => {
            const element = document.getElementById(slider.id);
            if (element) {
                noUiSlider.create(element, {
                    start: [slider.start],
                    connect: [true, false],
                    range: {
                        'min': slider.min,
                        'max': slider.max
                    },
                    step: 1,
                    format: {
                        to: function (value) {
                            return Math.round(value);
                        },
                        from: function (value) {
                            return Number(value);
                        }
                    }
                });

                element.noUiSlider.on('update', function (values, handle) {
                    document.getElementById(slider.valueId).textContent = values[handle];
                });
            }
        });
    }

    setupEventListeners() {
        // Data collection buttons
        document.getElementById('start-collection')?.addEventListener('click', () => this.startDataCollection());
        document.getElementById('stop-collection')?.addEventListener('click', () => this.stopDataCollection());
        document.getElementById('prepare-data')?.addEventListener('click', () => this.prepareHistoricalData());
        document.getElementById('refresh-status')?.addEventListener('click', () => this.refreshDataStatus());
        
        // Backtest button
        document.getElementById('run-backtest')?.addEventListener('click', () => this.runBacktest());

        // Load/Save parameter set buttons
        document.getElementById('load-params')?.addEventListener('click', () => {
            document.getElementById('params-file-input').click();
        });
        document.getElementById('save-params')?.addEventListener('click', () => this.saveParameterSet());
        document.getElementById('params-file-input')?.addEventListener('change', (e) => this.loadParameterSet(e));
        
        // Tab switching
        document.querySelectorAll('[data-bs-toggle="tab"]').forEach(tab => {
            tab.addEventListener('shown.bs.tab', (event) => {
                if (event.target.id === 'results-tab') {
                    this.refreshResults();
                }
            });
        });
    }

    async refreshDataStatus() {
        try {
            const response = await fetch('/api/data_status');
            const data = await response.json();
            
            this.updateDataStatus(data);
        } catch (error) {
            console.error('Error refreshing data status:', error);
            this.showAlert('Error refreshing data status', 'danger');
        }
    }

    updateDataStatus(data) {
        // Update available dates
        const datesElement = document.getElementById('available-dates');
        if (data.available_dates && data.available_dates.length > 0) {
            datesElement.innerHTML = data.available_dates.map(date => 
                `<span class="badge bg-success me-1">${date}</span>`
            ).join('');
        } else {
            datesElement.innerHTML = '<span class="text-muted">No data available</span>';
        }
        
        // Update total files
        document.getElementById('total-files').textContent = data.total_files || 0;
        
        // Update data readiness
        const readinessElement = document.getElementById('data-readiness');
        if (data.is_ready) {
            readinessElement.textContent = 'Ready';
            readinessElement.className = 'badge bg-success';
        } else {
            readinessElement.textContent = 'Insufficient Data';
            readinessElement.className = 'badge bg-warning';
        }
    }

    async startDataCollection() {
        const days = document.getElementById('collection-days').value;
        const filter = document.querySelector('input[name="options-filter"]:checked').value;
        
        try {
            this.showProgress();
            
            const response = await fetch('/api/start_data_collection', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    days: parseInt(days),
                    filter: filter
                })
            });
            
            const result = await response.json();
            
            if (result.success) {
                this.showAlert('Data collection started successfully', 'success');
                this.currentTaskId = result.task_id;
                this.startProgressMonitoring(result.task_id);
                document.getElementById('start-collection').disabled = true;
                document.getElementById('stop-collection').disabled = false;
            } else {
                this.showAlert(`Failed to start data collection: ${result.error}`, 'danger');
                this.hideProgress();
            }
        } catch (error) {
            console.error('Error starting data collection:', error);
            this.showAlert('Error starting data collection', 'danger');
            this.hideProgress();
        }
    }

    async stopDataCollection() {
        if (!this.currentTaskId) return;
        try {
            const response = await fetch('/api/stop_data_collection', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ task_id: this.currentTaskId })
            });
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || 'Failed to stop data collection');
            }
            this.showAlert('Data collection stopped', 'info');
            this.hideProgress();
            document.getElementById('start-collection').disabled = false;
            document.getElementById('stop-collection').disabled = true;
            this._clearPolling();
        } catch (error) {
            console.error('Error stopping data collection:', error);
            this.showAlert(`Failed to stop data collection: ${error.message}`, 'danger');
        }
    }

    async prepareHistoricalData() {
        try {
            this.showAlert('Preparing historical data...', 'info');
            
            const response = await fetch('/api/prepare_historical_data', {
                method: 'POST'
            });
            
            const result = await response.json();
            
            if (result.success) {
                this.showAlert('Historical data prepared successfully', 'success');
                this.refreshDataStatus();
            } else {
                this.showAlert(`Failed to prepare historical data: ${result.error}`, 'danger');
            }
        } catch (error) {
            this.showAlert('Please select start and end dates', 'warning');
            return;
        }
        
        // Collect strategy parameters
        const params = {
            start_date: startDate,
            end_date: endDate,
            initial_capital: parseFloat(initialCapital),
            strategy_params: {},
            asset_symbol: document.getElementById('asset-symbol')?.value || 'CRUDEOIL',
            option_type: document.getElementById('option-type')?.value || 'CE',
            timeframe: document.getElementById('timeframe')?.value || '5m',
            strike_distance: parseInt(document.getElementById('strike-distance')?.value || '0')
        };
        
        // Get numeric input values and enabled states
        const inputParams = [
            { name: 'fast_ema_period', inputId: 'fast-ema-input', checkboxId: 'use-fast-ema' },
            { name: 'slow_ema_period', inputId: 'slow-ema-input', checkboxId: 'use-slow-ema' },
            { name: 'rsi_period', inputId: 'rsi-input', checkboxId: 'use-rsi' },
            { name: 'vwap_period', inputId: 'vwap-input', checkboxId: 'use-vwap' },
            { name: 'atr_period', inputId: 'atr-input', checkboxId: 'use-atr' }
        ];

        inputParams.forEach(p => {
            const inputEl = document.getElementById(p.inputId);
            const checkbox = document.getElementById(p.checkboxId);
            if (inputEl && checkbox) {
                params.strategy_params[p.name] = parseInt(inputEl.value);
                params.strategy_params[`use_${p.name.replace('_period','')}`] = checkbox.checked;
            }
        });
        
        try {
            this.showLoadingOverlay();
            
            const response = await fetch('/run_backtest', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(params)
            });
            
            const result = await response.json();
            
            this.hideLoadingOverlay();
            
            if (response.ok && result.combined) {
                this.displayBacktestResults(result);
                // Switch to results tab
                document.getElementById('results-tab').click();
                // ensure overlay hidden after tab becomes visible
                setTimeout(() => this.hideLoadingOverlay(), 200);
            } else {
                this.showAlert(`Backtest failed: ${result.error || 'Unknown error'}`, 'danger');
            }
        } catch (error) {
            console.error('Error running backtest:', error);
            this.hideLoadingOverlay();
            this.showAlert('Error running backtest', 'danger');
        }
    }

    displayBacktestResults(result) {
        // Ensure loading overlay is hidden
        this.hideLoadingOverlay();
        // Show summary statistics
        document.getElementById('summary-stats').style.display = 'flex';
        document.getElementById('charts-section').style.display = 'block';
        document.getElementById('detailed-metrics').style.display = 'flex';
        
        // Update summary cards with data from result.combined
        document.getElementById('total-return').textContent = `${result.combined.total_return.toFixed(2)}%`;
        document.getElementById('max-drawdown').textContent = `${result.combined.max_drawdown.toFixed(2)}%`;
        document.getElementById('sharpe-ratio').textContent = result.combined.sharpe_ratio.toFixed(2);
        document.getElementById('win-rate').textContent = `${result.combined.win_rate.toFixed(1)}%`;
        
        // Calculate detailed metrics from trades
        const allTrades = [...(result.ce?.trades || []), ...(result.pe?.trades || [])];
        const winningTrades = allTrades.filter(t => t.pnl > 0);
        const losingTrades = allTrades.filter(t => t.pnl < 0);
        
        // Update detailed metrics
        document.getElementById('total-trades').textContent = allTrades.length;
        document.getElementById('winning-trades').textContent = winningTrades.length;
        document.getElementById('losing-trades').textContent = losingTrades.length;
        
        if (winningTrades.length > 0) {
            const avgWin = winningTrades.reduce((sum, t) => sum + t.pnl, 0) / winningTrades.length;
            document.getElementById('avg-win').textContent = `₹${avgWin.toFixed(2)}`;
            
            const largestWin = Math.max(...winningTrades.map(t => t.pnl));
            document.getElementById('largest-win').textContent = `₹${largestWin.toFixed(2)}`;
        } else {
            document.getElementById('avg-win').textContent = '--';
            document.getElementById('largest-win').textContent = '--';
        }
        
        if (losingTrades.length > 0) {
            const avgLoss = losingTrades.reduce((sum, t) => sum + t.pnl, 0) / losingTrades.length;
            document.getElementById('avg-loss').textContent = `₹${avgLoss.toFixed(2)}`;
            
            const largestLoss = Math.min(...losingTrades.map(t => t.pnl));
            document.getElementById('largest-loss').textContent = `₹${largestLoss.toFixed(2)}`;
        } else {
            document.getElementById('avg-loss').textContent = '--';
            document.getElementById('largest-loss').textContent = '--';
        }
        
        // Advanced metrics direct from backend
        const combined = result.combined;
        const nf = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2 });
        const pctFormat = val => `${val.toFixed(2)}%`;

        document.getElementById('profit-factor').textContent = nf.format(combined.profit_factor);
        document.getElementById('sortino-ratio').textContent = nf.format(combined.sortino_ratio);
        document.getElementById('calmar-ratio').textContent = nf.format(combined.calmar_ratio);
        document.getElementById('volatility').textContent = pctFormat(combined.volatility * 100);
        document.getElementById('var-95').textContent = pctFormat(combined.var_95 * 100);
        document.getElementById('max-consecutive-losses').textContent = combined.max_consecutive_losses;
        document.getElementById('recovery-factor').textContent = nf.format(combined.recovery_factor);
        // New P&L metrics
        const safeFmt = v => (Number.isFinite(v) ? `₹${nf.format(v)}` : '--');
        document.getElementById('gross-profit').textContent = safeFmt(combined.gross_profit);
        document.getElementById('gross-loss').textContent = safeFmt(combined.gross_loss);
        document.getElementById('total-costs').textContent = safeFmt(combined.total_costs);
        document.getElementById('net-profit').textContent = safeFmt(combined.net_profit);
        
        // Draw equity curve
        this.drawEquityCurve(result);
    }

    drawEquityCurve(result) {
        const trace = {
            x: result.dates,
            y: result.combined.equity_curve,
            type: 'scatter',
            mode: 'lines',
            name: 'Combined Equity Curve',
            line: {
                color: '#007bff',
                width: 2
            }
        };
        
        const layout = {
            title: 'Portfolio Equity Curve',
            xaxis: {
                title: 'Date',
                type: 'date'
            },
            yaxis: {
                title: 'Portfolio Value (₹)'
            },
            plot_bgcolor: 'rgba(0,0,0,0)',
            paper_bgcolor: 'rgba(0,0,0,0)',
        };
        
        Plotly.newPlot('equity-curve-chart', [trace], layout, {responsive: true});
    }

    showLoadingOverlay() {
        const overlay = document.getElementById('loading-overlay');
        if (overlay) overlay.style.setProperty('display', 'flex', 'important');
    }

    hideLoadingOverlay() {
        const overlay = document.getElementById('loading-overlay');
        if (overlay) overlay.style.setProperty('display', 'none', 'important');
    }

    showProgress() {
        document.getElementById('progress-card').style.display = 'block';
        document.getElementById('collection-progress').style.width = '0%';
        document.getElementById('collection-progress').textContent = '0%';
        document.getElementById('collection-log').innerHTML = '';
    }

    hideProgress() {
        document.getElementById('progress-card').style.display = 'none';
        this._clearPolling();
    }

    showAlert(message, type = 'info') {
        const alertsContainer = document.getElementById('alerts-container');
        const alertId = 'alert-' + Date.now();
        
        const alertHtml = `
            <div class="alert alert-${type} alert-dismissible fade show" role="alert" id="${alertId}">
                ${message}
                <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
            </div>
        `;
        
        alertsContainer.insertAdjacentHTML('afterbegin', alertHtml);
        
        // Auto-dismiss after 5 seconds
        setTimeout(() => {
            const alertElement = document.getElementById(alertId);
            if (alertElement) {
                alertElement.remove();
            }
        }, 5000);
    }

    /* -------------------- Parameter Set Helpers -------------------- */
    getCurrentParameterSet() {
        const paramSet = {
            asset_symbol: document.getElementById('asset-symbol')?.value,
            option_type: document.getElementById('option-type')?.value,
            timeframe: document.getElementById('timeframe')?.value,
            strike_distance: parseInt(document.getElementById('strike-distance')?.value || '0'),
            strategy_params: {}
        };
        const inputs = [
            { name: 'fast_ema_period', inputId: 'fast-ema-input', checkboxId: 'use-fast-ema' },
            { name: 'slow_ema_period', inputId: 'slow-ema-input', checkboxId: 'use-slow-ema' },
            { name: 'rsi_period', inputId: 'rsi-input', checkboxId: 'use-rsi' },
            { name: 'vwap_period', inputId: 'vwap-input', checkboxId: 'use-vwap' },
            { name: 'atr_period', inputId: 'atr-input', checkboxId: 'use-atr' }
        ];
        inputs.forEach(p => {
            paramSet.strategy_params[p.name] = {
                value: parseInt(document.getElementById(p.inputId).value),
                enabled: document.getElementById(p.checkboxId).checked
            };
        });
        return paramSet;
    }

    applyParameterSet(ps) {
        try {
            document.getElementById('asset-symbol').value = ps.asset_symbol || 'CRUDEOIL';
            document.getElementById('option-type').value = ps.option_type || 'CE';
            document.getElementById('timeframe').value = ps.timeframe || '5m';
            document.getElementById('strike-distance').value = ps.strike_distance ?? 0;
            const mapping = {
                fast_ema_period: { inputId: 'fast-ema-input', checkboxId: 'use-fast-ema' },
                slow_ema_period: { inputId: 'slow-ema-input', checkboxId: 'use-slow-ema' },
                rsi_period: { inputId: 'rsi-input', checkboxId: 'use-rsi' },
                vwap_period: { inputId: 'vwap-input', checkboxId: 'use-vwap' },
                atr_period: { inputId: 'atr-input', checkboxId: 'use-atr' }
            };
            for (const [key, cfg] of Object.entries(mapping)) {
                if (ps.strategy_params?.[key]) {
                    document.getElementById(cfg.inputId).value = ps.strategy_params[key].value;
                    document.getElementById(cfg.checkboxId).checked = ps.strategy_params[key].enabled;
                }
            }
            this.showAlert('Parameters loaded', 'success');
        } catch (err) {
            console.error(err);
            this.showAlert('Failed to apply parameters', 'danger');
        }
    }

    saveParameterSet() {
        const paramSet = this.getCurrentParameterSet();
        const blob = new Blob([JSON.stringify(paramSet, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `param_set_${new Date().toISOString().slice(0,19).replace(/[:T]/g,'-')}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        this.showAlert('Parameters saved', 'success');
    }

    loadParameterSet(e) {
        const file = e.target.files[0];
        if (!file) return;
        const reader = new FileReader();
        reader.onload = (evt) => {
            try {
                const ps = JSON.parse(evt.target.result);
                this.applyParameterSet(ps);
            } catch (err) {
                console.error('Error parsing parameter file', err);
                this.showAlert('Invalid parameter file', 'danger');
            }
        };
        reader.readAsText(file);
    }

    /* --------------------------------------------------------------- */

    refreshResults() {
        // Refresh results if needed
        console.log('Results tab activated');
    }
}

// Initialize when DOM is loaded
document.addEventListener('DOMContentLoaded', function() {
    window.backtestManager = new BacktestManager();
});