from SmartApi import SmartConnect
import pyotp
import json
import logging
import pandas as pd
from datetime import datetime, timedelta
import http.client
import socket
import uuid
import requests
import time
from pathlib import Path
import sys
import os
from dotenv import load_dotenv

# Add parent directory to path to import from main project


# Load environment variables
load_dotenv(Path(__file__).resolve().parents[2] / '.env')

# Get credentials from environment variables
API_KEY = os.getenv('ANGEL_ONE_API_KEY')
CLIENT_CODE = os.getenv('ANGEL_ONE_CLIENT_CODE')
PASSWORD = os.getenv('ANGEL_ONE_PASSWORD')
TOTP_SECRET = os.getenv('ANGEL_ONE_TOTP_SECRET')

# Validate credentials
if not all([API_KEY, CLIENT_CODE, PASSWORD, TOTP_SECRET]):
    missing = [name for name, value in [
        ('ANGEL_ONE_API_KEY', API_KEY),
        ('ANGEL_ONE_CLIENT_CODE', CLIENT_CODE),
        ('ANGEL_ONE_PASSWORD', PASSWORD),
        ('ANGEL_ONE_TOTP_SECRET', TOTP_SECRET)
    ] if not value]
    raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

# Setup logging with both file and console output
def setup_logging():
    log_dir = Path('logs')
    log_dir.mkdir(exist_ok=True)
    
    # Create a formatter that includes more details
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s'
    )
    
    # File handler with detailed logging
    file_handler = logging.FileHandler(
        log_dir / f'crude_data_{datetime.now().strftime("%Y%m%d")}.log'
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    
    # Console handler with less verbose output
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)
    
    # Setup root logger
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    logger.handlers = []  # Remove any existing handlers
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

logger = setup_logging()

