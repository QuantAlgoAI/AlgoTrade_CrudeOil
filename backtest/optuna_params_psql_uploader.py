import os
import json
import pandas as pd
import psycopg2
from pathlib import Path

# PostgreSQL connection config
DB_CONFIG = {
    'host': 'localhost',
    'port': 5432,
    'user': 'postgres',
    'password': 'postgres',
    'dbname': 'quantalgo_db'
}

# Base directory
OPTUNA_DIR = Path("backtest/backtest_results/optuna")

# Valid subfolders (intervals)
INTERVALS = ['bars_1m', 'bars_5s']

def load_all_params():
    records = []
    trial_id = 1

    for interval in INTERVALS:
        folder = OPTUNA_DIR / interval
        if not folder.exists():
            continue

        for file in folder.glob("best_params_*.json"):
            with open(file, "r") as f:
                try:
                    params = json.load(f)
                except json.JSONDecodeError:
                    print(f"⚠️ Failed to read {file}")
                    continue

            params['timestamp'] = file.stem.replace('best_params_', '')
            params['bar_interval'] = interval
            params['trial_id'] = trial_id
            records.append(params)
            trial_id += 1

    df = pd.DataFrame(records)

    # Ensure all boolean columns exist with default False if missing
    bool_cols = ['use_fast_ema', 'use_slow_ema', 'use_rsi', 'use_atr', 'use_vwap']
    for col in bool_cols:
        if col not in df.columns:
            df[col] = False
        else:
            # Fill NaNs and convert to bool
            pd.set_option('future.no_silent_downcasting', True)
            df[col] = df[col].fillna(False).astype(bool)

    # Fill NaNs for other columns with zero or sensible defaults
    df.fillna({
        'fast_ema_period': 0,
        'slow_ema_period': 0,
        'rsi_period': 0,
        'atr_period': 0,
        'vwap_period': 0,
        'rsi_oversold': 0,
        'rsi_overbought': 0,
        'volume_surge_factor': 0.0,
        'atr_volatility_factor': 0.0,
        'timestamp': '',
        'bar_interval': ''
    }, inplace=True)

    return df


def insert_params(df: pd.DataFrame):
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()

        # Create table if not exists
        cur.execute("""
        CREATE TABLE IF NOT EXISTS optuna_params (
            trial_id SERIAL PRIMARY KEY,
            fast_ema_period INT,
            slow_ema_period INT,
            rsi_period INT,
            atr_period INT,
            vwap_period INT,
            rsi_oversold INT,
            rsi_overbought INT,
            volume_surge_factor FLOAT,
            atr_volatility_factor FLOAT,
            use_fast_ema BOOLEAN,
            use_slow_ema BOOLEAN,
            use_rsi BOOLEAN,
            use_atr BOOLEAN,
            use_vwap BOOLEAN,
            timestamp TEXT,
            bar_interval TEXT
        )
        """)

        for _, row in df.iterrows():
            cur.execute("""
            INSERT INTO optuna_params (
                fast_ema_period, slow_ema_period, rsi_period, atr_period, vwap_period,
                rsi_oversold, rsi_overbought, volume_surge_factor, atr_volatility_factor,
                use_fast_ema, use_slow_ema, use_rsi, use_atr, use_vwap,
                timestamp, bar_interval
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                row['fast_ema_period'], row['slow_ema_period'], row['rsi_period'],
                row['atr_period'], row['vwap_period'],
                row['rsi_oversold'], row['rsi_overbought'],
                row['volume_surge_factor'], row['atr_volatility_factor'],
                row['use_fast_ema'], row['use_slow_ema'], row['use_rsi'],
                row['use_atr'], row['use_vwap'],
                row['timestamp'], row['bar_interval']
            ))

        conn.commit()
        print("✅ Params successfully inserted into optuna_params table.")
    except Exception as e:
        print(f"❌ Error inserting params: {e}")
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


if __name__ == "__main__":
    df = load_all_params()
    print(f"✅ Loaded {len(df)} parameter sets from backtest/backtest_results/optuna")

    if df.empty:
        print("⚠️ No parameter files found.")
    else:
        insert_params(df)
