import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import logging
import sys
from pathlib import Path

# Add parent directory to Python path to import from root
sys.path.append(str(Path(__file__).parent.parent))
from strategy import HighWinRateStrategy

import matplotlib.pyplot as plt
import seaborn as sns
import json

COST_PER_TRADE = 75  # INR per round-trip (buy+sell) for 1 lot CrudeOil options
LOT_SIZE = 100     # CrudeOil option contract size (barrels) per lot

class StrategyBacktester:
    def __init__(self, strategy_params=None):
        """Initialize backtester with strategy parameters"""
        self.strategy_params = strategy_params or {
            'fast_ema_period': 5,  # Reduced from 9 for quicker signals
            'slow_ema_period': 13,  # Reduced from 21 for quicker signals
            'rsi_period': 8,  # Reduced from 14 for faster momentum signals
            'atr_period': 10,  # Reduced from 14
            'vwap_period': 15,  # Reduced from 20
            'use_fast_ema': True,
            'use_slow_ema': True,
            'use_rsi': True,
            'use_atr': True,
            'use_vwap': True,
            'rsi_oversold': 35,  # New parameter
            'rsi_overbought': 65,  # New parameter
            'volume_surge_factor': 1.1,  # Reduced from 1.2 for more trades
            'atr_volatility_factor': 0.01  # Reduced from 0.015 for more trades
        }
        
        self.logger = self._setup_logging()
        # per-trade transaction cost
        self.cost_per_trade = COST_PER_TRADE
        # --- Risk-management controls ---
        self.daily_loss_cap_pct = -0.03   # halt entries when equity drops 3 % (per day)
        # MCX CrudeOil trading window (non-agri): 09:00–23:30 IST (23:55 Nov-Mar)
        self.session_start = datetime.strptime('09:00', '%H:%M').time()
        self.session_end_regular = datetime.strptime('23:30', '%H:%M').time()
        self.session_end_dst = datetime.strptime('23:55', '%H:%M').time()
        self.trail_start_atr = 1.0    # start trailing after +1 ATR move
        self.trail_distance_atr = 0.5 # trail at 0.5 ATR behind
        self.take_profit_atr = 2.0    # hard TP at +2 ATR
        self.strategy_ce = HighWinRateStrategy(contract_hub=None)
        self.strategy_pe = HighWinRateStrategy(contract_hub=None)
        self._apply_strategy_params()
        
    def _setup_logging(self):
        """Setup logging configuration"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        return logging.getLogger()

    def _apply_strategy_params(self):
        """Apply strategy parameters to both CE and PE strategies"""
        for strategy in [self.strategy_ce, self.strategy_pe]:
            for param, value in self.strategy_params.items():
                setattr(strategy, param, value)

    def prepare_data(self, data):
        """
        Prepare data for backtesting
        
        Args:
            data (pd.DataFrame): Raw OHLCV data with columns [open, high, low, close, volume]
        """
        df = data.copy()
        # ensure numeric columns are float to avoid Decimal comparison issues
        num_cols = ['open', 'high', 'low', 'close', 'volume']
        df[num_cols] = df[num_cols].astype('float64')
        # ensure timestamp column present and set as datetime index for date-based logic
        if 'timestamp' in df.columns:
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df.set_index('timestamp', inplace=True)
        # Drop any duplicate timestamps to avoid duplicate index errors in backtest
        df = df[~df.index.duplicated(keep='first')]

        
        # Calculate technical indicators
        df['fast_ema'] = df['close'].ewm(span=self.strategy_params['fast_ema_period'], adjust=False).mean()
        df['slow_ema'] = df['close'].ewm(span=self.strategy_params['slow_ema_period'], adjust=False).mean()
        
        # RSI
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=self.strategy_params['rsi_period']).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=self.strategy_params['rsi_period']).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))
        
        # VWAP
        typical_price = (df['high'] + df['low'] + df['close']) / 3
        df['vwap'] = (typical_price * df['volume']).rolling(window=self.strategy_params['vwap_period']).sum() / \
                     df['volume'].rolling(window=self.strategy_params['vwap_period']).sum()
        
        # ATR
        high_low = df['high'] - df['low']
        high_close = (df['high'] - df['close'].shift()).abs()
        low_close = (df['low'] - df['close'].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df['atr'] = tr.rolling(window=self.strategy_params['atr_period']).mean()
        
        return df

    def backtest(self, ce_data, pe_data, initial_capital=100000):
        """
        Run backtest on CE and PE data
        
        Args:
            ce_data (pd.DataFrame): CE option data
            pe_data (pd.DataFrame): PE option data
            initial_capital (float): Initial capital for the backtest
        """
        results = {
            'ce': self._backtest_single_risk(ce_data, 'CE', initial_capital/2),
            'pe': self._backtest_single_risk(pe_data, 'PE', initial_capital/2)
        }
        
        # Combine CE and PE results
        combined_equity = pd.DataFrame({
            'CE': results['ce']['equity_curve'],
            'PE': results['pe']['equity_curve']
        })
        
        # Use ffill() instead of fillna(method='ffill')
        combined_equity = combined_equity.ffill()
        combined_equity['Total'] = combined_equity.sum(axis=1)
        
        results['combined'] = {
            'equity_curve': combined_equity['Total'],
            'total_return': (combined_equity['Total'].iloc[-1] - initial_capital) / initial_capital * 100,
            'max_drawdown': self._calculate_max_drawdown(combined_equity['Total']),
            'sharpe_ratio': self._calculate_sharpe_ratio(combined_equity['Total']),
            'win_rate': (results['ce']['win_rate'] + results['pe']['win_rate']) / 2,
            'profit_factor': self._calculate_profit_factor(results['ce']['trades'] + results['pe']['trades']),
            'gross_profit': float(np.nansum([results['ce']['gross_profit'], results['pe']['gross_profit']])),
            'gross_loss': float(np.nansum([results['ce']['gross_loss'], results['pe']['gross_loss']])),
            'total_costs': results['ce']['total_costs'] + results['pe']['total_costs'],
            'net_profit': float(np.nansum([results['ce']['net_profit'], results['pe']['net_profit']])),
            'sortino_ratio': self._calculate_sortino_ratio(combined_equity['Total']),
            'calmar_ratio': self._calculate_calmar_ratio(combined_equity['Total']),
            'volatility': self._calculate_volatility(combined_equity['Total']),
            'trades': results['ce']['trades'] + results['pe']['trades'],
            'var_95': self._calculate_var(combined_equity['Total']),
            'max_consecutive_losses': self._calculate_max_consecutive_losses(results['ce']['trades'] + results['pe']['trades']),
            'recovery_factor': self._calculate_recovery_factor((combined_equity['Total'].iloc[-1] - initial_capital)/initial_capital*100, results['combined']['max_drawdown'] if 'combined' in results else self._calculate_max_drawdown(combined_equity['Total']))
        }
        
        return results

    def _is_trading_time(self, ts):
        """Return True if timestamp falls within MCX CrudeOil trading session (Mon-Fri)."""
        if ts.weekday() >= 5:  # Saturday/Sunday
            return False
        end_time = self.session_end_dst if ts.month in [11, 12, 1, 2, 3] else self.session_end_regular
        return self.session_start <= ts.time() <= end_time

    def _backtest_single_risk(self, data, option_type, initial_capital):
        """Run backtest on single option type with ATR-based sizing & stop-loss"""
        strat = self.strategy_ce if option_type == 'CE' else self.strategy_pe
        df = self.prepare_data(data)
        position_qty = 0  # number of contracts
        entry_price = 0.0
        entry_time = None
        stop_loss = None
        equity = initial_capital
        equity_curve = pd.Series(index=df.index, dtype=float)
        equity_curve.iloc[0] = equity
        trades = []
        wins = losses = 0

        # Daily loss cap helpers
        current_day = df.index[0].date()
        day_start_equity = equity
        day_halted = False

        for i in range(1, len(df)):
            bar = df.iloc[i]
            ts = df.index[i]
            # reset daily trackers if new day
            if ts.date() != current_day:
                current_day = ts.date()
                day_start_equity = equity
                day_halted = False
            sub_df = df.iloc[: i + 1]
            signal = self._generate_signal(sub_df, option_type)
            atr = bar['atr'] if not np.isnan(bar['atr']) else None

            # Exit logic first (includes trailing stop / TP)
            # update trailing stop if in profit
            if position_qty != 0 and atr and atr > 0 and stop_loss is not None:
                favourable_move = (bar['close'] - entry_price) if option_type == 'CE' else (entry_price - bar['close'])
                if favourable_move >= self.trail_start_atr * atr:
                    # new trail level
                    new_sl = entry_price + (favourable_move - self.trail_distance_atr * atr) if option_type == 'CE' else entry_price - (favourable_move - self.trail_distance_atr * atr)
                    # tighten stop in favourable direction only
                    if (option_type == 'CE' and new_sl > stop_loss) or (option_type == 'PE' and new_sl < stop_loss):
                        stop_loss = new_sl
            exit_price = None
            exit_reason = None
            if position_qty != 0:
                # stop hit?
                if stop_loss is not None and ((option_type == 'CE' and bar['low'] <= stop_loss) or (option_type == 'PE' and bar['high'] >= stop_loss)):
                    exit_price = stop_loss
                    exit_reason = 'SL'
                # opposite signal
                elif signal == -1 or (self.take_profit_atr and favourable_move >= self.take_profit_atr * atr):
                    exit_price = bar['close']
                    exit_reason = 'Signal'

                if exit_price is not None:
                    pnl_per_contract = (exit_price - entry_price) if option_type == 'CE' else (entry_price - exit_price)
                    gross_pnl = pnl_per_contract * position_qty * LOT_SIZE
                    net_pnl = gross_pnl - self.cost_per_trade
                    equity += net_pnl
                    trade_return_pct = (net_pnl / (entry_price * position_qty * LOT_SIZE)) * 100 if entry_price > 0 else 0
                    trades.append({
                        'entry_time': entry_time,
                        'exit_time': ts,
                        'entry_price': entry_price,
                        'exit_price': exit_price,
                        'qty': position_qty,
                        'pnl': gross_pnl,
                        'net_pnl': net_pnl,
                        'return': trade_return_pct,
                        'reason': exit_reason,
                        'type': 'LONG'
                    })
                    wins += 1 if net_pnl > 0 else 0
                    losses += 1 if net_pnl <= 0 else 0
                    position_qty = 0
                    entry_price = 0.0
                    entry_time = None
                    stop_loss = None

            # Check daily loss cap
            if not day_halted and (equity - day_start_equity)/day_start_equity <= self.daily_loss_cap_pct:
                day_halted = True  # stop new entries for the rest of the day

            # Entry logic – only during MCX session and if not halted
            if position_qty == 0 and not day_halted and self._is_trading_time(ts) and signal == 1 and atr and atr > 0:
                contracts = strat.calculate_position_size(atr)
                # Capital check – ensure we can afford the position
                max_affordable = int(equity // (bar['close'] * LOT_SIZE))
                if max_affordable <= 0:
                    contracts = 0  # cannot afford even one lot
                elif contracts > max_affordable:
                    contracts = max_affordable
                if contracts > 0:
                    position_qty = contracts
                    entry_price = bar['close']
                    entry_time = ts
                    stop_loss = strat.calculate_stop_loss(entry_price, 'BUY')
                    equity -= self.cost_per_trade  # commission on entry treated immediately

            equity_curve.iloc[i] = equity

        equity_curve.bfill(inplace=True)
        win_rate = wins / (wins + losses) if (wins + losses) > 0 else 0

        return {
            'equity_curve': equity_curve,
            'trades': trades,
            'gross_profit': float(sum(t['pnl'] for t in trades if t['pnl'] > 0)),
            'gross_loss': float(abs(sum(t['pnl'] for t in trades if t['pnl'] < 0))),
            'total_costs': len(trades) * self.cost_per_trade,
            'net_profit': float(sum(t['net_pnl'] for t in trades)),
            'total_return': (equity - initial_capital) / initial_capital * 100,
            'win_rate': win_rate * 100,
            'max_drawdown': self._calculate_max_drawdown(equity_curve),
            'sharpe_ratio': self._calculate_sharpe_ratio(equity_curve)
        }
        """Run backtest on single option type"""
        df = self.prepare_data(data)
        position = 0
        equity = initial_capital
        equity_curve = pd.Series(index=df.index, dtype=float)
        equity_curve.iloc[0] = equity
        trades = []
        wins = 0
        losses = 0
        
        for i in range(1, len(df)):
            signal = self._generate_signal(df.iloc[:i], option_type)
            
            if position == 0 and signal == 1:  # Enter long
                entry_price = df.iloc[i]['close']
                position = 1
                trade = {
                    'entry_time': df.index[i],
                    'entry_price': entry_price,
                    'type': 'LONG'
                }
            
            elif position == 1:  # Check exit
                current_price = df.iloc[i]['close']
                price_diff = current_price - trade['entry_price']
                pnl = price_diff * LOT_SIZE
                
                # Exit conditions
                if (signal == -1 or 
                    price_diff < -trade['entry_price'] * 0.05 or  # 5% stop loss evaluated per contract
                    price_diff > trade['entry_price'] * 0.10):    # 10% take profit
                    
                    trade.update({
                        'exit_time': df.index[i],
                        'exit_price': current_price,
                        'pnl': pnl,
                        'cost': self.cost_per_trade,
                        'net_pnl': pnl - self.cost_per_trade,
                        'return': pnl / trade['entry_price'] * 100
                    })
                    trades.append(trade)
                    
                    equity += (pnl - self.cost_per_trade)
                    equity_curve.iloc[i] = equity
                    
                    if pnl > 0:
                        wins += 1
                    else:
                        losses += 1
                    
                    position = 0
            
            if position == 0:
                equity_curve.iloc[i] = equity
        
        # Forward fill any missing values using ffill()
        equity_curve = equity_curve.ffill()
        
        total_trades = wins + losses
        win_rate = wins / total_trades if total_trades > 0 else 0
        
        return {
            'equity_curve': equity_curve,
            'trades': trades,
            'gross_profit': float(np.nansum([t['pnl'] for t in trades if t['pnl'] > 0])),
            'gross_loss': float(np.nansum([t['pnl'] for t in trades if t['pnl'] < 0])),
            'total_costs': len(trades)*self.cost_per_trade,
            'net_profit': float(np.nansum([t['net_pnl'] for t in trades])),
            'total_return': (equity - initial_capital) / initial_capital * 100,
            'win_rate': win_rate * 100,
            'max_drawdown': self._calculate_max_drawdown(equity_curve),
            'sharpe_ratio': self._calculate_sharpe_ratio(equity_curve)
        }

    def _generate_signal(self, data, option_type):
        """Generate trading signal based on optimized strategy rules"""
        if len(data) < max(self.strategy_params['vwap_period'],
                          self.strategy_params['atr_period']):
            return 0
            
        df = data.iloc[-1]
        prev_df = data.iloc[-2] if len(data) > 1 else None
        
        if prev_df is None:
            return 0
        
        # VWAP crossover condition
        vwap_cross_up = df['close'] > df['vwap'] and prev_df['close'] < prev_df['vwap']
        vwap_cross_down = df['close'] < df['vwap'] and prev_df['close'] > prev_df['vwap']
        
        # EMA conditions
        ema_bullish = df['fast_ema'] > df['slow_ema']
        ema_bearish = df['fast_ema'] < df['slow_ema']
        
        # RSI conditions
        rsi_oversold = df['rsi'] < self.strategy_params['rsi_oversold']
        rsi_overbought = df['rsi'] > self.strategy_params['rsi_overbought']
        
        # ATR volatility filter
        volatility_condition = df['atr'] > df['close'] * self.strategy_params['atr_volatility_factor']
        
        # Volume surge condition
        volume_condition = df['volume'] > data['volume'].rolling(window=20).mean().iloc[-1] * self.strategy_params['volume_surge_factor']
        
        # Trend strength
        trend_strength = abs(df['fast_ema'] - df['slow_ema']) / df['slow_ema']
        strong_trend = trend_strength > 0.002  # 0.2% difference
        
        # Generate signals based on option type
        if option_type == 'CE':
            # Buy CE when bullish
            if ((vwap_cross_up and ema_bullish) or (rsi_oversold and ema_bullish)) and volume_condition and volatility_condition:
                return 1
            # Exit CE when bearish
            elif ((vwap_cross_down and ema_bearish) or rsi_overbought) and strong_trend:
                return -1
        else:  # PE
            # Buy PE when bearish
            if ((vwap_cross_down and ema_bearish) or (rsi_overbought and ema_bearish)) and volume_condition and volatility_condition:
                return 1
            # Exit PE when bullish
            elif ((vwap_cross_up and ema_bullish) or rsi_oversold) and strong_trend:
                return -1
        
        return 0

    def _calculate_max_drawdown(self, equity_curve):
        """Calculate maximum drawdown"""
        rolling_max = equity_curve.expanding().max()
        drawdowns = (equity_curve - rolling_max) / rolling_max * 100
        return drawdowns.min()

    def _calculate_sortino_ratio(self, equity_curve, risk_free_rate=0.02):
        """Calculate Sortino ratio of the equity curve"""
        returns = equity_curve.pct_change().dropna()
        excess_returns = returns - (risk_free_rate/252)
        downside_returns = excess_returns[excess_returns < 0]
        downside_std = downside_returns.std()
        if downside_std == 0 or np.isnan(downside_std):
            return 0.0
        avg_excess_return = excess_returns.mean()*252  # annualise
        return avg_excess_return / (downside_std * np.sqrt(252))

    def _calculate_volatility(self, equity_curve):
        returns = equity_curve.pct_change().dropna()
        return returns.std() * np.sqrt(252)

    def _calculate_calmar_ratio(self, equity_curve):
        cagr = (equity_curve.iloc[-1]/equity_curve.iloc[0])**(252/len(equity_curve)) - 1
        max_dd = self._calculate_max_drawdown(equity_curve)/100  # convert to fraction
        return cagr/abs(max_dd) if max_dd != 0 else np.nan

    def _calculate_var(self, equity_curve, confidence=0.95):
        returns = equity_curve.pct_change().dropna()
        var = np.percentile(returns, (1-confidence)*100)
        return var

    def _calculate_max_consecutive_losses(self, trades):
        max_cons = cons = 0
        for t in trades:
            if t.get('pnl',0) < 0:
                cons += 1
                max_cons = max(max_cons, cons)
            else:
                cons = 0
        return max_cons

    def _calculate_profit_factor(self, trades):
        gross_profit = sum(t['pnl'] for t in trades if t.get('pnl',0) > 0)
        gross_loss = abs(sum(t['pnl'] for t in trades if t.get('pnl',0) < 0))
        return gross_profit / gross_loss if gross_loss != 0 else np.inf

    def _calculate_recovery_factor(self, total_return_pct, max_drawdown_pct):
        dd = abs(max_drawdown_pct)
        return total_return_pct / dd if dd != 0 else np.nan

    def _calculate_sharpe_ratio(self, equity_curve, risk_free_rate=0.02):
        """Calculate Sharpe ratio"""
        returns = equity_curve.pct_change().dropna()
        excess_returns = returns - risk_free_rate/252  # Daily risk-free rate
        if len(excess_returns) < 2:
            return 0
        return np.sqrt(252) * excess_returns.mean() / excess_returns.std()

    def plot_results(self, results, save_path=None):
        """Plot backtest results"""
        plt.style.use('dark_background')
        fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(15, 12))
        
        # Plot equity curves
        ax1.plot(results['combined']['equity_curve'], label='Combined', color='white', linewidth=2)
        ax1.plot(results['ce']['equity_curve'], label='CE', color='#26a69a', alpha=0.7)
        ax1.plot(results['pe']['equity_curve'], label='PE', color='#ef5350', alpha=0.7)
        ax1.set_title('Equity Curves')
        ax1.legend()
        ax1.grid(True, alpha=0.2)
        
        # Plot drawdown
        combined_equity = results['combined']['equity_curve']
        rolling_max = combined_equity.expanding().max()
        drawdown = (combined_equity - rolling_max) / rolling_max * 100
        ax2.fill_between(drawdown.index, drawdown, 0, color='red', alpha=0.3)
        ax2.set_title('Drawdown (%)')
        ax2.grid(True, alpha=0.2)
        
        # Plot trade distribution
        all_trades = results['ce']['trades'] + results['pe']['trades']
        returns = [trade['return'] for trade in all_trades]
        sns.histplot(returns, bins=50, ax=ax3, color='cyan', alpha=0.6)
        ax3.set_title('Trade Return Distribution (%)')
        ax3.grid(True, alpha=0.2)
        
        plt.tight_layout()
        
        if save_path:
            plt.savefig(save_path)
            plt.close()
        else:
            plt.show()

    def save_results(self, results, save_dir='backtest_results'):
        """Save backtest results"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        save_dir = Path(save_dir)
        save_dir.mkdir(exist_ok=True)
        
        # Save strategy parameters
        with open(save_dir / f'strategy_params_{timestamp}.json', 'w') as f:
            json.dump(self.strategy_params, f, indent=4)
        
        # Save performance metrics
        metrics = {
            'total_return': results['combined']['total_return'],
            'max_drawdown': results['combined']['max_drawdown'],
            'sharpe_ratio': results['combined']['sharpe_ratio'],
            'win_rate': results['combined']['win_rate'],
            'ce_metrics': {
                'total_return': results['ce']['total_return'],
                'win_rate': results['ce']['win_rate']
            },
            'pe_metrics': {
                'total_return': results['pe']['total_return'],
                'win_rate': results['pe']['win_rate']
            }
        }
        
        with open(save_dir / f'metrics_{timestamp}.json', 'w') as f:
            json.dump(metrics, f, indent=4)
        
        # Save equity curves
        equity_data = pd.DataFrame({
            'Combined': results['combined']['equity_curve'],
            'CE': results['ce']['equity_curve'],
            'PE': results['pe']['equity_curve']
        })
        equity_data.to_csv(save_dir / f'equity_curves_{timestamp}.csv')
        
        # Save trades
        trades_ce = pd.DataFrame(results['ce']['trades'])
        trades_pe = pd.DataFrame(results['pe']['trades'])
        trades_ce.to_csv(save_dir / f'trades_ce_{timestamp}.csv', index=False)
        trades_pe.to_csv(save_dir / f'trades_pe_{timestamp}.csv', index=False)
        
        # Save plots
        self.plot_results(results, save_path=save_dir / f'backtest_plots_{timestamp}.png')
        
        self.logger.info(f"Backtest results saved to {save_dir}")

