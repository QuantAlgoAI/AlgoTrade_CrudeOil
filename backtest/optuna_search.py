"""Hyper-parameter optimisation for HighWinRateStrategy using Optuna.

Runs the risk-managed back-tester and searches for the parameter set that
maximises Sharpe / |MaxDD| ratio (risk-adjusted).

Usage:
    python backtest/optuna_search.py --trials 100 --timeout 3600
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from functools import lru_cache
import time
from tqdm import tqdm

try:
    import optuna
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Optuna not installed. Please `pip install optuna` and rerun.") from exc

# Ensure parent path to import backtest module
import sys
from pathlib import Path as _P
sys.path.append(str(_P(__file__).resolve().parent))  # backtest directory in path

from backtest import StrategyBacktester  # noqa: E402


RESULT_DIR = Path('backtest_results/optuna')
RESULT_DIR.mkdir(parents=True, exist_ok=True)
INITIAL_CAPITAL = 100_000  # ₹

# --- Search Space (around winning Set-3) ---
SEARCH_BOUNDS = {
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


try:
    from backtest import db_pg_sync  # type: ignore
except ImportError:
    try:
        import db_pg_sync  # type: ignore
    except ImportError:
        db_pg_sync = None
        print("ℹ️ db_pg_sync not found.")
    else:
        db_pg_sync = db_pg_sync

try:
    from backtest import db_postgres  # type: ignore
except ImportError:
    try:
        import db_postgres  # type: ignore
    except ImportError:
        db_postgres = None
        print("ℹ️ db_postgres not found.")
    else:
        db_postgres = db_postgres

@lru_cache(maxsize=1)
def load_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return CE and PE OHLCV data.

    Priority:
    • Fetch from Postgres (sync helper first, async helper second).
      If both DB paths fail, raise an error – CSV fallback has been removed.
    The result is cached so each Optuna trial reuses the same DataFrames and
    avoids repeated database hits.
    """
    # 1) Try fast sync helper first
    if db_pg_sync is not None:
        try:
            print("📥 Fetching OHLCV from Postgres (sync)…", flush=True)
            ce, pe = db_pg_sync.fetch_ohlcv_range('1900-01-01', '2100-01-01')
            if not ce.empty and not pe.empty:
                print(f"CE rows: {len(ce):,}, PE rows: {len(pe):,}")
                return ce.reset_index(), pe.reset_index()
            print("⚠️  Sync fetch returned empty, trying async util.")
        except Exception as err:
            print(f"⚠️  Sync Postgres fetch failed ({err}), attempting async util.")

    # 2) Try async helper
    if db_postgres is not None:
        try:
            print("📥 Fetching OHLCV from Postgres…", flush=True)
            ce = db_postgres.fetch_ohlcv_sync("CE", "1900-01-01", "2100-01-01").reset_index()
            pe = db_postgres.fetch_ohlcv_sync("PE", "1900-01-01", "2100-01-01").reset_index()
            if not ce.empty and not pe.empty:
                print(f"CE rows: {len(ce):,}, PE rows: {len(pe):,}")
                numeric_cols = ['open', 'high', 'low', 'close', 'volume']
                ce[numeric_cols] = ce[numeric_cols].astype('float64')
                pe[numeric_cols] = pe[numeric_cols].astype('float64')
                return ce, pe
            print("⚠️  Async fetch returned empty, aborting.")
        except Exception as err:  # pragma: no cover
            print(f"❌ Async Postgres fetch failed ({err})")

    # If we reach this point, both DB paths failed
    raise RuntimeError("Unable to fetch OHLCV data from Postgres – aborting.")


