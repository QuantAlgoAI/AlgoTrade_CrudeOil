# Refactored `crude_data_collector.py` with TQDM, SIGINT handling, skip on failure, and failed.csv logging

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

# === Setup Graceful Exit ===
class GracefulExit(Exception): pass

def signal_handler(sig, frame):
    raise GracefulExit()

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# === Load .env ===
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
API_KEY = os.getenv("ANGEL_ONE_API_KEY")
CLIENT_CODE = os.getenv("ANGEL_ONE_CLIENT_CODE")
PASSWORD = os.getenv("ANGEL_ONE_PASSWORD")
TOTP_SECRET = os.getenv("ANGEL_ONE_TOTP_SECRET")

# === Logging Setup ===
log_dir = Path(__file__).parent / "logs"
log_dir.mkdir(exist_ok=True)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler = logging.FileHandler(log_dir / f"crude_data_{datetime.now().strftime('%Y%m%d')}.log")
file_handler.setFormatter(formatter)
file_handler.setLevel(logging.DEBUG)
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(formatter)
console_handler.setLevel(logging.INFO)
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)
logger.handlers.clear()
logger.addHandler(file_handler)
logger.addHandler(console_handler)

# === Collector Class ===
class CrudeDataCollector:
    def __init__(self):
        self.api = None
        self.session_valid = False
        self.retry_count = 1
        self.retry_delay = 5
        self.data_dir = Path(__file__).parent / 'data'
        self.data_dir.mkdir(exist_ok=True)
        self.local_ip = socket.gethostbyname(socket.gethostname())
        try:
            self.public_ip = requests.get("https://api.ipify.org", timeout=5).text.strip()
        except:
            self.public_ip = self.local_ip
        self.mac_address = ':'.join(['{:02x}'.format((uuid.getnode() >> i) & 0xff) for i in range(0,8*6,8)][::-1])
        self.headers = None
        self.error_counts = {'login': 0, 'price_data': 0, 'oi_data': 0, 'network': 0}

    def login(self):
        for attempt in range(self.retry_count):
            try:
                logger.info(f"Login attempt {attempt + 1}")
                self.api = SmartConnect(api_key=API_KEY)
                session = self.api.generateSession(CLIENT_CODE, PASSWORD, pyotp.TOTP(TOTP_SECRET).now())
                self.session_valid = 'data' in session
                if not self.session_valid: continue
                self.headers = {
                    'Authorization': session['data']['jwtToken'],
                    'X-PrivateKey': API_KEY,
                    'X-ClientCode': CLIENT_CODE,
                    'X-FeedToken': session['data']['feedToken'],
                    'X-SourceID': 'WEB',
                    'X-ClientLocalIP': self.local_ip,
                    'X-ClientPublicIP': self.public_ip,
                    'X-MACAddress': self.mac_address,
                    'X-UserType': 'USER',
                    'Content-Type': 'application/json',
                    'Accept': 'application/json'
                }
                logger.info("[LOGIN] Success")
                return True
            except Exception as e:
                self.error_counts['login'] += 1
                wait = self.retry_delay + random.uniform(0, 1)
                logger.warning(f"[LOGIN] Failed: {e}, retrying in {wait:.2f}s")
                time.sleep(wait)
        return False

    def get_crude_options(self):
        instrument_file = Path(__file__).parent.parent / "instruments" / f"{datetime.now().strftime('%Y%m%d')}_instrument_file.csv"
        if not instrument_file.exists():
            files = list((Path(__file__).parent.parent / "instruments").glob("*_instrument_file.csv"))
            if not files:
                logger.error("[MISSING] No instrument file found in instruments/")
                return None
            instrument_file = max(files)
            logger.warning(f"[WARN] Falling back to most recent instrument file: {instrument_file.name}")

        df = pd.read_csv(instrument_file, low_memory=False)
        
        # Filter CRUDEOIL options
        df = df[(df['name'] == 'CRUDEOIL') & 
                (df['instrumenttype'] == 'OPTFUT') & 
                (df['exch_seg'] == 'MCX')]

        # Ensure only CE/PE
        df = df[df['symbol'].str.endswith("CE") | df['symbol'].str.endswith("PE")].copy()

        # Parse and filter expiry
        df['expiry'] = pd.to_datetime(df['expiry'], errors='coerce')
        df = df[df['expiry'].notna()]
        today = datetime.now().date()
        df = df[df['expiry'].dt.date >= today]

        if df.empty:
            logger.error("[FILTER] No valid CRUDEOIL options found in instrument file")
            return None

        # OPTIONAL: Restrict to ATM ± 10 strikes (best-effort)
        df = df.sort_values(by='strike')
        mid = len(df) // 2
        df = df.iloc[max(0, mid-10):mid+10]

        logger.info(f"[OPTIONS] Loaded {len(df)} CRUDEOIL options (CE/PE) for fetch")
        return df[['symbol', 'token', 'strike']]


    def fetch_data(self, token, symbol, start_time, end_time):
        try:
            interval = "ONE_MINUTE" if (end_time - start_time).days <= 1 else "FIVE_MINUTE"
            payload = json.dumps({
                "exchange": "MCX",
                "symboltoken": str(token),
                "interval": interval,
                "fromdate": start_time.strftime('%Y-%m-%d %H:%M'),
                "todate": end_time.strftime('%Y-%m-%d %H:%M')
            })

            conn = http.client.HTTPSConnection("apiconnect.angelone.in", timeout=30)
            conn.request("POST", "/rest/secure/angelbroking/historical/v1/getCandleData", payload, self.headers)
            response = conn.getresponse()
            raw = response.read().decode("utf-8")

            if response.status != 200:
                logger.error(f"[FETCH] HTTP {response.status} for {symbol}")
                logger.debug(f"[DEBUG] Response body: {raw}")
                return None

            data = json.loads(raw)
            if not data.get('data'):
                logger.error(f"[FETCH] No 'data' field for {symbol}")
                return None

            price_data = pd.DataFrame(data['data'], columns=['timestamp','open','high','low','close','volume'])
            price_data['timestamp'] = pd.to_datetime(price_data['timestamp'])
            price_data = price_data[(price_data['timestamp'].dt.hour >= 9) & (price_data['timestamp'].dt.hour <= 23)]

            # OI fetch
            oi_data = None
            try:
                conn = http.client.HTTPSConnection("apiconnect.angelone.in", timeout=30)
                conn.request("POST", "/rest/secure/angelbroking/historical/v1/getOIData", payload, self.headers)
                oi_raw = conn.getresponse().read().decode("utf-8")
                oi_json = json.loads(oi_raw)
                if oi_json.get('data'):
                    oi_data = pd.DataFrame(oi_json['data'], columns=['timestamp','openInterest','changeinOpenInterest'])
                    oi_data['timestamp'] = pd.to_datetime(oi_data['timestamp'])
            except Exception as oe:
                self.error_counts['oi_data'] += 1
                logger.warning(f"[OI] Failed for {symbol}: {oe}")

            if oi_data is not None:
                df = price_data.merge(oi_data, on='timestamp', how='left')
            else:
                price_data['openInterest'] = 0
                price_data['changeinOpenInterest'] = 0
                df = price_data

            df['symbol'] = symbol
            return df.sort_values('timestamp')

        except Exception as e:
            self.error_counts['network'] += 1
            logger.error(f"[FETCH] Exception for {symbol}: {e}")
            return None


    def collect_data_for_date(self, target_date, options):
        start_time = target_date.replace(hour=9)
        end_time = target_date.replace(hour=23, minute=30)
        success, failed, skipped = [], [], []

        for _, option in tqdm(options.iterrows(), total=len(options), desc=f"🟦 {target_date.strftime('%Y-%m-%d')}", unit="opt"):
            try:
                symbol = option['symbol']
                token = str(option['token'])
                fname = self.data_dir / f"{symbol}_{target_date.strftime('%Y%m%d')}.csv"
                if fname.exists():
                    skipped.append(symbol)
                    continue
                data = self.fetch_data(token, symbol, start_time, end_time)
                if data is None or data.empty:
                    failed.append(symbol)
                    continue
                data.to_csv(fname, index=False)
                success.append(symbol)
            except Exception as e:
                logger.error(f"[ERROR] {symbol} on {target_date}: {e}")
                failed.append(symbol)

        pd.DataFrame({'symbol': failed}).to_csv(self.data_dir / f"failed_{target_date.strftime('%Y%m%d')}.csv", index=False)
        return len(success), len(skipped), len(failed)

    def collect_data(self, days_back=1):
        if not self.login():
            logger.error("[ABORT] Login failed")
            return

        options = self.get_crude_options()
        if options is None: return

        today = datetime.now().date()
        dates = [today - timedelta(days=i) for i in range(days_back) if (today - timedelta(days=i)).weekday() < 5]
        dates.reverse()
        logger.info(f"Processing dates: {[d.strftime('%Y-%m-%d') for d in dates]}")

        for date in tqdm(dates, desc="📅 Collecting per date", unit="day"):
            try:
                self.collect_data_for_date(datetime.combine(date, datetime.min.time()), options)
                time.sleep(5)
            except Exception as e:
                logger.exception(f"[ERROR] Failed on date {date.strftime('%Y-%m-%d')}: {e}")

        logger.info("[DONE] Collection complete")

        try:
            from prepare_historical_data import combine_option_data
            if combine_option_data():
                logger.info("[INFO] Combined CE/PE data successfully")
            else:
                logger.error("[FAIL] Data combine step failed")
        except Exception as e:
            logger.error(f"[ERROR] combine_option_data failed: {e}")

if __name__ == "__main__":
    try:
        CrudeDataCollector().collect_data(days_back=180)
    except GracefulExit:
        logger.warning("[EXIT] Interrupted by user")
    except Exception as e:
        logger.exception(f"[FATAL] Unexpected error: {e}")