def optimize_strategy_parameters():
    """Run multiple backtests with different parameter combinations"""
    # Create backtest results directory
    Path('backtest_results/optimization').mkdir(exist_ok=True, parents=True)
    
    # Load and prepare data
    ce_data = pd.read_csv('backtest/historical_data_ce.csv')
    pe_data = pd.read_csv('backtest/historical_data_pe.csv')

    
    # Convert timestamp to datetime
    ce_data['timestamp'] = pd.to_datetime(ce_data['timestamp'])
    pe_data['timestamp'] = pd.to_datetime(pe_data['timestamp'])
    
    # Handle duplicate timestamps by keeping the latest value
    ce_data = ce_data.sort_values('timestamp').groupby('timestamp', as_index=False).last()
    pe_data = pe_data.sort_values('timestamp').groupby('timestamp', as_index=False).last()
    
    # Set timestamp as index
    ce_data.set_index('timestamp', inplace=True)
    pe_data.set_index('timestamp', inplace=True)
    
    # Parameter sets to test
    parameter_sets = [
        {
            # Original conservative set
            'fast_ema_period': 5,
            'slow_ema_period': 13,
            'rsi_period': 8,
            'atr_period': 10,
            'vwap_period': 15,
            'use_fast_ema': True,
            'use_slow_ema': True,
            'use_rsi': True,
            'use_atr': True,
            'use_vwap': True,
            'rsi_oversold': 35,
            'rsi_overbought': 65,
            'volume_surge_factor': 1.1,
            'atr_volatility_factor': 0.01
        },
        {
            # Aggressive intraday set
            'fast_ema_period': 3,
            'slow_ema_period': 8,
            'rsi_period': 6,
            'atr_period': 8,
            'vwap_period': 10,
            'use_fast_ema': True,
            'use_slow_ema': True,
            'use_rsi': True,
            'use_atr': True,
            'use_vwap': True,
            'rsi_oversold': 30,
            'rsi_overbought': 70,
            'volume_surge_factor': 1.05,
            'atr_volatility_factor': 0.008
        },
        {
            # Ultra-aggressive scalping set
            'fast_ema_period': 2,
            'slow_ema_period': 5,
            'rsi_period': 4,
            'atr_period': 5,
            'vwap_period': 8,
            'use_fast_ema': True,
            'use_slow_ema': True,
            'use_rsi': True,
            'use_atr': True,
            'use_vwap': True,
            'rsi_oversold': 25,
            'rsi_overbought': 75,
            'volume_surge_factor': 1.02,
            'atr_volatility_factor': 0.005
        },
        {
            # Trend-following set
            'fast_ema_period': 8,
            'slow_ema_period': 21,
            'rsi_period': 14,
            'atr_period': 14,
            'vwap_period': 20,
            'use_fast_ema': True,
            'use_slow_ema': True,
            'use_rsi': True,
            'use_atr': True,
            'use_vwap': True,
            'rsi_oversold': 40,
            'rsi_overbought': 60,
            'volume_surge_factor': 1.15,
            'atr_volatility_factor': 0.012
        }
    ]
    
    results = []
    for i, params in enumerate(parameter_sets, 1):
        print(f"\nTesting parameter set {i}:")
        print(json.dumps(params, indent=2))
        
        backtester = StrategyBacktester(params)
        backtest_results = backtester.backtest(ce_data, pe_data)
        
        results.append({
            'params': params,
            'total_return': backtest_results['combined']['total_return'],
            'max_drawdown': backtest_results['combined']['max_drawdown'],
            'sharpe_ratio': backtest_results['combined']['sharpe_ratio'],
            'win_rate': backtest_results['combined']['win_rate']
        })
        
        # Save results
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        save_dir = f'backtest_results/optimization/set_{i}_{timestamp}'
        backtester.save_results(backtest_results, save_dir)
        
    # Print summary
    print("\nOptimization Results:")
    for i, result in enumerate(results, 1):
        print(f"\nParameter Set {i}:")
        print(f"Total Return: {result['total_return']:.2f}%")
        print(f"Max Drawdown: {result['max_drawdown']:.2f}%")
        print(f"Sharpe Ratio: {result['sharpe_ratio']:.2f}")
        print(f"Win Rate: {result['win_rate']:.2f}%")

if __name__ == "__main__":
    optimize_strategy_parameters() 