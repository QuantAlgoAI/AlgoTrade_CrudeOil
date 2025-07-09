"""Run Optuna hyper-parameter optimisation using OHLCV data that lives
in PostgreSQL materialised views (bars_1m by default).

Placed in backtest/ so it is not blocked by the .gitignore rule that
skips backtest_results/.  Execute:

    python backtest/optuna_from_psql.py --trials 100 --interval bars_1m
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from datetime import datetime

import optuna  # type: ignore
import pandas as pd
from sqlalchemy import create_engine

# ---------------------------------------------------------------------------
# Local imports – ensure project root is on path so we can reach backtester
# ---------------------------------------------------------------------------
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

import importlib.util

# locate backtest.py regardless of current folder depth
bt_path = (ROOT / "backtest.py") if (ROOT / "backtest.py").exists() else (ROOT / "backtest" / "backtest.py")
spec = importlib.util.spec_from_file_location("bt_module", bt_path)
_bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_bt)  # type: ignore
StrategyBacktester = _bt.StrategyBacktester

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
parser = argparse.ArgumentParser(description="Optuna optimisation via Postgres")
parser.add_argument("--trials", type=int, default=50, help="number of Optuna trials")
parser.add_argument(
    "--interval",
    default="bars_1m",
    choices=["bars_1s", "bars_5s", "bars_1m", "bars_5m"],
    help="name of the materialised-view to optimise on",
)
parser.add_argument(
    "--db-uri",
    default="postgresql+psycopg2://postgres:postgres@localhost:5432/quantalgo_db",
    help="SQLAlchemy DB URI",
)
args = parser.parse_args()

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
engine = create_engine(args.db_uri, pool_pre_ping=True)

NUMERIC_COLS = ["open", "high", "low", "close", "volume"]

def load_data(view: str) -> pd.DataFrame:
    """Fetch OHLCV from Postgres and rename columns expected by backtester."""
    df = pd.read_sql(
        f"""
        SELECT
            bar_time                AS timestamp,
            open, high, low, close,
            vol                     AS volume
        FROM {view}
        ORDER BY bar_time
        """,
        engine,
    )
    df[NUMERIC_COLS] = df[NUMERIC_COLS].astype("float64")
    return df

# ---------------------------------------------------------------------------
# Back-test wrapper
# ---------------------------------------------------------------------------

def run_backtest(df: pd.DataFrame, params: dict) -> float:
    """Return Sharpe ratio from StrategyBacktester for given params."""
    bt = StrategyBacktester(strategy_params=params)
    res = bt.backtest(df.copy(), df.copy())
    return res["combined"]["sharpe_ratio"]

# ---------------------------------------------------------------------------
# Optuna objective
# ---------------------------------------------------------------------------


def objective(trial: optuna.Trial) -> float:
    params = {
        "fast_ema_period": trial.suggest_int("fast_ema_period", 2, 20),
        "slow_ema_period": trial.suggest_int("slow_ema_period", 10, 60),
        "rsi_period": trial.suggest_int("rsi_period", 6, 20),
        "atr_period": trial.suggest_int("atr_period", 6, 20),
        "vwap_period": trial.suggest_int("vwap_period", 10, 30),
        "rsi_oversold": trial.suggest_int("rsi_oversold", 20, 45),
        "rsi_overbought": trial.suggest_int("rsi_overbought", 55, 80),
        "volume_surge_factor": trial.suggest_float("volume_surge_factor", 1.0, 2.0),
        "atr_volatility_factor": trial.suggest_float("atr_volatility_factor", 0.001, 0.02),
        "use_fast_ema": True,
        "use_slow_ema": True,
        "use_rsi": True,
        "use_atr": True,
        "use_vwap": True,
    }
    df = load_data(args.interval)
    return run_backtest(df, params)

# ---------------------------------------------------------------------------
# Run optimisation
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    study = optuna.create_study(direction="maximize")
    print(f"Starting optimisation on {args.interval} – trials: {args.trials}")
    study.optimize(objective, n_trials=args.trials, show_progress_bar=True)

    print("Best value :", study.best_value)
    print("Best params:")
    print(json.dumps(study.best_params, indent=4))

    # persist to project-level backtest_results / optuna / <interval>
    project_root = ROOT.parent  # one level above backtest/
    result_dir = project_root / "backtest_results" / "optuna" / args.interval
    result_dir.mkdir(parents=True, exist_ok=True)
    fname = result_dir / f"best_params_{args.interval}_{datetime.now():%Y%m%d_%H%M%S}.json"
    fname.write_text(json.dumps(study.best_params, indent=4))
    print(f"Saved to {fname}")
