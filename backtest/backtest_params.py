"""Standalone helper to run a quick back-test from the command line.

python backtest/backtest_params.py [--params path_to_json] [--days 30]

If run without arguments it will:
1. Fetch CE/PE data for the last N days (default 30) from Postgres.
2. Load the best Optuna parameters (json) given via --params or latest file pattern.
3. Execute StrategyBacktester and print a concise performance summary.
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

# Ensure project root on PYTHONPATH
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest import db_pg_sync, backtest as bt  # type: ignore


def _latest_best_params() -> Path:
    pattern = ROOT / "backtest_results" / "optuna" / "best_params_*.json"
    files = sorted(glob.glob(str(pattern)), reverse=True)
    if not files:
        raise FileNotFoundError("No best_params_*.json found under backtest_results/optuna")
    return Path(files[0])


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run quick back-test with stored Optuna params")
    parser.add_argument("--params", type=Path, default=None, help="Path to best_params JSON")
    parser.add_argument("--days", type=int, default=30, help="How many recent days to test")
    parser.add_argument("--capital", type=float, default=100000.0, help="Initial capital")

    args = parser.parse_args(argv)

    params_file = args.params or _latest_best_params()
    print(f"➡️  Using parameters from {params_file}")
    params = json.loads(Path(params_file).read_text())

    end_dt = datetime.now(tz=timezone.utc)
    start_dt = end_dt - timedelta(days=args.days)
    print(f"➡️  Fetching {args.days} days of CE/PE data: {start_dt:%Y-%m-%d} → {end_dt:%Y-%m-%d}")

    ce_df, pe_df = db_pg_sync.fetch_ohlcv_range(start_dt, end_dt)
    if ce_df.empty or pe_df.empty:
        sys.exit("❌ No data returned from Postgres for requested range")

    ce_df.set_index("timestamp", inplace=True)
    pe_df.set_index("timestamp", inplace=True)

    tester = bt.StrategyBacktester(params)
    results = tester.backtest(ce_df, pe_df, initial_capital=args.capital)

    combined = results["combined"]
    total_ret = combined["equity_curve"].iloc[-1] - args.capital
    max_dd = combined["max_drawdown"]

    print("\n===== Back-test Summary =====")
    print(f"Total P/L: ₹{total_ret:,.0f}")
    print(f"Max Drawdown: {max_dd:.2%}")
    print(f"Trades: {combined['trades'][-1] if 'trades' in combined else 'n/a'}")


if __name__ == "__main__":
    main()