class CrudeDataCollector:
    def __init__(self):
        self.api = None
        self.auth_token = None
        self.feed_token = None
        self.session_valid = False
        self.data_dir = Path(__file__).parent / 'data'
        self.data_dir.mkdir(exist_ok=True)
        self.headers = None
        self.local_ip, self.public_ip, self.mac_address = self._get_system_info()
        self.retry_count = 3
        self.retry_delay = 5
        self.error_counts = {
            'login': 0,
            'price_data': 0,
            'oi_data': 0,
            'network': 0
        }
        
    def _get_system_info(self):
        """Get system information for headers"""
        try:
            local_ip = socket.gethostbyname(socket.gethostname())
            try:
                public_ip = requests.get('https://api.ipify.org', timeout=5).text.strip()
            except:
                logger.warning("Could not get public IP, using local IP")
                public_ip = local_ip
            
            mac = ':'.join(['{:02x}'.format((uuid.getnode() >> elements) & 0xff)
                          for elements in range(0,8*6,8)][::-1])
            logger.debug(f"System info - Local IP: {local_ip}, Public IP: {public_ip}, MAC: {mac}")
            return local_ip, public_ip, mac
        except Exception as e:
            logger.error(f"Error getting system info: {str(e)}")
            return '0.0.0.0', '0.0.0.0', '00:00:00:00:00:00'

    def _make_api_request(self, endpoint, payload, retry_count=None):
        """Make API request with retry mechanism"""
        if retry_count is None:
            retry_count = self.retry_count
            
        for attempt in range(retry_count):
            try:
                if not self.session_valid:
                    if not self.login():
                        raise Exception("Failed to login")
                
                conn = http.client.HTTPSConnection("apiconnect.angelone.in", timeout=30)
                logger.debug(f"Making request to {endpoint} (Attempt {attempt + 1}/{retry_count})")
                conn.request("POST", endpoint, payload, self.headers)
                response = conn.getresponse()
                data = json.loads(response.read().decode("utf-8"))
                
                if data.get('status'):
                    return data
                elif 'error' in data:
                    error_msg = data.get('error', {}).get('message', 'Unknown error')
                    error_code = data.get('error', {}).get('code', 'Unknown code')
                    
                    if 'token expired' in error_msg.lower():
                        logger.warning(f"Token expired (Error {error_code}), refreshing session...")
                        self.session_valid = False
                        if attempt < retry_count - 1:
                            continue
                    
                    if endpoint.endswith('getOIData'):
                        self.error_counts['oi_data'] += 1
                    else:
                        self.error_counts['price_data'] += 1
                        
                    logger.error(f"API Error ({error_code}): {error_msg}")
                
            except Exception as e:
                self.error_counts['network'] += 1
                logger.error(f"Network error on attempt {attempt + 1}: {str(e)}")
                if attempt < retry_count - 1:
                    wait_time = self.retry_delay * (attempt + 1)
                    logger.info(f"Waiting {wait_time} seconds before retry...")
                    time.sleep(wait_time)
            finally:
                try:
                    conn.close()
                except:
                    pass
                    
        return None

    def login(self):
        """Login to Angel Broking API with retry mechanism"""
        for attempt in range(self.retry_count):
            try:
                logger.info(f"Login attempt {attempt + 1}/{self.retry_count}")
                self.api = SmartConnect(api_key=API_KEY)
                totp = pyotp.TOTP(TOTP_SECRET)
                session = self.api.generateSession(CLIENT_CODE, PASSWORD, totp.now())
                
                if session.get('data'):
                    self.auth_token = session['data']['jwtToken']
                    self.feed_token = session['data']['feedToken']
                    self.session_valid = True
                    
                    # Setup headers
                    self.headers = {
                        'X-PrivateKey': API_KEY,
                        'Accept': 'application/json',
                        'X-SourceID': 'WEB',
                        'X-ClientLocalIP': self.local_ip,
                        'X-ClientPublicIP': self.public_ip,
                        'X-MACAddress': self.mac_address,
                        'X-UserType': 'USER',
                        'Authorization': self.auth_token,
                        'Content-Type': 'application/json',
                        'X-ClientCode': CLIENT_CODE,
                        'X-FeedToken': self.feed_token
                    }
                    
                    logger.info("[SUCCESS] Successfully logged in!")
                    return True
                else:
                    logger.error("[ERROR] Login failed: No session data received")
                    
            except Exception as e:
                self.error_counts['login'] += 1
                logger.error(f"[ERROR] Login error on attempt {attempt + 1}: {str(e)}")
                if attempt < self.retry_count - 1:
                    wait_time = self.retry_delay * (attempt + 1)
                    logger.info(f"Waiting {wait_time} seconds before retry...")
                    time.sleep(wait_time)
                    
        logger.error("[ERROR] All login attempts failed")
        return False

    def get_crude_options(self):
        """Get actively traded CRUDEOIL options"""
        try:
            today = datetime.now().strftime('%Y%m%d')
            # Look for instrument file in parent directory
            instrument_file = Path(__file__).resolve().parents[2] / f"data/instruments/{today}_instrument_file.csv"
            
            if not instrument_file.exists():
                # Try to find the most recent instrument file
                instruments_dir = Path(__file__).parent.parent / "instruments"
                if instruments_dir.exists():
                    instrument_files = list(instruments_dir.glob("*_instrument_file.csv"))
                    if instrument_files:
                        # Get the most recent file
                        instrument_file = max(instrument_files, key=lambda x: x.name)
                        logger.warning(f"[WARN] Today's instrument file not found, using most recent: {instrument_file.name}")
                    else:
                        logger.error(f"[ERROR] No instrument files found in {instruments_dir}")
                        return None
                else:
                    logger.error(f"[ERROR] Instruments directory not found: {instruments_dir}")
                    return None
                
            logger.info(f"[INFO] Reading instrument file: {instrument_file}")
            df = pd.read_csv(instrument_file, low_memory=False)
            
            crude_options = df[
                (df['name'] == 'CRUDEOIL') &
                (df['exch_seg'] == 'MCX') &
                (df['instrumenttype'] == 'OPTFUT')
            ].copy()
            
            if crude_options.empty:
                logger.error("[ERROR] No CRUDEOIL options found")
                return None
                
            # Add expiry date in datetime format
            crude_options['expiry'] = pd.to_datetime(crude_options['expiry'], errors='coerce')

            if crude_options['expiry'].isnull().any():
                logger.warning("[WARN] Some expiry values could not be parsed and were set to NaT")
                crude_options = crude_options.dropna(subset=['expiry'])
            
            # Filter for nearest expiry
            nearest_expiry = crude_options['expiry'].min()
            crude_options = crude_options[crude_options['expiry'] == nearest_expiry]
            logger.info(f"[INFO] Using nearest expiry: {nearest_expiry.strftime('%d-%b-%Y')}")
            
            # Get current futures price to find ATM strike
            futures = df[
                (df['name'] == 'CRUDEOIL') &
                (df['exch_seg'] == 'MCX') &
                (df['instrumenttype'] == 'FUTCOM') &
                (df['expiry'] == nearest_expiry.strftime('%d%b%Y'))
            ]
            
            if not futures.empty:
                # Get ATM and nearby strikes
                fut_price = float(futures.iloc[0]['strike']) / 100  # Convert to actual price
                atm_strike = round(fut_price / 50) * 50
                strikes = [atm_strike + (i * 50) for i in range(-5, 6)]  # 5 strikes above and below ATM
                
                logger.info(f"[INFO] Futures Price: {fut_price:.2f}, ATM Strike: {atm_strike}")
                logger.info(f"[INFO] Selected strikes: {', '.join(map(str, strikes))}")
                
                # Filter options for these strikes
                crude_options = crude_options[
                    (crude_options['strike'].apply(lambda x: float(x)/100).isin(strikes))
                ]
            else:
                logger.warning("[WARN] No futures data found, using all strikes")
            
            crude_options = crude_options.sort_values(['expiry', 'strike'])
            logger.info(f"[INFO] Found {len(crude_options)} active CRUDEOIL options")
            
            # Log strike details
            for _, opt in crude_options.iterrows():
                strike = float(opt['strike']) / 100
                symbol = opt['symbol']
                logger.debug(f"Strike {strike}: {symbol}")
                
            return crude_options
            
        except Exception as e:
            logger.error(f"[ERROR] Error getting CRUDEOIL options: {str(e)}")
            return None

    def fetch_data(self, token, symbol, start_time, end_time, interval='FIVE_MINUTE'):
        """Fetch both price and OI data for a token"""
        if not self.session_valid:
            if not self.login():
                return None
        
        try:
            # For single day requests, use more granular data
            time_diff = end_time - start_time
            if time_diff.days <= 1:
                interval = "ONE_MINUTE"  # Use 1-minute data for single day
            
            payload = json.dumps({
                "exchange": "MCX",
                "symboltoken": str(token),
                "interval": interval,
                "fromdate": start_time.strftime('%Y-%m-%d %H:%M'),
                "todate": end_time.strftime('%Y-%m-%d %H:%M')
            })
            
            logger.debug(f"[DEBUG] Fetching {interval} data for {symbol} from {start_time} to {end_time}")
            
            # Get price data
            price_data = None
            data = self._make_api_request("/rest/secure/angelbroking/historical/v1/getCandleData", 
                                        payload)
            
            if data and data.get('data'):
                price_data = pd.DataFrame(data['data'], 
                                        columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                price_data['timestamp'] = pd.to_datetime(price_data['timestamp'])
                
                # Filter to market hours (9:00 AM to 11:30 PM)
                price_data = price_data[
                    (price_data['timestamp'].dt.hour >= 9) & 
                    (price_data['timestamp'].dt.hour <= 23) & 
                    ~((price_data['timestamp'].dt.hour == 23) & (price_data['timestamp'].dt.minute > 30))
                ]
                
                logger.debug(f"[DEBUG] Got {len(price_data)} price records for {symbol}")
            else:
                logger.debug(f"[DEBUG] No price data available for {symbol}")
                return None  # Skip if no price data
            
            # Get OI data with extra retries
            oi_data = None
            try:
                data = self._make_api_request("/rest/secure/angelbroking/historical/v1/getOIData", 
                                            payload, retry_count=3)  # Fewer retries for faster processing
                
                if data and data.get('data'):
                    oi_data = pd.DataFrame(data['data'],
                                         columns=['timestamp', 'openInterest', 'changeinOpenInterest'])
                    oi_data['timestamp'] = pd.to_datetime(oi_data['timestamp'])
                    logger.debug(f"[DEBUG] Got {len(oi_data)} OI records for {symbol}")
                else:
                    logger.debug(f"[DEBUG] No OI data available for {symbol}")
            except Exception as oi_error:
                logger.debug(f"[DEBUG] OI data fetch failed for {symbol}: {str(oi_error)}")
                self.error_counts['oi_data'] += 1
            
            # Combine price and OI data
            if price_data is not None and not price_data.empty:
                if oi_data is not None and not oi_data.empty:
                    combined_data = price_data.merge(oi_data, on='timestamp', how='left')
                    logger.debug(f"[DEBUG] Combined {len(price_data)} price records with {len(oi_data)} OI records")
                else:
                    combined_data = price_data.copy()
                    combined_data['openInterest'] = 0
                    combined_data['changeinOpenInterest'] = 0
                    logger.debug("[DEBUG] Added zero OI columns to price data")
                
                # Add symbol and sort
                combined_data['symbol'] = symbol
                combined_data = combined_data.sort_values('timestamp')
                combined_data = combined_data.ffill()  # Forward fill missing values
                
                logger.debug(f"[DEBUG] Final dataset has {len(combined_data)} records for {symbol}")
                return combined_data
                
            return None
            
        except Exception as e:
            logger.error(f"[ERROR] Error fetching data for {symbol}: {str(e)}")
            self.error_counts['network'] += 1
            self.session_valid = False  # Force re-login on next attempt
            return None

    def collect_data_for_date(self, target_date, options):
        """Collect data for a specific date"""
        logger.info(f"\n[INFO] Collecting data for date: {target_date.strftime('%Y-%m-%d')}")
        
        # Setup time range for the specific date
        start_time = target_date.replace(hour=9, minute=0, second=0, microsecond=0)
        end_time = target_date.replace(hour=23, minute=30, second=0, microsecond=0)
        
        successful_count = 0
        failed_count = 0
        skipped_count = 0
        
        for _, option in options.iterrows():
            try:
                symbol = option['symbol']
                token = str(option['token'])
                strike_price = float(option['strike']) / 100
                
                # Check if file already exists for this date
                filename = self.data_dir / f"{symbol}_{target_date.strftime('%Y%m%d')}.csv"
                if filename.exists():
                    logger.debug(f"[SKIP] File already exists: {filename}")
                    skipped_count += 1
                    continue
                
                logger.debug(f"[INFO] Processing {symbol} for {target_date.strftime('%Y-%m-%d')}")
                
                # Fetch data for this specific date
                data = self.fetch_data(token, symbol, start_time, end_time)
                
                if data is not None and not data.empty:
                    # Filter data to ensure it's only for the target date
                    data['date'] = data['timestamp'].dt.date
                    data = data[data['date'] == target_date.date()]
                    
                    if not data.empty:
                        # Calculate additional columns
                        data['strike'] = strike_price
                        data['option_type'] = 'CE' if symbol.endswith('CE') else 'PE'
                        
                        # Remove the date column before saving
                        data = data.drop('date', axis=1)
                        
                        # Save to file
                        data.to_csv(filename, index=False)
                        logger.debug(f"[SUCCESS] Saved {len(data)} records to {filename}")
                        successful_count += 1
                    else:
                        logger.debug(f"[WARN] No data for {symbol} on {target_date.strftime('%Y-%m-%d')}")
                        skipped_count += 1
                else:
                    logger.debug(f"[WARN] No data available for {symbol} on {target_date.strftime('%Y-%m-%d')}")
                    skipped_count += 1
                
                # Small delay between requests for the same date
                time.sleep(1)
                
            except Exception as e:
                logger.error(f"[ERROR] Error processing {symbol} for {target_date.strftime('%Y-%m-%d')}: {str(e)}")
                failed_count += 1
                continue
        
        logger.info(f"[DATE SUMMARY] {target_date.strftime('%Y-%m-%d')}: "
                   f"Success: {successful_count}, Skipped: {skipped_count}, Failed: {failed_count}")
        return successful_count, skipped_count, failed_count

    def collect_data(self, days_back=1):
        """Main method to collect CRUDEOIL options data"""
        logger.info("\n=== Starting CRUDEOIL Options Data Collection ===")
        
        if not self.login():
            logger.error("[ERROR] Failed to login, aborting data collection")
            return
            
        # Get actively traded CRUDEOIL options
        options = self.get_crude_options()
        if options is None:
            return
            
        # Generate list of dates to collect data for
        today = datetime.now().date()
        dates_to_collect = []
        
        for i in range(days_back):
            target_date = today - timedelta(days=i)
            # Skip weekends (Saturday=5, Sunday=6)
            if target_date.weekday() < 5:  # Monday=0 to Friday=4
                dates_to_collect.append(datetime.combine(target_date, datetime.min.time()))
        
        dates_to_collect.reverse()  # Collect oldest first
        logger.info(f"[INFO] Will collect data for {len(dates_to_collect)} trading days: "
                   f"{', '.join([d.strftime('%Y-%m-%d') for d in dates_to_collect])}")
        
        # Process each date
        total_successful = 0
        total_failed = 0
        total_skipped = 0
        
        for date in dates_to_collect:
            try:
                successful, skipped, failed = self.collect_data_for_date(date, options)
                total_successful += successful
                total_skipped += skipped
                total_failed += failed
                
                # Longer delay between dates to avoid rate limiting
                if date != dates_to_collect[-1]:  # Not the last date
                    logger.info(f"[INFO] Waiting 30 seconds before processing next date...")
                    time.sleep(30)
                    
            except Exception as e:
                logger.error(f"[ERROR] Error processing date {date.strftime('%Y-%m-%d')}: {str(e)}")
                total_failed += len(options)
                continue
            
        # Print final summary
        logger.info("\n=== Final Data Collection Summary ===")
        logger.info(f"[SUCCESS] Total files created: {total_successful}")
        logger.info(f"[WARN] Total skipped (existing/no data): {total_skipped}")
        logger.info(f"[ERROR] Total failed: {total_failed}")
        logger.info(f"[INFO] Dates processed: {len(dates_to_collect)}")
        logger.info(f"[INFO] Options per date: {len(options)}")
        logger.info(f"[INFO] Expected total files: {len(dates_to_collect) * len(options)}")
        
        logger.info("\nError Counts:")
        logger.info(f"- Login errors: {self.error_counts['login']}")
        logger.info(f"- Price data errors: {self.error_counts['price_data']}")
        logger.info(f"- OI data errors: {self.error_counts['oi_data']}")
        logger.info(f"- Network errors: {self.error_counts['network']}")
        
        # List files in data directory
        data_files = list(self.data_dir.glob("*.csv"))
        logger.info(f"\n[INFO] Total files in data directory: {len(data_files)}")
        
        # Group by date
        dates_with_files = {}
        for file in data_files:
            # Extract date from filename (last 8 characters before .csv)
            try:
                date_str = file.stem.split('_')[-1]
                if len(date_str) == 8 and date_str.isdigit():
                    if date_str not in dates_with_files:
                        dates_with_files[date_str] = 0
                    dates_with_files[date_str] += 1
            except:
                continue
        
        logger.info(f"[INFO] Files per date:")
        for date_str, count in sorted(dates_with_files.items()):
            logger.info(f"  {date_str}: {count} files")

# def main():
#     collector = CrudeDataCollector()
#     collector.collect_data(days_back=30)  # Collect last 30 days of data

def main():
    collector = CrudeDataCollector()
    collector.collect_data(days_back=30)  # or any number of days you want

    # === AUTO-RUN THE COMBINER SCRIPT AFTER DATA COLLECTION ===
    try:
        from prepare_historical_data import combine_option_data
        logger.info("\n=== Running Option Data Combiner ===")
        if combine_option_data():
            logger.info("[INFO] Successfully combined CE/PE data")
        else:
            logger.error("[ERROR] Failed to combine CE/PE data")
    except Exception as e:
        logger.error(f"[ERROR] Could not run combine_option_data: {str(e)}")

if __name__ == "__main__":
    main() 