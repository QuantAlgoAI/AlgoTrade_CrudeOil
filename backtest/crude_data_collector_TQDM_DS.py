import signal
import sys
import os
import json
import time
import logging
import random
import requests
import uuid
import socket
import http.client
import pyotp
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv
from SmartApi import SmartConnect
from tqdm import tqdm
from typing import Optional, Tuple, List, Dict
from concurrent.futures import ThreadPoolExecutor, as_completed

# === Constants ===
MAX_RETRIES = 3
BASE_RETRY_DELAY = 5
MAX_THREADS = 5  # Conservative to avoid rate limiting
REQUEST_TIMEOUT = 30
API_BASE_URL = "apiconnect.angelone.in"

# === Setup Graceful Exit ===
class GracefulExit(Exception):
    """Custom exception for graceful shutdown"""
    pass

def signal_handler(sig, frame):
    """Handle interrupt signals"""
    raise GracefulExit()

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# === Load .env ===
def load_environment() -> bool:
    """Load environment variables and validate required ones"""
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        logging.error(f"Environment file not found at {env_path}")
        return False
    
    load_dotenv(env_path)
    required_vars = ["ANGEL_ONE_API_KEY", "ANGEL_ONE_CLIENT_CODE", 
                    "ANGEL_ONE_PASSWORD", "ANGEL_ONE_TOTP_SECRET"]
    
    missing = [var for var in required_vars if not os.getenv(var)]
    if missing:
        logging.error(f"Missing required environment variables: {missing}")
        return False
    
    return True

if not load_environment():
    sys.exit(1)

