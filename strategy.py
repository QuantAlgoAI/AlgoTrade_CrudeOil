import pandas as pd
import numpy as np
import logging
from datetime import datetime
import math
from scipy.stats import norm
import traceback
import pytz
import re

# Define IST timezone
IST = pytz.timezone('Asia/Kolkata')

class HighWinRateStrategy:
    """Advanced options trading strategy with Greek-based scoring and institutional flow detection."""
    
    def __init__(self, contract_hub, account_balance=100000):
        self.contract_hub = contract_hub
        self.account_balance = account_balance
        self.data = pd.DataFrame()
        
        # Configurable parameters
        self.fast_ema_period_base = 3
        self.slow_ema_period_base = 7
        self.rsi_period = 5
        self.volume_ma_period = 5
        self.oi_ma_period = 5
        self.atr_period = 5
        self.vwap_period = 5
        
        # Initialize actual EMA periods
        self.fast_ema_period = self.fast_ema_period_base
        self.slow_ema_period = self.slow_ema_period_base
        
        # Trade management
        self.trade_state = 'IDLE'
        self.current_trade = None
        self.entry_price: float | None = None
        self.last_trade_time = None
        # Convert trading times to IST
        self.trading_start_time = datetime.strptime('09:15', '%H:%M').replace(tzinfo=IST).time()
        self.trading_end_time = datetime.strptime('15:15', '%H:%M').replace(tzinfo=IST).time()
        self.max_trades_per_day = 6
        self.daily_loss_cap = 2000
        self.daily_pnl = 0
        self.trades_today = 0
        
        # Risk management
        self.trailing_sl_activated = False
        self.trailing_sl_percentage = 0.20
        self.trailing_sl_distance = 0.10
        self.partial_exit_percentage = 0.50
        self.partial_exit_target = 0.20
        
        # Greeks thresholds
        self.max_delta = 0.7
        self.min_delta = 0.15
        self.max_theta = -0.7
        self.max_iv = 0.7
        
        # Volume and OI thresholds
        self.min_volume_increase = 0.3
        self.min_oi_increase = 0.5
        
        # Market context
        self.market_regime = "UNKNOWN"
        self.volatility_threshold = 0.02
        self.support_levels = []
        self.resistance_levels = []
        
        # Position sizing
        self.base_position_size = 0.02  # 2% of account
        self.min_position_size = 1
        
        # Dynamic adjustments
        self.volatility_factor = 1.0

        # Indicator toggles
        self.use_ema = True
        self.use_rsi = True
        self.use_volume = True
        self.use_oi = True
        self.use_vwap = True
        self.use_atr = True

    def update_parameters(self, params):
        try:
            logging.info(f"Received parameters for update: {params}")

            self.fast_ema_period_base = int(params.get('fast_ema_period', self.fast_ema_period_base))
            self.slow_ema_period_base = int(params.get('slow_ema_period', self.slow_ema_period_base))
            self.rsi_period = int(params.get('rsi_period', self.rsi_period))
            self.vwap_period = int(params.get('vwap_period', self.vwap_period))
            self.atr_period = int(params.get('atr_period', self.atr_period))
            
            use_fast_ema = str(params.get('use_fast_ema', 'true')).lower() == 'true'
            use_slow_ema = str(params.get('use_slow_ema', 'true')).lower() == 'true'
            self.use_ema = use_fast_ema and use_slow_ema

            self.use_rsi = str(params.get('use_rsi', 'true')).lower() == 'true'
            self.use_vwap = str(params.get('use_vwap', 'true')).lower() == 'true'
            self.use_atr = str(params.get('use_atr', 'true')).lower() == 'true'

            self.use_volume = True
            self.use_oi = True

            logging.info(f"Strategy parameters updated")
        except Exception as e:
            logging.error(f"Error updating parameters: {e}")
            logging.error(traceback.format_exc())

    def calculate_vwap(self, data):
        typical_price = (data['high'] + data['low'] + data['close']) / 3
        volume_price = typical_price * data['volume']
        cumulative_volume = data['volume'].rolling(window=self.vwap_period).sum()
        cumulative_volume_price = volume_price.rolling(window=self.vwap_period).sum()
        return cumulative_volume_price / cumulative_volume

    def update_data(self, tick_data):
        try:
            utc_timestamp = datetime.fromtimestamp(int(tick_data['exchange_timestamp']) / 1000, tz=pytz.UTC)
            ist_timestamp = utc_timestamp.astimezone(IST)
            
            tick_df = pd.DataFrame([{
                'timestamp': ist_timestamp,
                'ltp': float(tick_data['last_traded_price']) / 100,
                'high': float(tick_data['high_price_of_the_day']) / 100,
                'low': float(tick_data['low_price_of_the_day']) / 100,
                'close': float(tick_data['last_traded_price']) / 100,
                'volume': tick_data['volume_trade_for_the_day'],
                'oi': tick_data['open_interest'],
                'oi_change': tick_data.get('open_interest_change_percentage', 0),
                'best_bid': float(tick_data['best_5_buy_data'][0]['price']) / 100 if tick_data.get('best_5_buy_data') else None,
                'best_ask': float(tick_data['best_5_sell_data'][0]['price']) / 100 if tick_data.get('best_5_sell_data') else None,
                'total_buy_qty': tick_data['total_buy_quantity'],
                'total_sell_qty': tick_data['total_sell_quantity']
            }])
            self.data = pd.concat([self.data, tick_df], ignore_index=True)
            
            if len(self.data) > 500:
                self.data = self.data.tail(500)
            
            if len(self.data) >= 3:
                if len(self.data) >= self.atr_period:
                    atr = self.calculate_atr(self.data, self.atr_period)
                    self.volatility_factor = atr.iloc[-1] / self.data['ltp'].iloc[-1] / self.volatility_threshold
                    self.volatility_factor = max(0.5, min(2.0, self.volatility_factor))
                else:
                    atr = pd.Series([0.1] * len(self.data))
                
                self.fast_ema_period = int(self.fast_ema_period_base * self.volatility_factor)
                self.slow_ema_period = int(self.slow_ema_period_base * self.volatility_factor)
                
                self.data['fast_ema'] = self.data['ltp'].ewm(span=self.fast_ema_period, adjust=False).mean()
                self.data['slow_ema'] = self.data['ltp'].ewm(span=self.slow_ema_period, adjust=False).mean()
                self.data['rsi'] = self.calculate_rsi(self.data, self.rsi_period)
                self.data['volume_ma'] = self.calculate_volume_ma(self.data, self.volume_ma_period)
                self.data['oi_ma'] = self.calculate_oi_ma(self.data, self.oi_ma_period)
                self.data['vwap'] = self.calculate_vwap(self.data)
                self.data['atr'] = atr
                
                self.market_regime = self.analyze_market_context()
                self.find_support_resistance(self.data)
                
        except Exception as e:
            logging.error(f"Error in update_data: {str(e)}")
            logging.error(traceback.format_exc())

    def calculate_rsi(self, data, period):
        delta = data['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))

    def calculate_volume_ma(self, data, period):
        return data['volume'].rolling(window=period).mean()

    def calculate_oi_ma(self, data, period):
        return data['oi'].rolling(window=period).mean()

    def calculate_atr(self, data, period):
        high_low = data['high'] - data['low']
        high_close = np.abs(data['high'] - data['close'].shift())
        low_close = np.abs(data['low'] - data['close'].shift())
        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        return true_range.rolling(window=period).mean()

    def analyze_market_context(self):
        try:
            if len(self.data) < self.slow_ema_period:
                return "UNKNOWN"
            volatility = self.calculate_atr(self.data, self.atr_period).iloc[-1] / self.data['ltp'].iloc[-1]
            sma = self.data['ltp'].rolling(window=self.slow_ema_period).mean()
            current_price = self.data['ltp'].iloc[-1]
            current_sma = sma.iloc[-1]
            if volatility > self.volatility_threshold:
                return "VOLATILE"
            elif current_price > current_sma * 1.02:
                return "UPTREND"
            elif current_price < current_sma * 0.98:
                return "DOWNTREND"
            else:
                return "RANGING"
        except Exception as e:
            return "UNKNOWN"

    def find_support_resistance(self, data):
        self.support_levels = [data['low'].rolling(window=20).min().iloc[-1]] if not data.empty else []
        self.resistance_levels = [data['high'].rolling(window=20).max().iloc[-1]] if not data.empty else []

    def is_valid_trading_time(self):
        current_time = datetime.now(IST).time()
        return self.trading_start_time <= current_time <= self.trading_end_time

    def check_exit_conditions(self):
        """Return 'EXIT' if SL/target or EOD square-off hit."""
        if self.trade_state != 'OPEN' or self.entry_price is None or self.data.empty:
            return None
        current_price = self.data['ltp'].iloc[-1]
        stop_pct = 0.01  # 1% SL
        target_pct = 0.015  # 1.5% target
        # End-of-day square-off at 15:25
        if datetime.now(IST).time() >= datetime.strptime('15:25', '%H:%M').time():
            return 'EXIT'
        if current_price <= self.entry_price * (1 - stop_pct):
            return 'EXIT'
        if current_price >= self.entry_price * (1 + target_pct):
            return 'EXIT'
        return None

    def generate_signals(self, tick_data=None, depth_signal=0):
        try:
            if len(self.data) < 3:
                return None
            
            symbol = tick_data.get('symbol', '') if tick_data else ''
            is_ce = symbol.endswith('CE')
            is_pe = symbol.endswith('PE')
            
            if not (is_ce or is_pe):
                return None
            
            # Get current values
            current_fast = self.data['fast_ema'].iloc[-1] if len(self.data) >= 2 else self.data['ltp'].iloc[-1]
            current_slow = self.data['slow_ema'].iloc[-1] if len(self.data) >= 3 else self.data['ltp'].iloc[-1]
            prev_fast = self.data['fast_ema'].iloc[-2] if len(self.data) >= 3 else current_fast
            prev_slow = self.data['slow_ema'].iloc[-2] if len(self.data) >= 3 else current_slow
            current_rsi = self.data['rsi'].iloc[-1] if len(self.data) >= self.rsi_period and not pd.isna(self.data['rsi'].iloc[-1]) else 50
            current_volume = self.data['volume'].iloc[-1]
            current_volume_ma = self.data['volume_ma'].iloc[-1] if len(self.data) >= self.volume_ma_period else current_volume
            current_oi = self.data['oi'].iloc[-1]
            current_oi_ma = self.data['oi_ma'].iloc[-1] if len(self.data) >= self.oi_ma_period else current_oi
            current_price = self.data['ltp'].iloc[-1]
            current_vwap = self.data['vwap'].iloc[-1] if len(self.data) >= self.vwap_period and not pd.isna(self.data['vwap'].iloc[-1]) else current_price
            
            # Parse option details
            strike_price, expiry_date = self._parse_symbol(symbol)
            time_to_expiry = (expiry_date - datetime.now(IST)).total_seconds() / (365 * 24 * 3600)
            
            # Check option value before proceeding
            value_score, _, _ = self.calculate_option_value_score(
                current_price=current_price,
                strike_price=strike_price,
                underlying_price=current_vwap,
                time_to_expiry=time_to_expiry,
                is_ce=is_ce
            )
            if value_score < 0.4:
                logging.info(f"🚫 Low-value option skipped (Score: {value_score:.2f})")
                return None
            
            # Calculate IV and Greeks
            iv = self.calculate_implied_volatility(current_price, current_vwap, strike_price, time_to_expiry, 0.05, 'call' if is_ce else 'put')
            greeks = self.calculate_option_greeks(current_vwap, strike_price, time_to_expiry, 0.05, iv, 'call' if is_ce else 'put') if iv else None
            
            # Calculate advanced signal score
            normalized_score = self._calculate_advanced_signal_score(
                current_fast, current_slow, prev_fast, prev_slow,
                current_rsi, current_volume, current_volume_ma,
                current_oi, current_oi_ma, current_price, current_vwap,
                greeks, iv, depth_signal, is_ce, symbol
            )
            
            # Dynamic threshold
            threshold = self._get_dynamic_threshold(iv, greeks, current_rsi)
            
            if normalized_score > threshold:
                logging.info(f"🚀 BUY Signal! Score: {normalized_score:.2f} > {threshold:.2f}")
                return 'BUY'
            return None
            
        except Exception as e:
            logging.error(f"Signal generation error: {str(e)}")
            return None

    def _parse_symbol(self, symbol):
        try:
            match = re.match(r'CRUDEOIL(\d{2})([A-Z]{3})(\d{2})(\d{5})(CE|PE)', symbol)
            if match:
                day = int(match.group(1))
                month_str = match.group(2)
                year_short = int(match.group(3))
                strike_price = int(match.group(4))
                year = 2000 + year_short
                month_map = {'JAN': 1, 'FEB': 2, 'MAR': 3, 'APR': 4, 'MAY': 5, 'JUN': 6, 
                             'JUL': 7, 'AUG': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DEC': 12}
                month = month_map[month_str]
                expiry_date = datetime(year, month, day).replace(tzinfo=IST)
                return strike_price, expiry_date
            return None, None
        except Exception as e:
            return None, None

    def _calculate_advanced_signal_score(self, current_fast, current_slow, prev_fast, prev_slow, current_rsi, 
                                        current_volume, current_volume_ma, current_oi, current_oi_ma, 
                                        current_price, current_vwap, greeks, iv, depth_signal, is_ce, symbol):
        weights = {
            'price_momentum': 0.25,
            'greeks_edge': 0.30,
            'volatility_edge': 0.20,
            'volume_surge': 0.15,
            'oi_momentum': 0.10
        }
        
        score = 0
        details = []
        
        # 1. Price Momentum
        if self.use_ema:
            if is_ce and current_fast > current_slow and prev_fast <= prev_slow:
                score += weights['price_momentum'] * 1.0
                details.append("CE EMA Cross")
            elif not is_ce and current_fast < current_slow and prev_fast >= prev_slow:
                score += weights['price_momentum'] * 1.0
                details.append("PE EMA Cross")
            elif is_ce and current_fast > current_slow:
                score += weights['price_momentum'] * 0.7
                details.append("CE Trend")
            elif not is_ce and current_fast < current_slow:
                score += weights['price_momentum'] * 0.7
                details.append("PE Trend")
            
            if (is_ce and current_price > current_vwap * 1.002) or (not is_ce and current_price < current_vwap * 0.998):
                score += weights['price_momentum'] * 0.3
                details.append("VWAP Aligned")
    
        # 2. Greeks Edge
        if greeks:
            delta = greeks['delta']
            theta = greeks['theta']
            gamma = greeks['gamma']
            
            if is_ce:
                if 0.3 <= abs(delta) <= 0.7:
                    delta_score = 1.0 - abs(abs(delta) - 0.5) * 2
                    score += weights['greeks_edge'] * 0.4 * delta_score
                    details.append(f"δ={delta:.3f}")
            else:
                if -0.7 <= delta <= -0.3:
                    delta_score = 1.0 - abs(abs(delta) - 0.5) * 2
                    score += weights['greeks_edge'] * 0.4 * delta_score
                    details.append(f"δ={delta:.3f}")
            
            if theta > -1.0:
                theta_score = min(1.0, (1.0 + theta))
                score += weights['greeks_edge'] * 0.3 * theta_score
                details.append(f"θ={theta:.2f}")
            
            if gamma > 0.001:
                gamma_score = min(1.0, gamma * 1000)
                score += weights['greeks_edge'] * 0.3 * gamma_score
                details.append(f"γ={gamma:.4f}")
    
        # 3. Volatility Edge
        if iv:
            if 0.15 <= iv <= 0.45:
                iv_score = 1.0 - abs(iv - 0.3) / 0.15
                score += weights['volatility_edge'] * iv_score
                details.append(f"IV={iv:.1%}")
            elif iv < 0.15:
                score += weights['volatility_edge'] * 0.8
                details.append(f"Low IV")
    
        # 4. Volume Surge
        if current_volume > current_volume_ma * 1.5:
            volume_surge = min(2.0, current_volume / current_volume_ma) - 1.0
            score += weights['volume_surge'] * volume_surge
            details.append(f"Vol↑{volume_surge:.2f}x")
        elif current_volume > current_volume_ma * 1.2:
            score += weights['volume_surge'] * 0.5
            details.append("Vol↑")
    
        # 5. OI Momentum
        if current_oi > current_oi_ma * 1.1:
            oi_surge = min(1.5, current_oi / current_oi_ma) - 1.0
            score += weights['oi_momentum'] * oi_surge * 2
            details.append(f"OI↑{oi_surge:.2f}")
        
        # 6. Market Depth Bonus
        if (is_ce and depth_signal > 0) or (not is_ce and depth_signal < 0):
            score += 0.05
            details.append("Depth")
        
        # 7. RSI Confirmation
        if 25 < current_rsi < 75:
            rsi_score = 1.0 - abs(current_rsi - 50) / 25
            score += 0.05 * rsi_score
            details.append(f"RSI={current_rsi:.1f}")
        
        logging.info(f"📊 {symbol} Score: {score:.2f} | {' '.join(details)}")
        return score

    def _get_dynamic_threshold(self, iv, greeks, current_rsi):
        base_threshold = 0.4
        
        if iv and iv < 0.2:
            base_threshold -= 0.1
        
        if greeks and abs(greeks['delta']) > 0.4:
            base_threshold -= 0.05
            
        if 40 <= current_rsi <= 60:
            base_threshold -= 0.05
        
        if iv and iv > 0.5:
            base_threshold += 0.1
            
        return max(0.2, min(0.7, base_threshold))
    
    def analyze_volume_oi_edge(self, current_volume, volume_ma, current_oi, oi_ma, symbol):
        analysis = {
            'volume_score': 0,
            'oi_score': 0,
            'institutional_signal': False,
            'smart_money': False
        }
        
        volume_ratio = current_volume / volume_ma if volume_ma > 0 else 1
        if volume_ratio >= 3.0:
            analysis['volume_score'] = 1.0
            analysis['institutional_signal'] = True
        elif volume_ratio >= 2.0:
            analysis['volume_score'] = 0.8
        elif volume_ratio >= 1.5:
            analysis['volume_score'] = 0.6
        elif volume_ratio >= 1.2:
            analysis['volume_score'] = 0.4
    
        oi_ratio = current_oi / oi_ma if oi_ma > 0 else 1
        if oi_ratio >= 1.2:
            analysis['oi_score'] = 1.0
            analysis['smart_money'] = True
        elif oi_ratio >= 1.1:
            analysis['oi_score'] = 0.7
        elif oi_ratio >= 1.05:
            analysis['oi_score'] = 0.5
        
        if analysis['institutional_signal'] and analysis['smart_money']:
            logging.info(f"🚨 Institutional + Smart Money: {symbol}")
            
        return analysis

    def calculate_option_value_score(self, current_price, strike_price, underlying_price, time_to_expiry, is_ce):
        if is_ce:
            intrinsic_value = max(0, underlying_price - strike_price)
        else:
            intrinsic_value = max(0, strike_price - underlying_price)
        
        time_value = current_price - intrinsic_value
        time_value_ratio = time_value / current_price if current_price > 0 else 1
        
        if time_value_ratio <= 0.3:
            value_score = 1.0
        elif time_value_ratio <= 0.5:
            value_score = 0.8
        elif time_value_ratio <= 0.7:
            value_score = 0.6
        else:
            value_score = 0.3
        
        return value_score, intrinsic_value, time_value

    def calculate_position_size(self, volatility=None):
        try:
            base_size = self.account_balance * self.base_position_size
            if volatility is None and len(self.data) >= self.atr_period:
                volatility = self.volatility_factor
            if volatility > self.volatility_threshold:
                base_size *= 0.5 / volatility
            
            # Institutional boost
            if len(self.data) >= self.volume_ma_period:
                current_volume = self.data['volume'].iloc[-1]
                current_volume_ma = self.data['volume_ma'].iloc[-1]
                current_oi = self.data['oi'].iloc[-1]
                current_oi_ma = self.data['oi_ma'].iloc[-1] if 'oi_ma' in self.data.columns else current_oi
                
                vol_oi_analysis = self.analyze_volume_oi_edge(
                    current_volume, current_volume_ma,
                    current_oi, current_oi_ma,
                    "Current"
                )
                if vol_oi_analysis['institutional_signal']:
                    base_size *= 1.5
                    logging.info("📈 Position boosted 50% for institutional signal")
            
            return max(self.min_position_size, int(base_size))
        except Exception as e:
            return self.min_position_size

    def calculate_stop_loss(self, entry_price, direction):
        try:
            if len(self.data) < self.atr_period:
                return entry_price * (0.98 if direction == 'BUY' else 1.02)
            atr = self.data['atr'].iloc[-1]
            atr_multiplier = 2.5 * self.volatility_factor if self.market_regime in ["UPTREND", "DOWNTREND"] else 1.5 * self.volatility_factor
            stop_loss = entry_price - (atr * atr_multiplier) if direction == 'BUY' else entry_price + (atr * atr_multiplier)
            min_distance = entry_price * 0.01
            return max(stop_loss, entry_price - min_distance) if direction == 'BUY' else min(stop_loss, entry_price + min_distance)
        except Exception as e:
            return entry_price * (0.98 if direction == 'BUY' else 1.02)

    def calculate_option_greeks(self, S, K, T, r, sigma, option_type='call'):
        try:
            S, K, T, r, sigma = map(float, [S, K, T, r, sigma])
            d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
            d2 = d1 - sigma * math.sqrt(T)
            if option_type.lower() == 'call':
                delta = norm.cdf(d1)
                gamma = norm.pdf(d1) / (S * sigma * math.sqrt(T))
                theta = (-S * norm.pdf(d1) * sigma / (2 * math.sqrt(T)) - r * K * math.exp(-r * T) * norm.cdf(d2)) / 365
                vega = S * math.sqrt(T) * norm.pdf(d1) / 100
                rho = K * T * math.exp(-r * T) * norm.cdf(d2) / 100
            else:
                delta = norm.cdf(d1) - 1
                gamma = norm.pdf(d1) / (S * sigma * math.sqrt(T))
                theta = (-S * norm.pdf(d1) * sigma / (2 * math.sqrt(T)) + r * K * math.exp(-r * T) * norm.cdf(-d2)) / 365
                vega = S * math.sqrt(T) * norm.pdf(d1) / 100
                rho = -K * T * math.exp(-r * T) * norm.cdf(-d2) / 100
            return {'delta': delta, 'gamma': gamma, 'theta': theta, 'vega': vega, 'rho': rho}
        except Exception as e:
            return None

    def calculate_implied_volatility(self, option_price, S, K, T, r, option_type='call', max_iterations=100, precision=0.00001):
        try:
            sigma = 0.5
            for _ in range(max_iterations):
                price = self.black_scholes(S, K, T, r, sigma, option_type)
                diff = option_price - price
                if abs(diff) < precision:
                    return sigma
                greeks = self.calculate_option_greeks(S, K, T, r, sigma, option_type)
                vega = greeks['vega'] if greeks else 0
                if vega == 0:
                    return None
                sigma += diff / vega
            return None
        except Exception as e:
            return None

    def black_scholes(self, S, K, T, r, sigma, option_type='call'):
        try:
            d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
            d2 = d1 - sigma * math.sqrt(T)
            if option_type.lower() == 'call':
                return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
            return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
        except Exception as e:
            return None

    def get_params(self):
        return {
            "fast_ema_period": self.fast_ema_period_base,
            "slow_ema_period": self.slow_ema_period_base,
            "rsi_period": self.rsi_period,
            "atr_period": self.atr_period,
            "vwap_period": self.vwap_period,
            "use_fast_ema": self.use_ema,
            "use_slow_ema": self.use_ema,
            "use_rsi": self.use_rsi,
            "use_vwap": self.use_vwap,
            "use_atr": self.use_atr,
            "use_volume": self.use_volume,
            "use_oi": self.use_oi
        }

    def set_params(self, params):
        for k, v in params.items():
            setattr(self, k, v)