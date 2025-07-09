"""Validate back-test using best Optuna parameters.

Usage (PowerShell):
    python backtest_optuna_params.py --start 2025-06-24 --end 2025-07-01
If --start/--end are omitted, the script uses the full available range.   
example : python backtest_optuna_params.py --start 2025-06-24 --end 2025-07-01
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import pytz

# Local imports
from backtest.backtest import StrategyBacktester
from backtest import db_pg_sync

BEST_PARAMS_PATH = Path("backtest_results/optuna/best_params_range_20250706_014242.json")

tz = pytz.timezone("Asia/Kolkata")

def parse_date(ds: str | None):
    if not ds:
        return None
    return tz.localize(datetime.strptime(ds, "%Y-%m-%d"))

def main():
    parser = argparse.ArgumentParser(description="Run validation back-test with best params")
    parser.add_argument("--start", type=str, default=None, help="YYYY-MM-DD inclusive")
    parser.add_argument("--end", type=str, default=None, help="YYYY-MM-DD inclusive")
    args = parser.parse_args()

    start_ts = parse_date(args.start) or tz.localize(datetime(1900, 1, 1))
    end_ts   = parse_date(args.end)   or tz.localize(datetime(2100, 1, 1))

    print(f"📥 Fetching data {start_ts.date()} → {end_ts.date()} …")
    ce_df, pe_df = db_pg_sync.fetch_ohlcv_range(start_ts, end_ts)
    print(f"Rows – CE: {len(ce_df):,}, PE: {len(pe_df):,}")

    params = json.load(BEST_PARAMS_PATH.open())
    bt = StrategyBacktester(strategy_params=params)
    results = bt.backtest(ce_df, pe_df, initial_capital=100_000)
    print("\n✅ Combined Performance:\n", results["combined"])

if __name__ == "__main__":
    main()