# === Logging Setup ===
def setup_logging() -> None:
    """Configure logging with file and console handlers"""
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    
    # File handler (debug level)
    file_handler = logging.FileHandler(
        log_dir / f"crude_data_{datetime.now().strftime('%Y%m%d')}.log"
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    
    # Console handler (info level)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)
    
    # Configure root logger
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

setup_logging()
logger = logging.getLogger(__name__)

# === Collector Class ===
class CrudeDataCollector:
    def __init__(self):
        """Initialize data collector with proper error tracking and configuration"""
        self.api = None
        self.session_valid = False
        self.retry_count = MAX_RETRIES
        self.retry_delay = BASE_RETRY_DELAY
        self.data_dir = Path(__file__).parent / 'data'
        self.data_dir.mkdir(exist_ok=True)
        self.failed_dir = self.data_dir / 'failed'
        self.failed_dir.mkdir(exist_ok=True)
        
        # Network information
        self.local_ip = self._get_local_ip()
        self.public_ip = self._get_public_ip()
        self.mac_address = self._get_mac_address()
        
        self.headers = None
        self.error_counts = {
            'login': 0, 
            'price_data': 0, 
            'oi_data': 0, 
            'network': 0,
            'other': 0
        }
        self.session_expiry = None

    def _get_local_ip(self) -> str:
        """Get local IP address with fallback"""
        try:
            return socket.gethostbyname(socket.gethostname())
        except:
            return "127.0.0.1"

    def _get_public_ip(self) -> str:
        """Get public IP address with multiple fallbacks"""
        services = [
            "https://api.ipify.org",
            "https://ident.me",
            "https://ifconfig.me/ip"
        ]
        
        for service in services:
            try:
                response = requests.get(service, timeout=5)
                if response.status_code == 200:
                    return response.text.strip()
            except:
                continue
                
        return self.local_ip

    def _get_mac_address(self) -> str:
        """Get MAC address with UUID fallback"""
        try:
            mac_num = hex(uuid.getnode()).replace('0x', '').upper()
            mac = '-'.join(mac_num[i:i+2] for i in range(0, 11, 2))
            return mac if len(mac) == 17 else str(uuid.uuid4())
        except:
            return str(uuid.uuid4())

    def _is_session_valid(self) -> bool:
        """Check if session is still valid"""
        if not self.session_valid:
            return False
        if self.session_expiry and datetime.now() >= self.session_expiry:
            logger.warning("Session expired")
            return False
        return True

    def login(self) -> bool:
        """Authenticate with Angel One API with retry logic"""
        for attempt in range(self.retry_count):
            try:
                logger.info(f"Login attempt {attempt + 1}/{self.retry_count}")
                
                # Initialize new API connection
                self.api = SmartConnect(api_key=os.getenv("ANGEL_ONE_API_KEY"))
                
                # Generate session with TOTP
                totp = pyotp.TOTP(os.getenv("ANGEL_ONE_TOTP_SECRET")).now()
                session = self.api.generateSession(
                    os.getenv("ANGEL_ONE_CLIENT_CODE"),
                    os.getenv("ANGEL_ONE_PASSWORD"),
                    totp
                )
                
                if 'data' not in session:
                    logger.warning(f"Login failed: {session.get('message', 'Unknown error')}")
                    continue
                    
                self.session_valid = True
                self.session_expiry = datetime.now() + timedelta(hours=1)  # Assume 1hr session
                
                # Prepare headers for future requests
                self.headers = {
                    'Authorization': session['data']['jwtToken'],
                    'X-PrivateKey': os.getenv("ANGEL_ONE_API_KEY"),
                    'X-ClientCode': os.getenv("ANGEL_ONE_CLIENT_CODE"),
                    'X-FeedToken': session['data']['feedToken'],
                    'X-SourceID': 'WEB',
                    'X-ClientLocalIP': self.local_ip,
                    'X-ClientPublicIP': self.public_ip,
                    'X-MACAddress': self.mac_address,
                    'X-UserType': 'USER',
                    'Content-Type': 'application/json',
                    'Accept': 'application/json'
                }
                
                logger.info("Login successful")
                return True
                
            except requests.exceptions.RequestException as e:
                self.error_counts['network'] += 1
                logger.warning(f"Network error during login: {str(e)}")
            except Exception as e:
                self.error_counts['login'] += 1
                logger.error(f"Unexpected login error: {str(e)}", exc_info=True)
            
            # Exponential backoff with jitter
            wait_time = min(self.retry_delay * (2 ** attempt) + random.uniform(0, 1), 30)
            logger.info(f"Waiting {wait_time:.2f} seconds before retry...")
            time.sleep(wait_time)
            
        logger.error("Maximum login attempts reached")
        return False

    def _get_latest_instrument_file(self) -> Optional[Path]:
        """Find the most recent instrument file with fallback logic"""
        instruments_dir = Path(__file__).parent.parent / "instruments"
        
        # Try current date first
        current_date_file = instruments_dir / f"{datetime.now().strftime('%Y%m%d')}_instrument_file.csv"
        if current_date_file.exists():
            return current_date_file
            
        # Fallback to most recent file
        try:
            files = list(instruments_dir.glob("*_instrument_file.csv"))
            if not files:
                logger.error("No instrument files found")
                return None
                
            latest_file = max(files, key=lambda f: f.stat().st_mtime)
            logger.warning(f"Using fallback instrument file: {latest_file.name}")
            return latest_file
        except Exception as e:
            logger.error(f"Error finding instrument file: {str(e)}")
            return None

    def get_crude_options(self) -> Optional[pd.DataFrame]:
        """Load and filter CRUDEOIL options from instrument file"""
        instrument_file = self._get_latest_instrument_file()
        if not instrument_file:
            return None
            
        try:
            # Read with explicit dtype for token to avoid mixed types
            df = pd.read_csv(instrument_file, dtype={'token': str})
            
            # Filter CRUDEOIL options
            crude_df = df[
                (df['name'] == 'CRUDEOIL') & 
                (df['instrumenttype'] == 'OPTFUT') & 
                (df['exch_seg'] == 'MCX')
            ].copy()
            
            # Ensure only CE/PE
            crude_df = crude_df[
                crude_df['symbol'].str.endswith("CE") | 
                crude_df['symbol'].str.endswith("PE")
            ].copy()
            
            # Parse and filter expiry
            crude_df['expiry'] = pd.to_datetime(crude_df['expiry'], errors='coerce')
            crude_df = crude_df[crude_df['expiry'].notna()]
            
            today = datetime.now().date()
            crude_df = crude_df[crude_df['expiry'].dt.date >= today]
            
            if crude_df.empty:
                logger.error("No valid CRUDEOIL options found after filtering")
                return None
                
            # OPTIONAL: Restrict to ATM ± 10 strikes (best-effort)
            crude_df = crude_df.sort_values(by='strike')
            mid = len(crude_df) // 2
            crude_df = crude_df.iloc[max(0, mid-10):mid+10]
            
            logger.info(f"Loaded {len(crude_df)} CRUDEOIL options (CE/PE)")
            return crude_df[['symbol', 'token', 'strike', 'expiry']]
            
        except Exception as e:
            logger.error(f"Error processing instrument file: {str(e)}", exc_info=True)
            return None

    def _make_api_request(self, endpoint: str, payload: dict) -> Optional[dict]:
        """Generic API request handler with retry logic"""
        for attempt in range(self.retry_count):
            try:
                # Refresh session if needed
                if not self._is_session_valid() and not self.login():
                    return None
                    
                conn = http.client.HTTPSConnection(API_BASE_URL, timeout=REQUEST_TIMEOUT)
                conn.request("POST", endpoint, json.dumps(payload), self.headers)
                response = conn.getresponse()
                
                if response.status == 401:  # Unauthorized
                    logger.warning("Session expired, attempting re-login")
                    self.session_valid = False
                    continue
                    
                raw = response.read().decode("utf-8")
                
                if response.status != 200:
                    logger.warning(
                        f"API request failed (attempt {attempt+1}): "
                        f"HTTP {response.status} - {raw}"
                    )
                    time.sleep(self.retry_delay * (attempt + 1))
                    continue
                    
                return json.loads(raw)
                
            except (http.client.HTTPException, json.JSONDecodeError) as e:
                logger.warning(f"API request error (attempt {attempt+1}): {str(e)}")
                time.sleep(self.retry_delay * (attempt + 1))
            except Exception as e:
                logger.error(f"Unexpected API error: {str(e)}", exc_info=True)
                break
                
        return None

    def fetch_option_data(self, token: str, symbol: str, 
                         start_time: datetime, end_time: datetime) -> Optional[pd.DataFrame]:
        """Fetch price and OI data for a specific option"""
        try:
            # Determine appropriate interval
            interval = "ONE_MINUTE" if (end_time - start_time).days <= 1 else "FIVE_MINUTE"
            
            payload = {
                "exchange": "MCX",
                "symboltoken": token,
                "interval": interval,
                "fromdate": start_time.strftime('%Y-%m-%d %H:%M'),
                "todate": end_time.strftime('%Y-%m-%d %H:%M')
            }
            
            # Fetch price data
            price_response = self._make_api_request(
                "/rest/secure/angelbroking/historical/v1/getCandleData",
                payload
            )
            
            if not price_response or not price_response.get('data'):
                self.error_counts['price_data'] += 1
                logger.warning(f"No price data for {symbol}")
                return None
                
            price_data = pd.DataFrame(
                price_response['data'],
                columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
            )
            price_data['timestamp'] = pd.to_datetime(price_data['timestamp'])
            
            # Filter to trading hours (9 AM to 11:30 PM for MCX)
            price_data = price_data[
                (price_data['timestamp'].dt.hour >= 9) & 
                (price_data['timestamp'].dt.hour <= 23)
            ]
            
            if price_data.empty:
                logger.warning(f"No price data within trading hours for {symbol}")
                return None
                
            # Fetch OI data
            oi_response = self._make_api_request(
                "/rest/secure/angelbroking/historical/v1/getOIData",
                payload
            )
            
            # Merge OI data if available
            if oi_response and oi_response.get('data'):
                oi_data = pd.DataFrame(
                    oi_response['data'],
                    columns=['timestamp', 'openInterest', 'changeinOpenInterest']
                )
                oi_data['timestamp'] = pd.to_datetime(oi_data['timestamp'])
                merged_data = price_data.merge(oi_data, on='timestamp', how='left')
            else:
                self.error_counts['oi_data'] += 1
                logger.warning(f"No OI data for {symbol}")
                merged_data = price_data
                merged_data['openInterest'] = 0
                merged_data['changeinOpenInterest'] = 0
                
            merged_data['symbol'] = symbol
            return merged_data.sort_values('timestamp')
            
        except Exception as e:
            self.error_counts['other'] += 1
            logger.error(f"Error fetching data for {symbol}: {str(e)}", exc_info=True)
            return None

    def _should_skip_download(self, symbol: str, target_date: datetime) -> bool:
        """Check if we should skip downloading existing files"""
        fname = self.data_dir / f"{symbol}_{target_date.strftime('%Y%m%d')}.csv"
        return fname.exists()

    def _save_failed_symbols(self, failed: List[str], target_date: datetime) -> None:
        """Save list of failed symbols to file"""
        if not failed:
            return
            
        failed_file = self.failed_dir / f"failed_{target_date.strftime('%Y%m%d')}.csv"
        try:
            pd.DataFrame({'symbol': failed}).to_csv(failed_file, index=False)
            logger.info(f"Saved {len(failed)} failed symbols to {failed_file.name}")
        except Exception as e:
            logger.error(f"Error saving failed symbols: {str(e)}")

    def collect_data_for_date(self, target_date: datetime, options: pd.DataFrame) -> Tuple[int, int, int]:
        """Collect data for all options on a specific date"""
        start_time = target_date.replace(hour=9, minute=0, second=0)
        end_time = target_date.replace(hour=23, minute=30, second=0)
        
        success, skipped, failed = [], [], []
        
        # Process options with progress bar
        with tqdm(options.iterrows(), total=len(options), 
                desc=f"🟦 {target_date.strftime('%Y-%m-%d')}", unit="opt") as pbar:
            
            for _, option in pbar:
                symbol = option['symbol']
                token = str(option['token'])
                
                try:
                    # Skip if already downloaded
                    if self._should_skip_download(symbol, target_date):
                        skipped.append(symbol)
                        pbar.set_postfix_str(f"Skipped: {symbol}")
                        continue
                        
                    # Fetch data
                    data = self.fetch_option_data(token, symbol, start_time, end_time)
                    
                    if data is None or data.empty:
                        failed.append(symbol)
                        pbar.set_postfix_str(f"Failed: {symbol}")
                        continue
                        
                    # Save to file
                    fname = self.data_dir / f"{symbol}_{target_date.strftime('%Y%m%d')}.csv"
                    data.to_csv(fname, index=False)
                    success.append(symbol)
                    pbar.set_postfix_str(f"Saved: {symbol}")
                    
                except Exception as e:
                    failed.append(symbol)
                    logger.error(f"Error processing {symbol}: {str(e)}", exc_info=True)
                    pbar.set_postfix_str(f"Error: {symbol}")
                    
                # Small delay between requests to avoid rate limiting
                time.sleep(0.5)
                
        # Save failed symbols list
        self._save_failed_symbols(failed, target_date)
        
        logger.info(
            f"Date {target_date.date()}: "
            f"{len(success)} success, {len(skipped)} skipped, {len(failed)} failed"
        )
        
        return len(success), len(skipped), len(failed)

    def get_trading_dates(self, days_back: int) -> List[datetime]:
        """Generate list of trading dates to process"""
        today = datetime.now().date()
        dates = []
        
        for i in range(days_back):
            date = today - timedelta(days=i)
            # Skip weekends (5=Saturday, 6=Sunday)
            if date.weekday() < 5:
                dates.append(datetime.combine(date, datetime.min.time()))
                
        return sorted(dates)

    def collect_data(self, days_back: int = 1) -> None:
        """Main collection method with date iteration"""
        if not self.login():
            logger.error("Login failed, aborting")
            return
            
        options = self.get_crude_options()
        if options is None or options.empty:
            logger.error("No options data available, aborting")
            return
            
        dates = self.get_trading_dates(days_back)
        logger.info(f"Processing {len(dates)} trading dates")
        
        for date in tqdm(dates, desc="📅 Processing dates", unit="date"):
            try:
                success, skipped, failed = self.collect_data_for_date(date, options)
                
                # Small delay between dates
                time.sleep(5)
                
            except GracefulExit:
                logger.warning("Graceful exit requested during date processing")
                raise
            except Exception as e:
                logger.error(f"Error processing date {date.date()}: {str(e)}", exc_info=True)
                continue
                
        logger.info("Data collection complete")
        self._log_summary()

    def _log_summary(self) -> None:
        """Log summary of errors and statistics"""
        logger.info("=== Collection Summary ===")
        logger.info(f"Login errors: {self.error_counts['login']}")
        logger.info(f"Price data errors: {self.error_counts['price_data']}")
        logger.info(f"OI data errors: {self.error_counts['oi_data']}")
        logger.info(f"Network errors: {self.error_counts['network']}")
        logger.info(f"Other errors: {self.error_counts['other']}")

    def combine_option_data(self) -> bool:
        """Helper method to trigger data combination"""
        try:
            from prepare_historical_data import combine_option_data
            if combine_option_data():
                logger.info("Data combination successful")
                return True
            logger.error("Data combination failed")
            return False
        except ImportError:
            logger.error("Could not import combine_option_data function")
            return False
        except Exception as e:
            logger.error(f"Error during data combination: {str(e)}", exc_info=True)
            return False

if __name__ == "__main__":
    try:
        collector = CrudeDataCollector()
        
        # Collect historical data
        collector.collect_data(days_back=180)
        
        # Combine data if collection was successful
        collector.combine_option_data()
        
    except GracefulExit:
        logger.warning("Program terminated gracefully by user")
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}", exc_info=True)
        sys.exit(1)