def objective(trial: optuna.Trial) -> float:
    print(f"⇒ Trial {trial.number} starting", flush=True)
    params = {
        'fast_ema_period': trial.suggest_int('fast_ema_period', *SEARCH_BOUNDS['fast_ema_period']),
        'slow_ema_period': trial.suggest_int('slow_ema_period', *SEARCH_BOUNDS['slow_ema_period']),
        'rsi_period': trial.suggest_int('rsi_period', *SEARCH_BOUNDS['rsi_period']),
        'atr_period': trial.suggest_int('atr_period', *SEARCH_BOUNDS['atr_period']),
        'vwap_period': trial.suggest_int('vwap_period', *SEARCH_BOUNDS['vwap_period']),
        'use_fast_ema': True,
        'use_slow_ema': True,
        'use_rsi': True,
        'use_atr': True,
        'use_vwap': True,
        'rsi_oversold': trial.suggest_int('rsi_oversold', *SEARCH_BOUNDS['rsi_oversold']),
        'rsi_overbought': trial.suggest_int('rsi_overbought', *SEARCH_BOUNDS['rsi_overbought']),
        'volume_surge_factor': trial.suggest_float('volume_surge_factor', *SEARCH_BOUNDS['volume_surge_factor']),
        'atr_volatility_factor': trial.suggest_float('atr_volatility_factor', *SEARCH_BOUNDS['atr_volatility_factor']),
    }

    backtester = StrategyBacktester(strategy_params=params)
    ce_data, pe_data = load_data()
    start_time = time.time()
    results = backtester.backtest(ce_data, pe_data, INITIAL_CAPITAL)
    duration = time.time() - start_time

    sharpe = results['combined']['sharpe_ratio']
    max_dd = abs(results['combined']['max_drawdown'])

    # Custom objective: maximise Sharpe per unit drawdown (higher is better)
    score = sharpe / (max_dd if max_dd else 1e-6)
    print(f"⇐ Trial {trial.number} done in {duration:.1f}s → Sharpe={sharpe:.2f}, MaxDD={max_dd:.2f}%, Score={score:.4f}", flush=True)

    # Report additional metrics to dashboard
    trial.set_user_attr('total_return', results['combined']['total_return'])
    trial.set_user_attr('max_dd', max_dd)
    trial.set_user_attr('sharpe', sharpe)
    trial.set_user_attr('params', params)

    return score


def main(trials: int, timeout: int | None, resume: bool = False):
    """Run Optuna optimisation with a live tqdm progress bar."""
    # Prepare persistent storage for resume capability
    storage_path = RESULT_DIR / "optuna_study.db"
    storage_str = f"sqlite:///{storage_path}"

    if resume and storage_path.exists():
        print("🔄 Resuming existing study …", flush=True)
        study = optuna.load_study(study_name="strategy_opt", storage=storage_str)
    else:
        if not storage_path.exists():
            print("🆕 Creating new Optuna study …", flush=True)
        study = optuna.create_study(
            study_name="strategy_opt",
            direction="maximize",
            sampler=optuna.samplers.TPESampler(),
            pruner=optuna.pruners.MedianPruner(),
            storage=storage_str,
            load_if_exists=True,
        )
        # parameters set above if new study is created
        sampler=optuna.samplers.TPESampler(),
        pruner=optuna.pruners.MedianPruner(),
    

    existing_trials = len(study.trials)
    pbar = tqdm(total=trials, desc="Optuna Trials", ncols=100, unit="trial")

    def _update_bar(study: optuna.Study, trial: optuna.trial.FrozenTrial):  # noqa: ANN001
        pbar.update(1)
        pbar.set_postfix(
            score=f"{trial.value:.4f}",
            sharpe=f"{trial.user_attrs.get('sharpe', 0):.2f}",
            dd=f"{trial.user_attrs.get('max_dd', 0):.2f}%",
        )

    # Use single-core for cleaner sequential output; switch to mp.cpu_count() once happy.
    study.optimize(
        objective,
        n_trials=trials,
        timeout=timeout,
        n_jobs=1,
        callbacks=[_update_bar],
    )
    pbar.close()

    best = study.best_trial
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = RESULT_DIR / f"best_params_{timestamp}.json"
    with open(out_file, "w") as f:
        json.dump(best.user_attrs["params"], f, indent=4)

    print(
        f"\n✅ Best params saved to {out_file}\n"
        f"Score={best.value:.4f}, "
        f"Sharpe={best.user_attrs['sharpe']:.2f}, "
        f"MaxDD={best.user_attrs['max_dd']:.2f}%",
    )


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--trials', type=int, default=100, help='Number of Optuna trials')
    parser.add_argument('--timeout', type=int, default=None, help='Timeout in seconds')
    parser.add_argument('--resume', action='store_true', help='Resume from existing study.db')
    args = parser.parse_args()

    main(args.trials, args.timeout, args.resume)
