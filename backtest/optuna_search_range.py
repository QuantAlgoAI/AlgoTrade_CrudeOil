"""Optuna optimiser with configurable date range.

This script is identical to `optuna_search.py` but lets you restrict the
 dataset window to speed-up experimentation.

Example (one week of data):
    python backtest/optuna_search_range.py \
        --trials 50 \
        --start 2025-06-24 --end 2025-07-01
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from functools import lru_cache

import optuna  # type: ignore
import pandas as pd
import pytz  # for timezone-aware dates
from tqdm import tqdm

# Ensure we can import backtester
sys.path.append(str(Path(__file__).resolve().parent))
from backtest import StrategyBacktester  # type: ignore

import importlib.util as _iu
from pathlib import Path as _Path

# Robustly load db_pg_sync whether or not 'backtest' is a package
_db_path = _Path(__file__).resolve().parent / "db_pg_sync.py"
if _db_path.exists():
    spec = _iu.spec_from_file_location("db_pg_sync", str(_db_path))
    db_pg_sync = _iu.module_from_spec(spec)  # type: ignore
    spec.loader.exec_module(db_pg_sync)  # type: ignore
else:
    db_pg_sync = None  # type: ignore

RESULT_DIR = Path("backtest_results/optuna")
RESULT_DIR.mkdir(parents=True, exist_ok=True)
INITIAL_CAPITAL = 100_000

# Same search bounds as original script
SEARCH_BOUNDS: dict[str, tuple[float, float]] = {
    'fast_ema_period': (2, 6),
    'slow_ema_period': (5, 10),
    'rsi_period': (4, 10),
    'atr_period': (5, 12),
    'vwap_period': (8, 18),
    'rsi_oversold': (20, 40),
    'rsi_overbought': (60, 80),
    'volume_surge_factor': (1.0, 1.2),
    'atr_volatility_factor': (0.004, 0.015),
}


# ---------------------------------------------------------------------------
# Data loader with date slicing (cached)
# ---------------------------------------------------------------------------

tz = pytz.timezone("Asia/Kolkata")  # underlying data is Asia/Kolkata

@lru_cache(maxsize=1)
def load_data(start: str | None, end: str | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    if db_pg_sync is None:
        raise RuntimeError("db_pg_sync not available – cannot fetch Postgres data")

    start_ts = tz.localize(datetime.strptime(start, "%Y-%m-%d")) if start else tz.localize(datetime(1900, 1, 1))
    end_ts = tz.localize(datetime.strptime(end, "%Y-%m-%d")) if end else tz.localize(datetime(2100, 1, 1))

    print(f"📥 Fetching OHLCV from Postgres {start_ts.date()} → {end_ts.date()} …", flush=True)
    ce_df, pe_df = db_pg_sync.fetch_ohlcv_range(start_ts, end_ts)
    print(f"CE rows: {len(ce_df):,}, PE rows: {len(pe_df):,}")

    # ensure numeric dtypes
    num_cols = ["open", "high", "low", "close", "volume"]
    ce_df[num_cols] = ce_df[num_cols].astype("float64")
    pe_df[num_cols] = pe_df[num_cols].astype("float64")

    return ce_df.reset_index(drop=True), pe_df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Optuna objective
# ---------------------------------------------------------------------------

def objective(trial: optuna.Trial, start: str | None, end: str | None) -> float:
    params = {
        'fast_ema_period': trial.suggest_int('fast_ema_period', int(SEARCH_BOUNDS['fast_ema_period'][0]), int(SEARCH_BOUNDS['fast_ema_period'][1])),
        'slow_ema_period': trial.suggest_int('slow_ema_period', int(SEARCH_BOUNDS['slow_ema_period'][0]), int(SEARCH_BOUNDS['slow_ema_period'][1])),
        'rsi_period': trial.suggest_int('rsi_period', int(SEARCH_BOUNDS['rsi_period'][0]), int(SEARCH_BOUNDS['rsi_period'][1])),
        'atr_period': trial.suggest_int('atr_period', int(SEARCH_BOUNDS['atr_period'][0]), int(SEARCH_BOUNDS['atr_period'][1])),
        'vwap_period': trial.suggest_int('vwap_period', int(SEARCH_BOUNDS['vwap_period'][0]), int(SEARCH_BOUNDS['vwap_period'][1])),
        'rsi_oversold': trial.suggest_int('rsi_oversold', int(SEARCH_BOUNDS['rsi_oversold'][0]), int(SEARCH_BOUNDS['rsi_oversold'][1])),
        'rsi_overbought': trial.suggest_int('rsi_overbought', int(SEARCH_BOUNDS['rsi_overbought'][0]), int(SEARCH_BOUNDS['rsi_overbought'][1])),
        'volume_surge_factor': trial.suggest_float('volume_surge_factor', *SEARCH_BOUNDS['volume_surge_factor']),
        'atr_volatility_factor': trial.suggest_float('atr_volatility_factor', *SEARCH_BOUNDS['atr_volatility_factor']),
        'use_fast_ema': True,
        'use_slow_ema': True,
        'use_rsi': True,
        'use_atr': True,
        'use_vwap': True,
    }

    backtester = StrategyBacktester(strategy_params=params)
    ce, pe = load_data(start, end)
    t0 = time.time()
    res = backtester.backtest(ce, pe, initial_capital=INITIAL_CAPITAL)
    duration = time.time() - t0

    sharpe = res['combined']['sharpe_ratio']
    max_dd = abs(res['combined']['max_drawdown'])
    score = sharpe / (max_dd if max_dd else 1e-6)

    trial.set_user_attr('sharpe', sharpe)
    trial.set_user_attr('max_dd', max_dd)
    trial.set_user_attr('params', params)

    print(f"Trial {trial.number}: Sharpe={sharpe:.2f}, MaxDD={max_dd:.2f}%, Score={score:.4f}, time={duration:.1f}s", flush=True)
    return score


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Optuna optimiser with date range")
    parser.add_argument('--trials', type=int, default=50)
    parser.add_argument('--timeout', type=int, default=None)
    parser.add_argument('--start', type=str, default=None, help='YYYY-MM-DD inclusive')
    parser.add_argument('--end', type=str, default=None, help='YYYY-MM-DD inclusive')
    args = parser.parse_args()

    study = optuna.create_study(direction='maximize')
    pbar = tqdm(total=args.trials, desc='Trials', ncols=100)

    def cb(study: optuna.Study, trial: optuna.trial.FrozenTrial):
        pbar.update(1)
        pbar.set_postfix(score=f"{trial.value:.4f}", sharpe=f"{trial.user_attrs['sharpe']:.2f}")

    study.optimize(lambda t: objective(t, args.start, args.end), n_trials=args.trials, timeout=args.timeout, callbacks=[cb], n_jobs=1)
    pbar.close()

    best = study.best_trial
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_path = RESULT_DIR / f"best_params_range_{ts}.json"
    with open(out_path, 'w') as f:
        json.dump(best.user_attrs['params'], f, indent=4)

    print(f"\n✅ Saved best params to {out_path}\nScore={best.value:.4f}, Sharpe={best.user_attrs['sharpe']:.2f}, MaxDD={best.user_attrs['max_dd']:.2f}%")


if __name__ == '__main__':
    main()
