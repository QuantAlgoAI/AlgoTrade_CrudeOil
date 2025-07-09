import logging
import csv
import math
from datetime import datetime

# SmartApi may attempt network calls during import. Wrap to avoid fatal crash if DNS is down.
try:
    from SmartApi import SmartConnect
    from SmartApi.smartWebSocketV2 import SmartWebSocketV2
except Exception as e:  # broad except intentional here
    logging.error(f"[!] SmartApi import failed during startup: {e}. Continuing without broker connectivity.")
    SmartConnect = None  # type: ignore
    SmartWebSocketV2 = None  # type: ignore
import pyotp
from mcxlib.market_data import get_market_watch, get_option_chain
import pandas as pd

import traceback
import os
import threading
from flask import Flask, jsonify, render_template, request, send_from_directory, redirect, url_for
import time
import numpy as np
import requests
from strategy import HighWinRateStrategy
import atexit
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from notifier import NotificationManager
from file_watcher import start_watcher
import pytz
import json
from flask.json.provider import JSONProvider
from flask_socketio import SocketIO, emit
from config import Config
from dotenv import load_dotenv, set_key
from pathlib import Path
import sys

# Initialize configuration
config = Config()

# Optional import for performance monitoring
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

# Import optimized components
from optimized_websocket import OptimizedWebSocketHandler
# Broker wrapper for order execution
from broker import Broker
# Import optimized components with error handling
try:
    from database_manager import OptimizedDataManager
    DATABASE_MANAGER_AVAILABLE = True
except ImportError:
    DATABASE_MANAGER_AVAILABLE = False
    # Create a dummy class for fallback
    class OptimizedDataManager:
        def __init__(self, questdb_host='localhost'):
            self.questdb_host = questdb_host
            self.running = False
            self.questdb = None
        
        def start(self):
            pass
        
        def process_tick(self, tick_data):
            pass
        
        def stop(self):
            pass

# NEW: Ultra-fast QuestDB for extreme performance
try:
    from questdb_ultra_fast import UltraFastQuestDBManager
    ULTRA_QUESTDB_AVAILABLE = True
except ImportError:
    ULTRA_QUESTDB_AVAILABLE = False

# ----- Strategy parameter management -----
from backtest.backtest import StrategyBacktester

# Load environment variables
load_dotenv()

# Current strategy params loaded on startup
CURRENT_STRATEGY_PARAMS = StrategyBacktester().strategy_params.copy()

# Path to persist param overrides
PARAMS_FILE = Path('config/strategy_params.json')
if PARAMS_FILE.exists():
    try:
        CURRENT_STRATEGY_PARAMS.update(json.loads(PARAMS_FILE.read_text()))
    except Exception:
        pass

# === CONFIG FROM ENVIRONMENT ===
API_KEY = os.getenv('ANGEL_ONE_API_KEY')
CLIENT_CODE = os.getenv('ANGEL_ONE_CLIENT_CODE')
PASSWORD = os.getenv('ANGEL_ONE_PASSWORD')
TOTP_SECRET = os.getenv('ANGEL_ONE_TOTP_SECRET')

# Dry-run flag: set DRY_RUN=false in .env to send live orders
ENV_PATH = Path('.env')

DRY_RUN = os.getenv('DRY_RUN', 'true').lower() != 'false'

# Validate required environment variables
# Validate required environment variables with detailed feedback
missing_vars = [
    var_name for var_name, val in [
        ("ANGEL_ONE_API_KEY", API_KEY),
        ("ANGEL_ONE_CLIENT_CODE", CLIENT_CODE),
        ("ANGEL_ONE_PASSWORD", PASSWORD),
        ("ANGEL_ONE_TOTP_SECRET", TOTP_SECRET),
    ] if not val
]
if missing_vars:
    msg = (
        "\n🚫 Required credentials not found: " + ", ".join(missing_vars) +
        "\nCreate a .env file (see .env.example) in the project root with the correct values and restart the app."
    )
    logger.error(msg)
    # Exit immediately but keep the message visible
    raise SystemExit(msg)



# File path for instrument CSV (daily updated)
SCRIP_MASTER_FILE = f"instruments/{datetime.today().strftime('%Y%m%d')}_instrument_file.csv"

# Notification settings from environment
notification_settings = {
    'telegram': {
        'enabled': True,
        'token': os.getenv('TELEGRAM_TOKEN'),
        'chat_id': os.getenv('TELEGRAM_CHAT_ID')
    },
    'email': {
        'enabled': True,
        'user': os.getenv('GMAIL_USER'),
        'pass': os.getenv('GMAIL_PASS')
    }
}

def setup_logging():
    """Setup logging configuration"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    return logging.getLogger()

logger = setup_logging()

def get_crude_atm_strike():
    """Get ATM strike for CRUDEOIL using mcxlib, and return nearest MCX OPTFUT expiry for options trading"""
    try:
        market_data = get_market_watch()
        logger.info(f"Market watch columns: {list(market_data.columns)}")

        # Get futures data for spot price
        crude_fut = market_data[
            (market_data['Symbol'] == 'CRUDEOIL') &
            (market_data['InstrumentName'] == 'FUTCOM')
        ].copy()

        if crude_fut.empty:
            raise Exception("No CRUDEOIL futures found")

        crude_fut['ExpiryDate'] = pd.to_datetime(crude_fut['ExpiryDate'], format='%d%b%Y', errors='coerce')
        crude_fut = crude_fut.sort_values('ExpiryDate')

        spot_price = float(crude_fut.iloc[0]['LTP'])
        fut_expiry = crude_fut.iloc[0]['ExpiryDate'].strftime('%d%b%Y').upper()

        logger.info(f"Spot price from MCX nearest futures: {spot_price}")
        logger.info(f"Futures expiry: {fut_expiry}")

        # Calculate ATM strike
        atm_strike = round(spot_price / 50) * 50
        logger.info(f"Calculated ATM strike: {atm_strike}")

        # Get options expiry (OPTFUT)
        df = pd.read_csv(SCRIP_MASTER_FILE, low_memory=False)
        opt_df = df[
            (df['name'] == 'CRUDEOIL') &
            (df['exch_seg'] == 'MCX') &
            (df['instrumenttype'] == 'OPTFUT')
        ]
        
        if opt_df.empty:
            raise Exception("No CRUDEOIL options found")

        opt_df = opt_df.copy()
        opt_df.loc[:, 'expiry_date'] = pd.to_datetime(opt_df['expiry'], format='%d%b%Y', errors='coerce')
        opt_df = opt_df.dropna(subset=['expiry_date']).copy()
        today = pd.Timestamp.now().normalize()
        future_expiries = opt_df[opt_df['expiry_date'] >= today]
        
        if future_expiries.empty:
            raise Exception("No future MCX OPTFUT expiries found for CRUDEOIL")
            
        opt_expiry = future_expiries.iloc[0]['expiry']
        logger.info(f"Options expiry: {opt_expiry}")

        return atm_strike, spot_price, opt_expiry

    except Exception as e:
        logger.error(f"Error getting ATM strike: {str(e)}")
        logger.error(traceback.format_exc())
        return None, None, None

def resolve_token(symbol: str, strike: int, expiry: str, option_type: str = None):
    """Resolve token from instrument CSV file"""
    try:
        if not os.path.exists(SCRIP_MASTER_FILE):
            logger.error(f"Instrument file not found: {SCRIP_MASTER_FILE}")
            return None

        df = pd.read_csv(SCRIP_MASTER_FILE, low_memory=False)

        base_mask = (
            (df['name'] == symbol) &
            (df['exch_seg'] == 'MCX')
        )
        base_filtered = df[base_mask]

        if base_filtered.empty:
            logger.error(f"No {symbol} contracts found in MCX")
            return None

        # Futures
        if option_type is None:
            futures_mask = (base_filtered['instrumenttype'] == 'FUTCOM')
            matches = base_filtered[futures_mask]
            if not matches.empty:
                # Convert expiry to datetime for comparison
                expiry_dt = pd.to_datetime(expiry, format='%d%b%Y')
                matches = matches.copy()
                matches.loc[:, 'expiry_dt'] = pd.to_datetime(matches['expiry'], format='%d%b%Y', errors='coerce')
                matches = matches.dropna(subset=['expiry_dt']).copy()
                
                # Find exact match
                exact_match = matches[matches['expiry_dt'] == expiry_dt]
                if not exact_match.empty:
                    token = str(exact_match.iloc[0]['token'])
                    logger.info(f"Found futures token: {token} for {symbol} {expiry} on MCX")
                    return token
                
                # If no exact match, find nearest future expiry
                future_expiries = matches[matches['expiry_dt'] >= expiry_dt]
                if not future_expiries.empty:
                    token = str(future_expiries.iloc[0]['token'])
                    logger.info(f"Found nearest futures token: {token} for {symbol} {future_expiries.iloc[0]['expiry']} on MCX")
                    return token

        # Options
        else:
            options_mask = (base_filtered['instrumenttype'] == 'OPTFUT')
            options = base_filtered[options_mask]
            if options.empty:
                logger.error(f"No option contracts found for {symbol}")
                return None

            mcx_matches = options[
                (options['strike'] == float(strike) * 100) &
                (options['symbol'].str.contains(option_type, na=False))
            ]

            matches = mcx_matches
            if matches.empty:
                logger.error(f"No MCX matches found for strike {strike} and type {option_type}")
                return None

            if 'expiry' in matches.columns:
                matches = matches.copy()
                matches.loc[:, 'expiry_date'] = pd.to_datetime(matches['expiry'], format='%d%b%Y', errors='coerce')
                matches = matches.sort_values('expiry_date')

            token = str(matches.iloc[0]['token'])
            symbol_found = matches.iloc[0]['symbol']
            exch = matches.iloc[0]['exch_seg']
            logger.info(f"Found option token: {token} for symbol {symbol_found} on {exch}")
            return token

        return None

    except Exception as e:
        logger.error(f"Error resolving token: {str(e)}")
        logger.error(traceback.format_exc())
        return None

class CrudeATMWebSocket:
    def __init__(self):
        self.smartapi = None
        self.websocket = None
        self.atm_strike = None
        self.spot_price = None
        self.expiry = None
        self.tokens = []  # [fut_token, ce_token, pe_token]
        self.token_type_map = {}  # Map token to type (FUT/CE/PE)
        
        # NEW: High-performance components
        self.optimized_handler = OptimizedWebSocketHandler()
        # Define the intervals to be pre-calculated
        self.supported_intervals = ["1s", "5s", "10s", "30s", "1min", "5min"]
        self.optimized_handler.set_supported_intervals(self.supported_intervals)

        # Simplified data manager without problematic async components
        self.data_manager = OptimizedDataManager(questdb_host='localhost')
        
        # Legacy components (for backward compatibility)
        self.tick_buffer = []  # Keep for compatibility
        self.ohlc_fut = pd.DataFrame()
        self.ohlc_ce = pd.DataFrame()
        self.ohlc_pe = pd.DataFrame()
        self.lock = threading.Lock()
        
        self.fut_info = {}
        self.ce_info = {}
        self.pe_info = {}
        
        # Strategy components
        self.strategy_ce = HighWinRateStrategy(contract_hub=None)
        self.strategy_pe = HighWinRateStrategy(contract_hub=None)
        self.latest_signal_ce = None
        self.latest_signal_pe = None
        self.latest_indicators_ce = {}
        self.latest_indicators_pe = {}
        # Broker instance will be initialised after login
        self.broker = None
        # === Trade management additions ===
        self.trade_cooldown_seconds = 300  # 5-minute cool-down between entries
        self.last_trade_time = None  # timestamp of last executed trade
        self.current_day = datetime.now(pytz.timezone('Asia/Kolkata')).date()  # track trading day
        
        # File-based backup (keep for reliability)
        self.tick_buffer_file = 'buffer/tick_buffer.csv'
        self._load_tick_buffer()
        
        # Initialize notification manager
        self.notifier = NotificationManager()
        atexit.register(self._save_tick_buffer)
        
        # Start optimized components safely
        try:
            self.data_manager.start()
            self.optimized_handler.start()
            logger.info("✅ Optimized components started successfully")
        except Exception as e:
            logger.error(f"❌ Failed to start optimized components: {e}")
            # Continue without optimized components

    def _save_tick_buffer(self):
        with self.lock:
            if self.tick_buffer:
                df = pd.DataFrame(self.tick_buffer)
                # Convert timestamp to string for CSV storage
                if 'timestamp' in df.columns:
                    df['timestamp'] = df['timestamp'].astype(str)
                df.to_csv(self.tick_buffer_file, index=False)
                logger.info(f"Tick buffer saved to {self.tick_buffer_file}")

    def _load_tick_buffer(self):
        if os.path.isfile(self.tick_buffer_file):
            try:
                df = pd.read_csv(self.tick_buffer_file)
                # Convert timestamp back to datetime
                if 'timestamp' in df.columns:
                    # Handle empty or NaN timestamps
                    df = df.dropna(subset=['timestamp'])
                    df = df[df['timestamp'].astype(str).str.strip() != '']  # Remove empty string timestamps
                    if not df.empty:
                        df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
                        df = df.dropna(subset=['timestamp'])  # Drop rows with invalid timestamps
                        # Convert back to list of dictionaries with proper timestamp objects
                        self.tick_buffer = []
                        for _, row in df.iterrows():
                            tick_dict = row.to_dict()
                            self.tick_buffer.append(tick_dict)
                        logger.info(f"Loaded tick buffer from {self.tick_buffer_file}, {len(self.tick_buffer)} records.")
                    else:
                        logger.warning("No valid timestamps found in tick buffer file")
                        self.tick_buffer = []
                else:
                    logger.warning("No timestamp column found in tick buffer file")
                    self.tick_buffer = []
            except Exception as e:
                logger.error(f"Failed to load tick buffer: {e}")
                self.tick_buffer = []
        else:
            self.tick_buffer = []

    # === NEW: Daily reset & signal handling helpers ===
    def _ensure_daily_reset(self):
        """Reset per-day counters when a new trading day starts."""
        ist = pytz.timezone('Asia/Kolkata')
        today = datetime.now(ist).date()
        if getattr(self, 'current_day', None) != today:
            self.current_day = today
            for strat in [self.strategy_ce, self.strategy_pe]:
                strat.trades_today = 0
                strat.daily_pnl = 0
                strat.trade_state = 'IDLE'
            self.last_trade_time = None
            logger.info("🔄 Daily trade counters reset")

    def _can_take_trade(self, strategy):
        """Return True if strategy is allowed to open a new position."""
        now = time.time()
        if strategy.trade_state != 'IDLE':
            return False
        if strategy.trades_today >= strategy.max_trades_per_day:
            return False
        if getattr(self, 'last_trade_time', None) and (now - self.last_trade_time) < self.trade_cooldown_seconds:
            return False
        return True

    def _handle_signal(self, option_type, signal):
        """Execute basic state transitions for BUY / EXIT signals."""
        strategy = self.strategy_ce if option_type == "CE" else self.strategy_pe
        if signal == 'BUY' and self._can_take_trade(strategy):
            # TODO: integrate real order placement here
            logger.info(f"🛒 Executing BUY for {option_type}")
            # Determine symbol/token/lot-size
            info = self.ce_info if option_type == "CE" else self.pe_info
            lot_size = int(info.get('lotsize', 1)) if info else 1
            symbol = info.get('symbol') if info else None
            # Map option type to token
            token_key = next((tok for tok, typ in self.token_type_map.items() if typ == option_type), None)
            order_resp = self.broker.place_market_order(
                symbol_token=str(token_key),
                trading_symbol=symbol or option_type,
                transaction_type='BUY',
                quantity=lot_size,
            )
            strategy.entry_price = strategy.data['ltp'].iloc[-1] if not strategy.data.empty else None
            strategy.trade_state = 'OPEN'
            strategy.trades_today += 1
            self.last_trade_time = time.time()
            # Log trade
            self._log_trade({
                'timestamp': datetime.now(pytz.timezone('Asia/Kolkata')).isoformat(),
                'side': 'BUY',
                'symbol': symbol,
                'token': token_key,
                'quantity': lot_size,
                'response': order_resp,
                'strategy': option_type,
            })
        elif signal == 'EXIT' and strategy.trade_state == 'OPEN':
            logger.info(f"💼 Exiting {option_type} position")
            # close position via SELL
            info = self.ce_info if option_type == "CE" else self.pe_info
            lot_size = int(info.get('lotsize', 1)) if info else 1
            symbol = info.get('symbol') if info else None
            token_key = next((tok for tok, typ in self.token_type_map.items() if typ == option_type), None)
            order_resp = self.broker.place_market_order(
                symbol_token=str(token_key),
                trading_symbol=symbol or option_type,
                transaction_type='SELL',
                quantity=lot_size,
            )
            self._log_trade({
                'timestamp': datetime.now(pytz.timezone('Asia/Kolkata')).isoformat(),
                'side': 'SELL',
                'symbol': symbol,
                'token': token_key,
                'quantity': lot_size,
                'response': order_resp,
                'strategy': option_type,
            })
            strategy.trade_state = 'IDLE'
            strategy.entry_price = None

        def _log_trade(self, trade_dict):
            """Append trade details to trades.csv in project root."""
            csv_path = Path('trades.csv')
            header_needed = not csv_path.exists()
            try:
                with csv_path.open('a', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=trade_dict.keys())
                    if header_needed:
                        writer.writeheader()
                    writer.writerow(trade_dict)
            except Exception as exc:
                logger.error("Failed to log trade: %s", exc)

    # === END helper methods ===

    def _start_tick_buffer_saver(self):
        def periodic_save():
            while True:
                time.sleep(60)
                self._save_tick_buffer()
        t = threading.Thread(target=periodic_save, daemon=True)
        t.start()

    def login(self):
        """Login to SmartAPI"""
        try:
            self.smartapi = SmartConnect(api_key=API_KEY)
            totp = pyotp.TOTP(TOTP_SECRET)
            session = self.smartapi.generateSession(CLIENT_CODE, PASSWORD, totp.now())

            if not session or not session.get('data'):
                raise Exception("Failed to generate session")

            self.feed_token = session['data']['feedToken']
            self.jwt_token = session['data']['jwtToken']
            # Initialise broker wrapper
            self.broker = Broker(self.smartapi, dry_run=DRY_RUN)
            # expose to Flask routes
            app.broker = self.broker
            logger.info("Successfully logged in to SmartAPI")
            return True

        except Exception as e:
            logger.error(f"Login error: {str(e)}")
            return False

    def on_data(self, ws, message):
        try:
            logger.info(f"📈 Tick: {message}")
            # Daily reset for counters
            self._ensure_daily_reset()
            if isinstance(message, dict):
                token = str(message.get('token', ''))
                if token in self.tokens:
                    ltp = float(message.get('last_traded_price', 0)) / 100
                    volume = int(message.get('volume_trade_for_the_day', 0))
                    oi = int(message.get('open_interest', 0))
                    
                    # Convert to IST
                    ist = pytz.timezone('Asia/Kolkata')
                    tick_time = pd.to_datetime(
                        message.get('exchange_timestamp', int(time.time()*1000)),
                        unit='ms'
                    ).tz_localize('UTC').tz_convert(ist)
                    
                    tick_type = self.token_type_map.get(token, "UNKNOWN")
                    
                    # NEW: Process through optimized handler
                    optimized_tick = {
                        'token': token,
                        'ltp': ltp,
                        'volume': volume,
                        'oi': oi,
                        'type': tick_type,
                        'timestamp': tick_time,
                        'open': float(message.get('open_price_of_the_day', 0)) / 100,
                        'high': float(message.get('high_price_of_the_day', 0)) / 100,
                        'low': float(message.get('low_price_of_the_day', 0)) / 100,
                        'symbol': 'CRUDEOIL'  # Add symbol
                    }
                    
                    # Process through optimized system
                    self.optimized_handler.process_market_data(optimized_tick)
                    self.data_manager.process_tick(optimized_tick)
                    
                    # Emit real-time data via SocketIO (rate-limited by optimized handler)
                    socketio.emit('market_data', {
                        'type': tick_type,
                        'ltp': ltp,
                        'volume': volume,
                        'oi': oi,
                        'timestamp': tick_time.isoformat()
                    })
                    
                    # Legacy tick buffer (keep for backward compatibility)
                    with self.lock:
                        if len(self.tick_buffer) > 1000:
                            self.tick_buffer = self.tick_buffer[-1000:]
                        
                        self.tick_buffer.append({
                            'timestamp': tick_time,
                            'ltp': ltp,
                            'volume': volume,
                            'oi': oi,
                            'token': token,
                            'type': tick_type,
                            'open': float(message.get('open_price_of_the_day', 0)) / 100,
                            'high': float(message.get('high_price_of_the_day', 0)) / 100,
                            'low': float(message.get('low_price_of_the_day', 0)) / 100,
                        })

                    # Update strategy data
                    if tick_type == "CE":
                        self.strategy_ce.update_data(message)
                        self.latest_signal_ce = self.strategy_ce.generate_signals(message)
                         # Handle trade state & limits
                        self._handle_signal("CE", self.latest_signal_ce)
                        exit_sig = self.strategy_ce.check_exit_conditions()
                        if exit_sig:
                            self._handle_signal("CE", exit_sig)
                        if not self.strategy_ce.data.empty:
                            last = self.strategy_ce.data.iloc[-1]
                            self.latest_indicators_ce = {
                                'fast_ema': float(last.get('fast_ema', 0)) if not pd.isna(last.get('fast_ema')) else None,
                                'slow_ema': float(last.get('slow_ema', 0)) if not pd.isna(last.get('slow_ema')) else None,
                                'rsi': float(last.get('rsi', 0)) if not pd.isna(last.get('rsi')) else None,
                                'vwap': float(last.get('vwap', 0)) if not pd.isna(last.get('vwap')) else None,
                                 'atr': float(last.get('atr', 0)) if not pd.isna(last.get('atr')) else None,
                                 'macd': float(last.get('macd', 0)) if not pd.isna(last.get('macd')) else None,
                                 'macd_signal': float(last.get('macd_signal', 0)) if not pd.isna(last.get('macd_signal')) else None,
                                'market_regime': self.strategy_ce.market_regime if self.strategy_ce.market_regime else None,
                            }
                            # Emit strategy data
                            socketio.emit('strategy_update', {
                                'type': 'CE',
                                'indicators': self.latest_indicators_ce,
                                'signal': self.latest_signal_ce
                            })
                    elif tick_type == "PE":
                        self.strategy_pe.update_data(message)
                        self.latest_signal_pe = self.strategy_pe.generate_signals(message)
                        # Handle trade state & limits
                        self._handle_signal("PE", self.latest_signal_pe)
                        exit_sig = self.strategy_pe.check_exit_conditions()
                        if exit_sig:
                            self._handle_signal("PE", exit_sig)
                        if not self.strategy_pe.data.empty:
                            last = self.strategy_pe.data.iloc[-1]
                            self.latest_indicators_pe = {
                                'fast_ema': float(last.get('fast_ema', 0)) if not pd.isna(last.get('fast_ema')) else None,
                                'slow_ema': float(last.get('slow_ema', 0)) if not pd.isna(last.get('slow_ema')) else None,
                                'rsi': float(last.get('rsi', 0)) if not pd.isna(last.get('rsi')) else None,
                                'vwap': float(last.get('vwap', 0)) if not pd.isna(last.get('vwap')) else None,
                                 'atr': float(last.get('atr', 0)) if not pd.isna(last.get('atr')) else None,
                                 'macd': float(last.get('macd', 0)) if not pd.isna(last.get('macd')) else None,
                                 'macd_signal': float(last.get('macd_signal', 0)) if not pd.isna(last.get('macd_signal')) else None,
                                'market_regime': self.strategy_pe.market_regime if self.strategy_pe.market_regime else None,
                            }
                            # Emit strategy data
                            socketio.emit('strategy_update', {
                                'type': 'PE',
                                'indicators': self.latest_indicators_pe,
                                'signal': self.latest_signal_pe
                            })

        except Exception as e:
            logger.error(f"Error processing tick: {str(e)}")
            logger.error(traceback.format_exc())

    def initialize_websocket(self):
        """Initialize and connect WebSocket with optimized handler"""
        try:
            # Initialize optimized handler with SocketIO reference
            if hasattr(self, 'optimized_handler'):
                self.optimized_handler.socketio = socketio
            
            self.websocket = SmartWebSocketV2(
                self.jwt_token,
                API_KEY,
                CLIENT_CODE,
                self.feed_token
            )

            def on_open(ws):
                logger.info("✅ WebSocket Connected")
                socketio.emit('websocket_status', {'status': 'connected'})
                self.subscribe()

            def on_close(ws):
                logger.info("🔌 WebSocket Closed")
                socketio.emit('websocket_status', {'status': 'disconnected'})

            def on_error(ws, error):
                logger.error(f"WebSocket error: {error}")
                socketio.emit('websocket_status', {'status': 'error', 'message': str(error)})

            self.websocket.on_open = on_open
            self.websocket.on_data = self.on_data  # Use the class method
            self.websocket.on_error = on_error
            self.websocket.on_close = on_close

            return True

        except Exception as e:
            logger.error(f"WebSocket initialization error: {str(e)}")
            return False

    def subscribe(self):
        """Subscribe to market data for futures and ATM strikes"""
        try:
            self.atm_strike, self.spot_price, self.expiry = get_crude_atm_strike()
            if not self.atm_strike or not self.expiry:
                self.notifier.notify_error("Subscription", "Failed to get ATM strike or expiry")
                raise Exception("Failed to get ATM strike or expiry")

            # Get futures token first
            fut_token = resolve_token("CRUDEOIL", None, self.expiry)
            if not fut_token:
                self.notifier.notify_error("Subscription", "Failed to resolve futures token")
                raise Exception("Failed to resolve futures token")

            # Get CE and PE tokens
            ce_token = resolve_token("CRUDEOIL", self.atm_strike, self.expiry, "CE")
            pe_token = resolve_token("CRUDEOIL", self.atm_strike, self.expiry, "PE")

            if not ce_token or not pe_token:
                self.notifier.notify_error("Subscription", "Failed to resolve option tokens")
                raise Exception("Failed to resolve option tokens")

            self.tokens = [fut_token, ce_token, pe_token]
            self.token_type_map = {
                str(fut_token): "FUT",
                str(ce_token): "CE", 
                str(pe_token): "PE"
            }

            # Notify startup
            self.notifier.notify_startup(
                instrument="CRUDEOIL",
                expiry=self.expiry,
                spot_price=self.spot_price,
                atm_strike=self.atm_strike,
                monitored=["FUTURES", "CE", "PE"],
                strategy="High Win Rate"
            )

            df = pd.read_csv(SCRIP_MASTER_FILE, low_memory=False)

            # Get futures info
            fut_row = df[df['token'].astype(str) == str(fut_token)].iloc[0] if not df[df['token'].astype(str) == str(fut_token)].empty else None
            if fut_row is not None:
                self.fut_info = {
                    'symbol': fut_row['symbol'],
                    'expiry': fut_row['expiry'],
                    'lotsize': fut_row['lotsize'],
                }

            # Get CE and PE info
            ce_row = df[df['token'].astype(str) == str(ce_token)].iloc[0] if not df[df['token'].astype(str) == str(ce_token)].empty else None
            pe_row = df[df['token'].astype(str) == str(pe_token)].iloc[0] if not df[df['token'].astype(str) == str(pe_token)].empty else None
            if ce_row is not None:
                self.ce_info = {
                    'symbol': ce_row['symbol'],
                    'strike': ce_row['strike'],
                    'expiry': ce_row['expiry'],
                    'lotsize': ce_row['lotsize'],
                    'optiontype': ce_row['symbol'][-2:],
                }
            if pe_row is not None:
                self.pe_info = {
                    'symbol': pe_row['symbol'],
                    'strike': pe_row['strike'],
                    'expiry': pe_row['expiry'],
                    'lotsize': pe_row['lotsize'],
                    'optiontype': pe_row['symbol'][-2:],
                }

            mcx_tokens = []
            for token in self.tokens:
                str_token = str(token)
                token_info = df[df['token'].astype(str) == str_token]
                if not token_info.empty:
                    exch = token_info.iloc[0]['exch_seg']
                    logger.info(f"Token {token} belongs to exchange {exch}")
                    if exch == 'MCX':
                        mcx_tokens.append(str_token)
                else:
                    logger.error(f"Could not find exchange info for token {token}")

            token_list = []
            if mcx_tokens:
                token_list.append({
                    "exchangeType": 5,  # MCX
                    "tokens": mcx_tokens
                })
                logger.info(f"Added MCX subscription for tokens: {mcx_tokens}")

            if not token_list:
                raise Exception("No valid tokens found for subscription")

            logger.info(f"Subscribing with token list: {token_list}")
            self.websocket.subscribe(
                correlation_id="ws_crude_atm",
                mode=3,
                token_list=token_list
            )
            logger.info(f"✅ Subscribed to MCX tokens: {mcx_tokens}")

        except Exception as e:
            self.notifier.notify_error("Subscription", str(e))
            logger.error(f"Subscription error: {str(e)}")
            logger.error(traceback.format_exc())

    def start(self):
        """Start WebSocket connection"""
        if self.login() and self.initialize_websocket():
            try:
                self.websocket.connect()
            except Exception as e:
                logger.error(f"Connection error: {str(e)}")

    def close(self):
        """Close WebSocket connection"""
        if self.websocket:
            self.websocket.close_connection()

    def aggregate_ohlc(self, interval='5s'):
        """Aggregate tick_buffer into OHLC DataFrames for futures, CE and PE."""
        with self.lock:
            logger.info(f"Aggregating OHLC data with interval {interval}. Tick buffer size: {len(self.tick_buffer)}")
            if not self.tick_buffer:
                logger.warning("Tick buffer is empty")
                return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
            
            # Convert tick buffer to DataFrame
            df = pd.DataFrame(self.tick_buffer)
            
            # **Clear the buffer after converting it to a DataFrame**
            self.tick_buffer.clear()
            logger.info("Legacy tick buffer cleared after aggregation.")

            if df.empty:
                logger.warning("Tick buffer DataFrame is empty")
                return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
            
            # Ensure timestamp is datetime and set as index
            if 'timestamp' in df.columns:
                # Check if timestamp column contains timezone-aware objects
                if df['timestamp'].dtype == 'object':
                    # If they're already pandas Timestamps, just set as index
                    try:
                        # Try to convert, handling timezone-aware objects
                        df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
                    except ValueError:
                        # If conversion fails, assume they're already proper datetime objects
                        pass
                df = df.set_index('timestamp')
                df = df.sort_index()  # Sort by timestamp
            else:
                logger.error("No timestamp column in tick buffer data")
                return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
            
            # Split data by type
            fut_df = df[df['type'] == 'FUT'].copy()
            ce_df = df[df['type'] == 'CE'].copy()
            pe_df = df[df['type'] == 'PE'].copy()
            
            logger.info(f"Split data sizes - FUT: {len(fut_df)}, CE: {len(ce_df)}, PE: {len(pe_df)}")
            
            def calculate_ohlc(data_df, name):
                if data_df.empty:
                    logger.warning(f"No {name} data available")
                    return pd.DataFrame()
                try:
                    # Basic OHLC
                    ohlc = data_df.resample(interval).agg(
                        open=('ltp', 'first'),
                        high=('ltp', 'max'),
                        low=('ltp', 'min'),
                        close=('ltp', 'last'),
                        volume=('volume', 'last'),
                        oi=('oi', 'last')
                    ).ffill()
                    
                    # Flatten column names
                    ohlc.columns = ['open', 'high', 'low', 'close', 'volume', 'oi']
                    
                    # Technical Indicators for CE/PE
                    if name in ['CE', 'PE']:
                        strategy = self.strategy_ce if name == 'CE' else self.strategy_pe
                        
                        # EMA
                        ohlc['fast_ema'] = ohlc['close'].ewm(span=strategy.fast_ema_period, adjust=False).mean()
                        ohlc['slow_ema'] = ohlc['close'].ewm(span=strategy.slow_ema_period, adjust=False).mean()
                        # MACD
                        ohlc['macd'] = ohlc['fast_ema'] - ohlc['slow_ema']
                        ohlc['macd_signal'] = ohlc['macd'].ewm(span=9, adjust=False).mean()
                        
                        # VWAP
                        typical_price = (ohlc['high'] + ohlc['low'] + ohlc['close']) / 3
                        ohlc['vwap'] = (typical_price * ohlc['volume']).cumsum() / ohlc['volume'].cumsum()
                        
                        # RSI
                        delta = ohlc['close'].diff()
                        gain = (delta.where(delta > 0, 0)).rolling(window=strategy.rsi_period).mean()
                        loss = (-delta.where(delta < 0, 0)).rolling(window=strategy.rsi_period).mean()
                        rs = gain / loss
                        ohlc['rsi'] = 100 - (100 / (1 + rs))
                        
                        # ATR
                        high_low = ohlc['high'] - ohlc['low']
                        high_close = np.abs(ohlc['high'] - ohlc['close'].shift())
                        low_close = np.abs(ohlc['low'] - ohlc['close'].shift())
                        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
                        ohlc['atr'] = true_range.rolling(window=strategy.atr_period).mean()
                        
                        # Emit strategy update
                        if not ohlc.empty:
                            last_row = ohlc.iloc[-1]
                            socketio.emit('strategy_update', {
                                'type': name,
                                'indicators': {
                                    'fast_ema': float(last_row['fast_ema']) if not pd.isna(last_row['fast_ema']) else None,
                                    'slow_ema': float(last_row['slow_ema']) if not pd.isna(last_row['slow_ema']) else None,
                                    'macd': float(last_row['macd']) if not pd.isna(last_row['macd']) else None,
                                    'macd_signal': float(last_row['macd_signal']) if not pd.isna(last_row['macd_signal']) else None,
                                    'rsi': float(last_row['rsi']) if not pd.isna(last_row['rsi']) else None,
                                    'vwap': float(last_row['vwap']) if not pd.isna(last_row['vwap']) else None,
                                    'atr': float(last_row['atr']) if not pd.isna(last_row['atr']) else None,
                                    'market_regime': strategy.market_regime if hasattr(strategy, 'market_regime') else None
                                }
                            })
                    
                    # Replace NaN with None for JSON serialization
                    ohlc = ohlc.replace([np.inf, -np.inf], np.nan)
                    ohlc = ohlc.where(pd.notnull(ohlc), None)
                    
                    logger.info(f"{name} OHLC created with {len(ohlc)} rows")
                    return ohlc.reset_index()
                    
                except Exception as e:
                    logger.error(f"Error calculating {name} OHLC: {str(e)}")
                    logger.error(traceback.format_exc())
                    return pd.DataFrame()
            
            # Calculate OHLC for each type
            ohlc_fut = calculate_ohlc(fut_df, "FUT")
            ohlc_ce = calculate_ohlc(ce_df, "CE")
            ohlc_pe = calculate_ohlc(pe_df, "PE")
            
            return ohlc_fut, ohlc_ce, ohlc_pe

    def get_account_summary(self):
        try:
            if not self.smartapi:
                return {'balance': '--', 'pnl': '--'}
            rms = self.smartapi.rmsLimit()
            if rms and 'data' in rms:
                return {
                    'balance': rms['data'].get('net', '--'),
                    'pnl': rms['data'].get('realizedprofitloss', '--')
                }
        except Exception as e:
            logger.error(f'Error fetching account summary: {e}')
        return {'balance': '--', 'pnl': '--'}

    def get_strategy_status(self):
        """Get current strategy status for both CE and PE options"""
        def clean_indicators(indicators):
            return {k: None if isinstance(v, float) and (math.isnan(v) or math.isinf(v)) else v 
                   for k, v in indicators.items()}
        
        # Get latest OHLC data using a fixed interval for strategy calculation
        strategy_interval = '5s'
        _, ce_ohlc, pe_ohlc = self.aggregate_ohlc(interval=strategy_interval)
        
        # Update indicators from OHLC
        if not ce_ohlc.empty:
            last_ce = ce_ohlc.iloc[-1]
            self.latest_indicators_ce = {
                'fast_ema': float(last_ce.get('fast_ema', 0)) if not pd.isna(last_ce.get('fast_ema')) else None,
                'slow_ema': float(last_ce.get('slow_ema', 0)) if not pd.isna(last_ce.get('slow_ema')) else None,
                'rsi': float(last_ce.get('rsi', 0)) if not pd.isna(last_ce.get('rsi')) else None,
                'vwap': float(last_ce.get('vwap', 0)) if not pd.isna(last_ce.get('vwap')) else None,
                 'atr': float(last_ce.get('atr', 0)) if not pd.isna(last_ce.get('atr')) else None,
                 'macd': float(last_ce.get('macd', 0)) if not pd.isna(last_ce.get('macd')) else None,
                 'macd_signal': float(last_ce.get('macd_signal', 0)) if not pd.isna(last_ce.get('macd_signal')) else None,
                'market_regime': self.strategy_ce.market_regime if self.strategy_ce.market_regime else None,
            }
        
        if not pe_ohlc.empty:
            last_pe = pe_ohlc.iloc[-1]
            self.latest_indicators_pe = {
                'fast_ema': float(last_pe.get('fast_ema', 0)) if not pd.isna(last_pe.get('fast_ema')) else None,
                'slow_ema': float(last_pe.get('slow_ema', 0)) if not pd.isna(last_pe.get('slow_ema')) else None,
                'rsi': float(last_pe.get('rsi', 0)) if not pd.isna(last_pe.get('rsi')) else None,
                'vwap': float(last_pe.get('vwap', 0)) if not pd.isna(last_pe.get('vwap')) else None,
                 'atr': float(last_pe.get('atr', 0)) if not pd.isna(last_pe.get('atr')) else None,
                 'macd': float(last_pe.get('macd', 0)) if not pd.isna(last_pe.get('macd')) else None,
                 'macd_signal': float(last_pe.get('macd_signal', 0)) if not pd.isna(last_pe.get('macd_signal')) else None,
                'market_regime': self.strategy_pe.market_regime if self.strategy_pe.market_regime else None,
            }
        
        return {
            'ce': {
                'signal': self.latest_signal_ce,
                'indicators': clean_indicators(self.latest_indicators_ce),
                'info': self.ce_info
            },
            'pe': {
                'signal': self.latest_signal_pe,
                'indicators': clean_indicators(self.latest_indicators_pe),
                'info': self.pe_info
            }
        }

# Ensure today's instrument file is present

def ensure_instrument_file():
    today_date = datetime.today().strftime('%Y%m%d')
    instruments_file = f"instruments/{today_date}_instrument_file.csv"
    if os.path.isfile(instruments_file):
        logging.info("[+] Instrument file is already present for today.")
    else:
        logging.info("[+] CSV file not found for today. Downloading...")
        url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            logging.error(f"[!] Failed to download instrument file: {e}")
            return  # Continue without stopping the whole service
        requests_data = response.json()
        df = pd.DataFrame.from_dict(requests_data)
        df.to_csv(instruments_file, index=False)
        logging.info("[+] Instrument file Downloaded successfully")

ensure_instrument_file()

def send_telegram_message(token, chat_id, message):
    """Send message via Telegram bot"""
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML"
        }
        response = requests.post(url, json=data, timeout=10)
        response.raise_for_status()
        return True, "Message sent successfully"
    except requests.exceptions.RequestException as e:
        return False, str(e)

def send_email(user, password, subject, body):
    """Send email via Gmail SMTP"""
    try:
        msg = MIMEMultipart()
        msg['From'] = user
        msg['To'] = user  # Sending to self
        msg['Subject'] = subject
        
        msg.attach(MIMEText(body, 'plain'))
        
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(user, password)
        server.send_message(msg)
        server.quit()
        
        return True, "Email sent successfully"
    except Exception as e:
        return False, str(e)

class CustomJSONProvider(JSONProvider):
    def dumps(self, obj, **kwargs):
        def _default(o):
            if isinstance(o, np.integer):
                return int(o)
            if isinstance(o, np.floating):
                if np.isnan(o):
                    return None
                return float(o)
            if isinstance(o, np.ndarray):
                return o.tolist()
            if isinstance(o, pd.Timestamp):
                return o.isoformat()
            if isinstance(o, pd.DataFrame):
                return o.replace({np.nan: None}).to_dict(orient='records')
            if isinstance(o, float):
                if math.isnan(o):
                    return None
                return o
            return str(o)
        return json.dumps(obj, default=_default, **kwargs)
    def loads(self, s, **kwargs):
        return json.loads(s, **kwargs)



# Initialize Flask app
app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False
import sys
async_mode_chosen = 'eventlet' if 'eventlet' in sys.modules else 'threading'

socketio = SocketIO(
    app,
    async_mode=async_mode_chosen,
    ping_interval=5,
    ping_timeout=10,
    cors_allowed_origins="*"
)
app.json = CustomJSONProvider(app)

# === File Watcher (after SocketIO is ready) ===
try:
    watcher_path = os.getenv('WATCH_PATH', 'watched_dir')
    _nm_instance = NotificationManager()
    start_watcher(path=watcher_path, notifier=_nm_instance, socketio=socketio, recursive=True)
    logger.info(f"📂 Watchdog started on {watcher_path}")
except Exception as e:
    logger.error(f"Failed to start file watcher: {e}")

# Initialize WebSocket immediately after Flask app creation
logger.info(f"🟢 Flask-SocketIO async_mode = {socketio.async_mode}")
# This ensures app.ws is always available for all API endpoints
def initialize_websocket():
    """Initialize WebSocket connection"""
    try:
        logger.info("🔌 Initializing WebSocket connection...")
        ws = CrudeATMWebSocket()
        app.ws = ws
        
        # Start WebSocket in background thread
        def start_websocket():
            try:
                ws.start()
                logger.info("✅ WebSocket started successfully")
            except Exception as e:
                logger.error(f"❌ WebSocket startup error: {e}")
                logger.error(traceback.format_exc())
        
        ws_thread = threading.Thread(target=start_websocket, daemon=True)
        ws_thread.start()
        
        # Wait a moment for connection to establish
        time.sleep(2)
        return ws
    except Exception as e:
        logger.error(f"Failed to initialize WebSocket: {e}")
        logger.error(traceback.format_exc())
        return None

# Initialize WebSocket once at module level
if not hasattr(app, 'ws') or app.ws is None:
    initialize_websocket()

def add_indicators_to_ohlc(df):
    """Add technical indicators to OHLC DataFrame"""
    try:
        if df.empty or len(df) < 5:
            # Not enough data for indicators
            df['fast_ema'] = 0
            df['slow_ema'] = 0
            df['vwap'] = 0
            df['rsi'] = 50
            df['atr'] = 0
            df['macd'] = 0
            df['macd_signal'] = 0
            return df
        
        # Calculate EMAs
        df['fast_ema'] = df['close'].ewm(span=9, adjust=False).mean()
        df['slow_ema'] = df['close'].ewm(span=21, adjust=False).mean()
        # Calculate MACD (difference between EMAs) and its signal line
        df['macd'] = df['fast_ema'] - df['slow_ema']
        df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
        
        # Calculate VWAP
        typical_price = (df['high'] + df['low'] + df['close']) / 3
        volume_price = typical_price * df['volume']
        cumulative_volume = df['volume'].rolling(window=20, min_periods=1).sum()
        cumulative_volume_price = volume_price.rolling(window=20, min_periods=1).sum()
        df['vwap'] = cumulative_volume_price / cumulative_volume
        
        # Calculate RSI
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14, min_periods=1).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14, min_periods=1).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))
        
        # Calculate ATR
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df['atr'] = true_range.rolling(window=14, min_periods=1).mean()
        
        # Fill any NaN values with defaults
        df['fast_ema'] = df['fast_ema'].fillna(df['close'])
        df['slow_ema'] = df['slow_ema'].fillna(df['close'])
        df['vwap'] = df['vwap'].fillna(df['close'])
        df['rsi'] = df['rsi'].fillna(50)
        df['atr'] = df['atr'].fillna(0)
        df['macd'] = df['macd'].fillna(0)
        df['macd_signal'] = df['macd_signal'].fillna(0)
        
        return df
        
    except Exception as e:
        logger.error(f"Error adding indicators to OHLC: {e}")
        # Add default indicator values on error
        df['fast_ema'] = df.get('close', 0)
        df['slow_ema'] = df.get('close', 0)
        df['vwap'] = df.get('close', 0)
        df['rsi'] = 50
        df['atr'] = 0
        df['macd'] = 0
        df['macd_signal'] = 0
        return df

@app.errorhandler(Exception)
def handle_error(error):
    logger.error(f"Error: {str(error)}")
    logger.error(traceback.format_exc())
    response = {
        "error": str(error),
        "type": error.__class__.__name__
    }
    return app.json.response(response), 500

@app.route("/")
def chart():
    return render_template('base.html')

@app.route("/ohlc")
def ohlc():
    if not hasattr(app, 'ws') or not app.ws:
        return jsonify({"error": "WebSocket not initialized"}), 500
    
    interval = request.args.get('interval', '1s')  # Default to 1 second for high-frequency
    limit = int(request.args.get('limit', 100))  # Number of bars to return
    
    # Ensure the requested interval is valid
    if interval not in ["1s", "5s", "10s", "30s", "1min", "5min", "15min", "1h"]:
        return jsonify({"error": f"Invalid interval: {interval}"}), 400

    logger.info(f"Getting OHLC data with interval: {interval}, limit: {limit}")
    
    try:
        # NEW: Use optimized handler for fast OHLC data
        if hasattr(app.ws, 'data_manager') or hasattr(app.ws, 'optimized_handler'):
            result = {'fut': [], 'ce': [], 'pe': []}
            
            # PRIMARY: Try data manager in-memory cache first (fastest)
            if hasattr(app.ws, 'data_manager'):
                logger.info("Using data manager for OHLC data...")
                for contract_type in ['FUT', 'CE', 'PE']:
                    try:
                        # Pass the interval to the data manager
                        ohlc_df = app.ws.data_manager.get_fast_ohlc('CRUDEOIL', contract_type, limit, interval=interval)
                        if not ohlc_df.empty:
                            # Add indicators
                            ohlc_df = add_indicators_to_ohlc(ohlc_df)
                            
                            # Convert to JSON format
                            ohlc_data = []
                            for idx, row in ohlc_df.iterrows():
                                ohlc_data.append({
                                    'timestamp': idx.isoformat() if hasattr(idx, 'isoformat') else str(idx),
                                    'open': float(row['open']) if not pd.isna(row['open']) else 0,
                                    'high': float(row['high']) if not pd.isna(row['high']) else 0,
                                    'low': float(row['low']) if not pd.isna(row['low']) else 0,
                                    'close': float(row['close']) if not pd.isna(row['close']) else 0,
                                    'volume': int(row['volume']) if not pd.isna(row['volume']) else 0,
                                    'oi': int(row['oi']) if not pd.isna(row['oi']) else 0,
                                    'fast_ema': float(row.get('fast_ema', 0)) if not pd.isna(row.get('fast_ema')) else 0,
                                    'slow_ema': float(row.get('slow_ema', 0)) if not pd.isna(row.get('slow_ema')) else 0,
                                    'vwap': float(row.get('vwap', 0)) if not pd.isna(row.get('vwap')) else 0,
                                    'rsi': float(row.get('rsi', 50)) if not pd.isna(row.get('rsi', 50)) else 50,
                                     'atr': float(row.get('atr', 0)) if not pd.isna(row.get('atr')) else 0,
                                     'macd': float(row.get('macd', 0)) if not pd.isna(row.get('macd')) else 0,
                                     'macd_signal': float(row.get('macd_signal', 0)) if not pd.isna(row.get('macd_signal')) else 0
                                })
                            
                            result[contract_type.lower()] = ohlc_data
                            logger.info(f"Data Manager: {contract_type} OHLC has {len(ohlc_data)} data points")
                    except Exception as e:
                        logger.error(f"Error getting {contract_type} data from data manager: {e}")
                        
                # If we got data from data manager, return it
                total_points = sum(len(v) for v in result.values())
                if total_points > 0:
                    logger.info(f"Returning data from Data Manager - Total points: {total_points}")
                    return jsonify(result)
            
            # SECONDARY: Try optimized handler
            if hasattr(app.ws, 'optimized_handler'):
                logger.info("Trying optimized handler for OHLC data...")
                # Pass the interval to the optimized handler
                handler_ohlc = app.ws.optimized_handler.get_ohlc_data(interval=interval)
                
                for symbol, ohlc_data in handler_ohlc.items():
                    if ohlc_data and isinstance(ohlc_data, dict):
                        # Convert handler OHLC dict to DataFrame
                        df_data = []
                        for timestamp, candle in sorted(ohlc_data.items()):
                            df_data.append({
                                'timestamp': pd.to_datetime(timestamp),
                                'open': candle.get('open', 0),
                                'high': candle.get('high', 0),
                                'low': candle.get('low', 0),
                                'close': candle.get('close', 0),
                                'volume': candle.get('volume', 0),
                                'oi': 0
                            })
                        
                        if df_data:
                            ohlc_df = pd.DataFrame(df_data).tail(limit)
                            ohlc_df = ohlc_df.set_index('timestamp')
                            
                            # Add indicators
                            ohlc_df = add_indicators_to_ohlc(ohlc_df)
                            
                            # Convert to JSON format
                            json_ohlc_data = []
                            for idx, row in ohlc_df.iterrows():
                                json_ohlc_data.append({
                                    'timestamp': idx.isoformat() if hasattr(idx, 'isoformat') else str(idx),
                                    'open': float(row['open']) if not pd.isna(row['open']) else 0,
                                    'high': float(row['high']) if not pd.isna(row['high']) else 0,
                                    'low': float(row['low']) if not pd.isna(row['low']) else 0,
                                    'close': float(row['close']) if not pd.isna(row['close']) else 0,
                                    'volume': int(row['volume']) if not pd.isna(row['volume']) else 0,
                                    'oi': int(row['oi']) if not pd.isna(row['oi']) else 0,
                                    'fast_ema': float(row.get('fast_ema', 0)) if not pd.isna(row.get('fast_ema')) else 0,
                                    'slow_ema': float(row.get('slow_ema', 0)) if not pd.isna(row.get('slow_ema')) else 0,
                                    'vwap': float(row.get('vwap', 0)) if not pd.isna(row.get('vwap')) else 0,
                                    'rsi': float(row.get('rsi', 50)) if not pd.isna(row.get('rsi', 50)) else 50,
                                     'atr': float(row.get('atr', 0)) if not pd.isna(row.get('atr')) else 0,
                                     'macd': float(row.get('macd', 0)) if not pd.isna(row.get('macd')) else 0,
                                     'macd_signal': float(row.get('macd_signal', 0)) if not pd.isna(row.get('macd_signal')) else 0
                                })
                            
                            # Map symbol to contract type
                            if '_FUT' in symbol.upper():
                                result['fut'] = json_ohlc_data
                                logger.info(f"Optimized Handler: FUT OHLC has {len(json_ohlc_data)} data points for symbol {symbol}")
                            elif '_CE' in symbol.upper():
                                result['ce'] = json_ohlc_data
                                logger.info(f"Optimized Handler: CE OHLC has {len(json_ohlc_data)} data points for symbol {symbol}")
                            elif '_PE' in symbol.upper():
                                result['pe'] = json_ohlc_data
                                logger.info(f"Optimized Handler: PE OHLC has {len(json_ohlc_data)} data points for symbol {symbol}")
            
                # If we got data from optimized handler, return it
                total_points = sum(len(v) for v in result.values())
                if total_points > 0:
                    logger.info(f"Returning data from Optimized Handler - Total points: {total_points}")
                    return jsonify(result)
            
            # TERTIARY: Try legacy aggregate method as final fallback
            logger.info("Trying legacy aggregate method...")
            fut, ce, pe = app.ws.aggregate_ohlc(interval=interval)
            
            # Add indicators to legacy data
            if not fut.empty:
                fut = add_indicators_to_ohlc(fut)
            if not ce.empty:
                ce = add_indicators_to_ohlc(ce)
            if not pe.empty:
                pe = add_indicators_to_ohlc(pe)
                
            logger.info(f"Legacy OHLC Data sizes - Futures: {len(fut)}, CE: {len(ce)}, PE: {len(pe)}")
            
            # Return legacy data if available
            legacy_result = {
                'fut': fut.to_dict('records') if not fut.empty else [],
                'ce': ce.to_dict('records') if not ce.empty else [],
                'pe': pe.to_dict('records') if not pe.empty else []
            }
            
            total_legacy_points = sum(len(v) for v in legacy_result.values())
            if total_legacy_points > 0:
                logger.info(f"Returning legacy data - Total points: {total_legacy_points}")
                return jsonify(legacy_result)
            
            # If no data found anywhere, return empty result with logging
            logger.warning("No OHLC data found in any source - returning empty result")
            return jsonify(result)
            
    except Exception as e:
        logger.error(f"Error in OHLC: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500

@app.route("/info")
def info():
    if not hasattr(app, 'ws') or not app.ws:
        return jsonify({"error": "WebSocket not initialized"}), 500
    
    try:
        def to_jsonable(d):
            return {k: str(v) for k, v in d.items()}
        account = app.ws.get_account_summary()
        return jsonify({
            'fut': to_jsonable(app.ws.fut_info),
            'ce': to_jsonable(app.ws.ce_info),
            'pe': to_jsonable(app.ws.pe_info),
            'account': account
        })
    except Exception as e:
        logger.error(f"Error in info: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500

@app.route("/strategy_status")
def strategy_status():
    if not hasattr(app, 'ws') or not app.ws:
        return jsonify({"error": "WebSocket not initialized"}), 500
    try:
        status = app.ws.get_strategy_status()
        # Log the full status for debugging
        logger.info(f"Full strategy status response: {json.dumps(status, default=str)}")
        
        # Verify CE indicators
        if status['ce']['indicators']:
            logger.info("CE Indicators present:")
            for k, v in status['ce']['indicators'].items():
                logger.info(f"  {k}: {v}")
        else:
            logger.warning("No CE indicators found")
            
        # Verify PE indicators
        if status['pe']['indicators']:
            logger.info("PE Indicators present:")
            for k, v in status['pe']['indicators'].items():
                logger.info(f"  {k}: {v}")
        else:
            logger.warning("No PE indicators found")
            
        return jsonify(status)
    except Exception as e:
        logger.error(f"Error in strategy_status: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500

@app.route("/strategy_params", methods=["GET", "POST"])
def strategy_params():
    if not hasattr(app, 'ws') or not app.ws:
        return jsonify({"error": "WebSocket not initialized"}), 500
    if request.method == "POST":
        params = request.json
        logger.info(f"Received strategy parameter update: {params}")
        
        # Use the proper update_parameters method for both strategies
        for strat in [app.ws.strategy_ce, app.ws.strategy_pe]:
            strat.update_parameters(params)
        
        logger.info("Strategy parameters updated successfully")
        return jsonify({"status": "ok"})
    else:
        # Return the base parameters that the user has set, not the dynamically adjusted ones
        strat = app.ws.strategy_ce
        return jsonify({
            "fast_ema_period": getattr(strat, 'fast_ema_period_base', 2),
            "slow_ema_period": getattr(strat, 'slow_ema_period_base', 3),
            "rsi_period": getattr(strat, 'rsi_period', 9),
            "atr_period": getattr(strat, 'atr_period', 9),
            "vwap_period": getattr(strat, 'vwap_period', 10),
            "use_fast_ema": getattr(strat, 'use_ema', True),
            "use_slow_ema": getattr(strat, 'use_ema', True),
            "use_rsi": getattr(strat, 'use_rsi', True),
            "use_atr": getattr(strat, 'use_atr', True),
            "use_vwap": getattr(strat, 'use_vwap', True)
        })

@app.route("/test_telegram", methods=["POST"])
def test_telegram():
    try:
        data = request.json
        notifier = NotificationManager()
        notifier.telegram_token = data.get('token')
        notifier.telegram_chat_id = data.get('chat_id')
        result = notifier.test_notifications()
        return jsonify({"success": result['telegram']})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/test_email", methods=["POST"])
def test_email():
    try:
        data = request.json
        notifier = NotificationManager()
        notifier.email_user = data.get('email')
        notifier.email_pass = data.get('password')
        result = notifier.test_notifications()
        return jsonify({"success": result['email']})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/save_notification_settings", methods=["POST"])
def save_notification_settings():
    try:
        data = request.json
        notifier = NotificationManager()
        notifier.update_settings(data)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/get_notification_settings")
def get_notification_settings():
    try:
        return jsonify(notification_settings)
    except Exception as e:
        logger.error(f"Error in get_notification_settings: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500

@app.route('/dashboard')
def dashboard():
    """Render main dashboard template"""
    return render_template('dashboard.html')


@app.route('/')
def index():
    """Redirect root to dashboard for convenience"""
    return redirect(url_for('dashboard'))


@app.route('/backtest')
def backtest():
    """Render the backtest UI template"""
    return render_template('backtest.html')

# ==========================================================
#           REST API ENDPOINTS FOR BACKTEST WEB UI
# ==========================================================

from pathlib import Path
import pandas as pd
import os

BACKTEST_DIR = Path(__file__).parent / 'backtest'


def _load_historical_data():
    """Utility to load historical CE & PE CSVs if present"""
    ce_path = BACKTEST_DIR / 'historical_data_ce.csv'
    pe_path = BACKTEST_DIR / 'historical_data_pe.csv'
    if not (ce_path.exists() and pe_path.exists()):
        return None, None
    ce_df = pd.read_csv(ce_path, parse_dates=['timestamp'])
    pe_df = pd.read_csv(pe_path, parse_dates=['timestamp'])
    return ce_df, pe_df


@app.route('/api/data_status')
def api_data_status():
    """Return basic information on historic data availability for the UI."""
    try:
        ce_df, pe_df = _load_historical_data()
        if ce_df is None:
            return jsonify({
                'available_dates': [],
                'total_files': 0,
                'is_ready': False
            })
        # Intersection of available dates in both CE & PE data
        ce_dates = ce_df['timestamp'].dt.date.unique()
        pe_dates = pe_df['timestamp'].dt.date.unique()
        common_dates = sorted(list(set(ce_dates) & set(pe_dates)))
        return jsonify({
            'available_dates': [d.isoformat() for d in common_dates],
            'total_files': len(common_dates),
            'is_ready': len(common_dates) > 0
        })
    except Exception as e:
        logger.error(f"api_data_status: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/prepare_historical_data', methods=['POST'])
def api_prepare_historical_data():
    """Placeholder that signals the FE that historical data is prepared.
    In a full implementation this would trigger aggregation scripts.
    """
    try:
        # For now we simply report success if files exist
        ce_path = BACKTEST_DIR / 'historical_data_ce.csv'
        pe_path = BACKTEST_DIR / 'historical_data_pe.csv'
        success = ce_path.exists() and pe_path.exists()
        return jsonify({'success': success, 'error': None if success else 'Historical data files missing'})
    except Exception as e:
        logger.error(f"api_prepare_historical_data: {e}")
        return jsonify({'success': False, 'error': str(e)})


from threading import Thread, Event
import uuid, time

# In-memory task registry
collection_tasks: dict[str, dict] = {}

def _run_pipeline(days: int, filter_type: str, task_id: str, stop_event: Event):
    """Background worker that fakes progress to 100%."""
    import subprocess, sys, queue
    from pathlib import Path
    cmd = [sys.executable, str(Path(__file__).parent / 'backtest' / 'run_pipeline.py')]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    q = queue.Queue()
    def _reader():
        for line in proc.stdout:
            q.put(line.rstrip('\n'))
        proc.stdout.close()
    Thread(target=_reader, daemon=True).start()
    progress = 0
    while proc.poll() is None:
        try:
            line = q.get(timeout=0.5)
            collection_tasks[task_id]['log'].append(line)
            if 'Completed' in line or '✅' in line:
                progress += 30  # rough increment
            collection_tasks[task_id]['progress'] = min(progress, 95)
        except queue.Empty:
            pass
        if stop_event.is_set():
            proc.terminate()
            proc.wait(5)
            collection_tasks[task_id]['status'] = 'stopped'
            return
    # process ended
    collection_tasks[task_id]['progress'] = 100
    collection_tasks[task_id]['status'] = 'completed'
    collection_tasks[task_id]['log'].append('Pipeline finished')

@app.route('/api/start_data_collection', methods=['POST'])
def api_start_data_collection():
    """Start data collection in a background thread and return a task_id."""
    try:
        data = request.get_json() or {}
        days = int(data.get('days', 5))
        filter_type = data.get('filter', 'atm')

        task_id = f"collection_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        stop_event = Event()
        collection_tasks[task_id] = {
            'progress': 0,
            'status': 'running',
            'log': [f'Started collection for {days} days, filter {filter_type}'],
            'stop_event': stop_event
        }
        Thread(target=_run_pipeline, args=(days, filter_type, task_id, stop_event), daemon=True).start()
        app.logger.info(f"Started data collection task {task_id}")
        return jsonify({'success': True, 'task_id': task_id})
    except Exception as e:
        app.logger.error(f"start_data_collection error: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/collection_status/<task_id>', methods=['GET'])
def api_collection_status(task_id):
    task = collection_tasks.get(task_id)
    if not task:
        return jsonify({'error': 'invalid task id'}), 404
    return jsonify({
        'progress': task['progress'],
        'status': task['status'],
        'log': task['log'][-50:]  # last 50 lines
    })

@app.route('/api/stop_data_collection', methods=['POST'])
def api_stop_data_collection():
    data = request.get_json() or {}
    task_id = data.get('task_id')
    task = collection_tasks.get(task_id)
    if not task:
        return jsonify({'success': False, 'error': 'invalid task id'}), 404
    task['stop_event'].set()
    return jsonify({'success': True})

@app.route('/api/backtest', methods=['POST'])
def api_backtest():
    """Run a backtest using StrategyBacktester and return JSON results."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        # Mandatory fields validation
        for fld in ['start_date', 'end_date', 'initial_capital', 'strategy_params']:
            if fld not in data:
                return jsonify({'error': f'Missing field {fld}'}), 400

        # Parse dates from FE (YYYY-MM-DD)
        start_date = pd.to_datetime(data['start_date'])
        end_date = pd.to_datetime(data['end_date']) + pd.Timedelta(days=1)  # inclusive
        initial_capital = float(data['initial_capital'])
        strategy_params = data['strategy_params']

        # Load historical data
        ce_df, pe_df = _load_historical_data()
        if ce_df is None:
            return jsonify({'error': 'Historical data not found'}), 500

        # Filter date range
        ce_df = ce_df[(ce_df['timestamp'] >= start_date) & (ce_df['timestamp'] <= end_date)].reset_index(drop=True)
        pe_df = pe_df[(pe_df['timestamp'] >= start_date) & (pe_df['timestamp'] <= end_date)].reset_index(drop=True)
        if ce_df.empty or pe_df.empty:
            return jsonify({'error': 'No data in selected range'}), 400

        # Run backtest
        from backtest.backtest import StrategyBacktester  # local import to avoid heavy cost at startup
        backtester = StrategyBacktester(strategy_params=strategy_params)
        results = backtester.backtest(ce_df, pe_df, initial_capital=initial_capital)

        # Build JSON for FE – convert numpy/pandas types to Python scalars
        def series_to_list(s):
            return [float(x) if pd.notna(x) else None for x in s]

        payload = {
            'dates': [d.isoformat() for d in results['combined']['equity_curve'].index],
            'combined': {
                'equity_curve': series_to_list(results['combined']['equity_curve']),
                'total_return': float(results['combined']['total_return']),
                'max_drawdown': float(results['combined']['max_drawdown'])
            },
            'ce': {
                'equity_curve': series_to_list(results['ce']['equity_curve']),
                'total_return': float(results['ce']['total_return']),
                'trades': results['ce']['trades']
            },
            'pe': {
                'equity_curve': series_to_list(results['pe']['equity_curve']),
                'total_return': float(results['pe']['total_return']),
                'trades': results['pe']['trades']
            }
        }
        return jsonify(payload)
    except Exception as e:
        logger.error(f"api_backtest: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

# ---------------------------------------------------------------------------
# Trade endpoint consumed by UI Trade tab
# ---------------------------------------------------------------------------
@app.route('/api/trade', methods=['POST'])
def api_trade():
    """Place MARKET/LIMIT order via Broker wrapper and return order_id or error."""
    data = request.get_json(force=True) or {}

    required = {'symbol_token', 'trading_symbol', 'side', 'qty'}
    missing = required - data.keys()
    if missing:
        return jsonify({'error': f"Missing fields: {', '.join(missing)}"}), 400

    try:
        qty = int(data['qty'])
    except (ValueError, TypeError):
        return jsonify({'error': 'qty must be integer'}), 400

    side = str(data['side']).upper()
    if side not in {'BUY', 'SELL'}:
        return jsonify({'error': 'side must be BUY or SELL'}), 400

    # obtain broker instance
    broker = getattr(app, 'broker', None)
    if broker is None:
        return jsonify({'error': 'broker not initialised'}), 500

    order_resp = broker.place_order(
        symbol_token=str(data['symbol_token']),
        trading_symbol=str(data['trading_symbol']),
        side=side,
        qty=qty,
        order_type=data.get('order_type', 'MARKET'),
        price=data.get('price'),
    )
    status = 200 if 'error' not in order_resp else 500
    return jsonify(order_resp), status

@app.route('/run_backtest', methods=['POST'])
def run_backtest():
    try:
        data = request.get_json()
        
        # Validate input data
        if not data:
            return jsonify({'error': 'No data provided'}), 400
            
        required_fields = ['start_date', 'end_date', 'initial_capital', 'strategy_params']
        for field in required_fields:
            if field not in data:
                return jsonify({'error': f'Missing required field: {field}'}), 400
        
        # Parse dates
        try:
            # Try parsing as date first, then as datetime
            try:
                start_date = datetime.strptime(data['start_date'], '%Y-%m-%d').replace(tzinfo=pytz.UTC)
                end_date = datetime.strptime(data['end_date'], '%Y-%m-%d').replace(hour=23, minute=59, second=59, tzinfo=pytz.UTC)
            except ValueError:
                start_date = datetime.strptime(data['start_date'], '%Y-%m-%d %H:%M').replace(tzinfo=pytz.UTC)
                end_date = datetime.strptime(data['end_date'], '%Y-%m-%d %H:%M').replace(tzinfo=pytz.UTC)
        except ValueError as e:
            return jsonify({'error': f'Invalid date format: {str(e)}'}), 400
            
        initial_capital = float(data['initial_capital'])
        strategy_params = data['strategy_params']
        
        # Convert frontend parameter format to backend format
        processed_params = {}
        for param_name, param_data in strategy_params.items():
            if isinstance(param_data, dict) and 'value' in param_data:
                # Frontend format: {'value': X, 'enabled': True}
                processed_params[param_name] = param_data['value']
                processed_params[f"use_{param_name.replace('_period', '')}"] = param_data.get('enabled', True)
            else:
                # Direct value format
                processed_params[param_name] = param_data
        
        # Add missing strategy parameters with defaults
        defaults = {
            'rsi_oversold': 35,
            'rsi_overbought': 65,
            'volume_surge_factor': 1.1,
            'atr_volatility_factor': 0.01
        }
        
        for key, default_value in defaults.items():
            if key not in processed_params:
                processed_params[key] = default_value
        
        # Attempt to load data from PostgreSQL first (sync pool) for higher performance
        try:
            from backtest.db_pg_sync import fetch_ohlcv_range
            ce_data, pe_data = fetch_ohlcv_range(start_date, end_date)
        except Exception as db_err:
            app.logger.warning(f"Postgres fetch failed, falling back to CSV: {db_err}")
            ce_data = pd.DataFrame()
            pe_data = pd.DataFrame()

        # Fallback to CSV if DB unavailable or returned empty
        if ce_data.empty or pe_data.empty:
            ce_file = 'backtest/historical_data_ce.csv'
            pe_file = 'backtest/historical_data_pe.csv'
            if not (os.path.exists(ce_file) and os.path.exists(pe_file)):
                return jsonify({'error': 'Historical CSVs not found and DB unavailable'}), 404
            ce_data = pd.read_csv(ce_file)
            pe_data = pd.read_csv(pe_file)
        
        # Parse timestamps with timezone support
        ce_data['timestamp'] = pd.to_datetime(ce_data['timestamp'], utc=True)
        pe_data['timestamp'] = pd.to_datetime(pe_data['timestamp'], utc=True)
        
        # Filter by date range
        ce_data = ce_data[(ce_data['timestamp'] >= start_date) & (ce_data['timestamp'] <= end_date)]
        pe_data = pe_data[(pe_data['timestamp'] >= start_date) & (pe_data['timestamp'] <= end_date)]

        # Remove duplicate timestamps to prevent reindex errors
        ce_data = ce_data.drop_duplicates(subset='timestamp', keep='first')
        pe_data = pe_data.drop_duplicates(subset='timestamp', keep='first')

        # Use timestamp as index to maintain correct equity curve dates
        ce_data.set_index('timestamp', inplace=True)
        pe_data.set_index('timestamp', inplace=True)

        # Ensure index uniqueness
        ce_data = ce_data[~ce_data.index.duplicated(keep='first')]
        pe_data = pe_data[~pe_data.index.duplicated(keep='first')]
        
        if ce_data.empty:
            return jsonify({'error': 'No CE data found for the specified date range'}), 400
        if pe_data.empty:
            return jsonify({'error': 'No PE data found for the specified date range'}), 400
        
        # Import and run backtest
        from backtest.backtest import StrategyBacktester
        
        # Debug: Log the processed parameters
        app.logger.info(f"Processed strategy parameters: {processed_params}")
        for key, value in processed_params.items():
            app.logger.info(f"  {key}: {value} (type: {type(value)})")
        
        backtester = StrategyBacktester(processed_params)
        results = backtester.backtest(ce_data, pe_data, initial_capital)
        # Down-sample equity curve to avoid megabyte-sized JSON payloads
        combined_eq = results['combined']['equity_curve']
        max_points = 2000
        step = 1  # default when no down-sampling
        if len(combined_eq) > max_points:
            step = len(combined_eq) // max_points
            # Ensure index is proper datetime to avoid 1970 epoch issues
        combined_eq.index = pd.to_datetime(combined_eq.index, utc=True, errors='coerce')
        combined_eq = combined_eq.iloc[::step]

        def _fmt(ts):
            try:
                return pd.to_datetime(ts).strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
                return str(ts)

        idx = combined_eq.index
        dates_list = [_fmt(ts) for ts in idx]

        formatted_results = {
            'dates': dates_list,
            'combined': {
                'equity_curve': combined_eq.tolist(),
                'total_return': results['combined']['total_return'],
                'max_drawdown': results['combined']['max_drawdown'],
                 'profit_factor': results['combined']['profit_factor'],
                 'sortino_ratio': results['combined']['sortino_ratio'],
                 'calmar_ratio': results['combined']['calmar_ratio'],
                 'volatility': results['combined']['volatility'],
                 'var_95': results['combined']['var_95'],
                 'max_consecutive_losses': results['combined']['max_consecutive_losses'],
                 'recovery_factor': results['combined']['recovery_factor'],
                'sharpe_ratio': results['combined']['sharpe_ratio'],
                'gross_profit': results['combined']['gross_profit'],
                'gross_loss': results['combined']['gross_loss'],
                'total_costs': results['combined']['total_costs'],
                'net_profit': results['combined']['net_profit'],
                'win_rate': results['combined']['win_rate']
            },
            'ce': {
                # CE equity curve also trimmed the same way for consistency
                'equity_curve': results['ce']['equity_curve'].iloc[::step].tolist() if len(results['ce']['equity_curve']) > max_points else results['ce']['equity_curve'].tolist(),
                'trades': [{
                    'entry_time': _fmt(t['entry_time']),
                    'exit_time': _fmt(t['exit_time']),
                    'type': 'CE ' + t['type'],
                    'entry_price': t['entry_price'],
                    'exit_price': t['exit_price'],
                    'pnl': t['pnl'],
                    'return': t['return']
                } for t in results['ce']['trades']]
            },
            'pe': {
                'equity_curve': results['pe']['equity_curve'].iloc[::step].tolist() if len(results['pe']['equity_curve']) > max_points else results['pe']['equity_curve'].tolist(),
                'trades': [{
                    'entry_time': _fmt(t['entry_time']),
                    'exit_time': _fmt(t['exit_time']),
                    'type': 'PE ' + t['type'],
                    'entry_price': t['entry_price'],
                    'exit_price': t['exit_price'],
                    'pnl': t['pnl'],
                    'return': t['return']
                } for t in results['pe']['trades']]
            }
        }
        return jsonify(formatted_results)
    except Exception as e:
        app.logger.error(f"Backtest error: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/data_status_files')
def api_data_status_files():
    """Get status of available data for backtesting"""
    try:
        data_dir = Path('backtest/data')
        
        # Get all data
        data_files = list(data_dir.glob('*.csv')) if data_dir.exists() else []
        
        # Extract unique dates from file names
        available_dates = set()
        for file in data_files:
            # Extract date from filename (format: SYMBOL_YYYYMMDD.csv)
            try:
                date_str = file.stem.split('_')[-1]
                if len(date_str) == 8 and date_str.isdigit():
                    date_obj = datetime.strptime(date_str, '%Y%m%d')
                    available_dates.add(date_obj.strftime('%Y-%m-%d'))
            except:
                continue
        
        # Check if historical data files exist
        ce_file = Path('backtest/historical_data_ce.csv')
        pe_file = Path('backtest/historical_data_pe.csv')
        
        is_ready = (
            len(available_dates) >= 3 and  # At least 3 days of data
            ce_file.exists() and
            pe_file.exists()
        )
        
        return jsonify({
            'success': True,
            'available_dates': sorted(list(available_dates)),
            'total_files': len(data_files),
            'is_ready': is_ready,
            'ce_file_exists': ce_file.exists(),
            'pe_file_exists': pe_file.exists()
        })
        
    except Exception as e:
        app.logger.error(f"Error in api_data_status: {str(e)}")
        return jsonify({'error': str(e)}), 500





@app.route('/api/strategy_params_schema')
def api_strategy_params_schema():
    """Return JSON schema + current values for strategy parameters."""
    schema = []
    for key, val in CURRENT_STRATEGY_PARAMS.items():
        schema.append({
            'name': key,
            'type': type(val).__name__,
            'default': val,
            'current': val
        })
    return jsonify({'params': schema})


@app.route('/api/update_strategy_params', methods=['POST'])
def api_update_strategy_params():
    """Update in-memory strategy parameters and persist to file."""
    try:
        new_params = request.get_json(force=True)
        if not isinstance(new_params, dict):
            return jsonify({'error': 'Invalid JSON body'}), 400
        CURRENT_STRATEGY_PARAMS.update(new_params)
        PARAMS_FILE.parent.mkdir(parents=True, exist_ok=True)
        PARAMS_FILE.write_text(json.dumps(CURRENT_STRATEGY_PARAMS, indent=2))
        # Broadcast via SocketIO if available
        try:
            socketio = SocketIO(message_queue=None)
            socketio.emit('strategy_params_updated', CURRENT_STRATEGY_PARAMS, broadcast=True)
        except Exception:
            pass
        return jsonify({'success': True, 'updated': CURRENT_STRATEGY_PARAMS})
    except Exception as e:
        app.logger.error(f"Error updating params: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/prepare_historical_data_run', methods=['POST'])
def api_prepare_historical_data_run():
    """Prepare historical data from collected raw data"""
    try:
        # Run the data preparation script
        import subprocess
        import sys
        
        script_path = Path('backtest/prepare_historical_data.py')
        if not script_path.exists():
            return jsonify({'error': 'Data preparation script not found'}), 404
        
        # Run the preparation script
        result = subprocess.run([
            sys.executable, str(script_path)
        ], capture_output=True, text=True, cwd=str(script_path.parent))
        
        if result.returncode == 0:
            return jsonify({
                'success': True,
                'message': 'Historical data prepared successfully',
                'output': result.stdout
            })
        else:
            return jsonify({
                'error': f'Data preparation failed: {result.stderr}',
                'output': result.stdout
            }), 500
            
    except Exception as e:
        app.logger.error(f"Error in api_prepare_historical_data: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/collection_progress/<task_id>')
def api_collection_progress(task_id):
    """Get progress of data collection task"""
    try:
        # Check if there are log files to read
        log_dir = Path('backtest/logs')
        if log_dir.exists():
            log_files = list(log_dir.glob('crude_data_*.log'))
            if log_files:
                # Read the most recent log file
                latest_log = max(log_files, key=lambda x: x.stat().st_mtime)
                try:
                    with open(latest_log, 'r') as f:
                        lines = f.readlines()
                        recent_logs = lines[-20:]  # Get last 20 lines
                        
                    # Parse logs for progress info
                    logs = []
                    for line in recent_logs:
                        if line.strip():
                            # Extract timestamp and message
                            parts = line.strip().split(' - ', 2)
                            if len(parts) >= 3:
                                timestamp = parts[0]
                                level = parts[1]
                                message = parts[2]
                                logs.append({
                                    'timestamp': timestamp,
                                    'level': level,
                                    'message': message
                                })
                    
                    # Simulate completion based on log content
                    completed = any('Final Data Collection Summary' in log['message'] for log in logs)
                    percentage = 100 if completed else 50
                    
                    return jsonify({
                        'task_id': task_id,
                        'percentage': percentage,
                        'completed': completed,
                        'logs': logs
                    })
                except Exception as e:
                    app.logger.warning(f"Error reading log file: {str(e)}")
        
        # Default response if no logs available
        return jsonify({
            'task_id': task_id,
            'percentage': 0,
            'completed': False,
            'logs': []
        })
        
    except Exception as e:
        app.logger.error(f"Error in api_collection_progress: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/favicon.ico')
def favicon():
    return '', 204

@app.route('/available_intervals')
def available_intervals():
    # Return the intervals that are pre-calculated by the optimized handler
    if hasattr(app, 'ws') and hasattr(app.ws, 'supported_intervals'):
        return jsonify(app.ws.supported_intervals)
    return jsonify(["1s", "5s", "10s", "30s", "1min", "5min", "15min", "1h"])

@app.route('/test_backtest_params', methods=['POST'])
def test_backtest_params():
    """Test endpoint to debug parameter processing"""
    try:
        data = request.get_json()
        
        # Process strategy parameters (same logic as run_backtest)
        strategy_params = data.get('strategy_params', {})
        processed_params = {}
        
        # Extract values from the UI format {value: X, enabled: Y}
        for key, param in strategy_params.items():
            if isinstance(param, dict) and 'value' in param:
                processed_params[key] = param['value']
                processed_params[f"use_{key.replace('_period', '')}"] = param.get('enabled', True)
            else:
                processed_params[key] = param
        
        # Add missing default parameters
        processed_params.update({
            'rsi_oversold': 30,
            'rsi_overbought': 70,
            'volume_surge_factor': 2.0,
            'atr_volatility_factor': 1.5
        })
        
        return jsonify({
            'success': True,
            'original_params': strategy_params,
            'processed_params': processed_params,
            'param_types': {k: str(type(v)) for k, v in processed_params.items()}
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/dry-run', methods=['GET', 'POST'])

def api_dry_run():
    """Get or update dry-run mode."""
    global DRY_RUN
    if request.method == 'GET':
        return jsonify({'dry_run': DRY_RUN})

    data = request.get_json(silent=True) or {}
    new_state = bool(data.get('dry_run', True))
    DRY_RUN = new_state

    # persist to .env
    try:
        set_key(str(ENV_PATH), 'DRY_RUN', 'true' if DRY_RUN else 'false')
    except Exception as e:
        print(f'[ENV WRITE ERROR] {e}')

    # broadcast to clients
    if socketio:
        socketio.emit('dry_run_change', {'dry_run': DRY_RUN})

    return jsonify({'dry_run': DRY_RUN})


@app.route('/performance')
def performance():
    """Get system performance statistics"""
    if not hasattr(app, 'ws') or not app.ws:
        return jsonify({"error": "WebSocket not initialized"}), 500
    
    try:
        stats = {}
        
        # Get optimized handler stats
        if hasattr(app.ws, 'optimized_handler') and hasattr(app.ws.optimized_handler, 'get_performance_stats'):
            handler_stats = app.ws.optimized_handler.get_performance_stats()
            stats['ticks_processed'] = handler_stats.get('ticks_processed', 0)
            # You can add other handler-specific stats here if needed
            # For example: stats['tick_queue_size'] = handler_stats.get('tick_queue_size', 0)

        # Add system stats if psutil is available
        if PSUTIL_AVAILABLE:
            stats.update({
                'system': {
                    'cpu_percent': psutil.cpu_percent(interval=0.1),
                    'memory_percent': psutil.virtual_memory().percent,
                    'memory_used_mb': psutil.virtual_memory().used / 1024 / 1024,
                    'process_memory_mb': psutil.Process().memory_info().rss / 1024 / 1024
                }
            })
        else:
            stats['system'] = {'note': 'psutil not available - install for system metrics'}
        
        # Add tick buffer stats
        with app.ws.lock:
            stats['legacy_tick_buffer_size'] = len(app.ws.tick_buffer)
        
        # Add database availability
        stats['questdb_available'] = hasattr(app.ws.data_manager, 'questdb') and app.ws.data_manager.questdb.running
        
        # Add a placeholder for avg_latency_ms if it's not available from the handler
        if 'avg_latency_ms' not in stats:
            stats['avg_latency_ms'] = None

        return jsonify(stats)
        
    except Exception as e:
        logger.error(f"Error getting performance stats: {str(e)}")
        return jsonify({"error": str(e)}), 500

# Root and /dashboard routes are defined earlier in the file.
# Removed duplicate definitions that caused endpoint conflict.

# Option Chain page
@app.route('/option_chain')
def option_chain():
    """Option Chain page"""
    return render_template('option_chain.html')

# API: Option Chain data
@app.route('/api/option_chain')
def api_option_chain():
    """Return latest CRUDEOIL option chain snapshot for UI"""
    try:
        import redis, json, os
        r = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)
        cached = r.get("crudeoil:option_chain")
        if not cached:
            return jsonify({"error": "data_not_ready"}), 503
        return jsonify(json.loads(cached))
    except Exception as e:
        app.logger.error(f"api_option_chain: {e}")
        return jsonify({'error': str(e)}), 500

# Trade page
@app.route("/trade")
def trade():
    """Trade page"""
    return render_template('trade.html')

# Strategy page
@app.route("/strategy")
def strategy():
    """Strategy page"""
    return render_template('strategy.html')

# Settings page
@app.route("/settings")
def settings():
    """Settings page"""
    return render_template('settings.html')

@app.route("/performance_dashboard")
def performance_dashboard():
    """Performance monitoring dashboard"""
    return render_template('performance.html')

if __name__ == "__main__":
    logger.info("=== Starting Crude Oil ATM WebSocket with OHLC & Strategy Signal ===")
    try:
        # WebSocket is already initialized at module level
        # Just ensure it's running if not already started
        if hasattr(app, 'ws') and app.ws:
            logger.info("✅ WebSocket already initialized and running")
        else:
            logger.warning("⚠️ WebSocket not found, initializing now...")
            initialize_websocket()
        
        # Run Flask app with SocketIO for proper WebSocket support
        logger.info("Starting Flask-SocketIO server...")
        print("🚀 Starting Crude Oil Trading Bot...")
        print("📊 Dashboard: http://127.0.0.1:5000")
        print("🔬 Backtest UI: http://127.0.0.1:5000/backtest")
        print("⭐ Press Ctrl+C to stop")
        print("-" * 50)
        
        socketio.run(app, host="0.0.0.0", port=5000, debug=True, use_reloader=True, allow_unsafe_werkzeug=True)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        print("\n🛑 Server stopped by user")
        if hasattr(app, 'ws') and app.ws:
            app.ws.close()
    except Exception as e:
        logger.error(f"Error in main: {str(e)}")
        logger.error(traceback.format_exc())
        print(f"❌ Error: {e}")
        if hasattr(app, 'ws') and app.ws:
            app.ws.close()
        raise

# --- END FLASK APP AND ROUTES ---
