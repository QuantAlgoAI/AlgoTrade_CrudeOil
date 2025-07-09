# Backtesting & Optuna Optimisation Guide

This short guide explains how the back-testing stack is organised and how to run / resume an Optuna hyper-parameter search.

---

## 1. Folder Structure

```
backtest/
├── backtest.py            # Risk-managed StrategyBacktester
├── db_pg_sync.py          # Fast synchronous Postgres fetch helpers
├── db_postgres.py         # Async Postgres helpers + sync wrapper
├── optuna_search.py       # Hyper-parameter search driver (this script)
└── results/
    └── optuna/
        ├── optuna_study.db        # ⬅ persistent SQLite study DB
        └── best_params_*.json     # best params snapshot per run
```

---

## 2. One-off Back-test

```powershell
# Example: run a single back-test with custom params
python - <<'PY'
from backtest.backtest import StrategyBacktester
from json import load

params = load(open('backtest_results/optuna/best_params_YYYYMMDD_HHMMSS.json'))
ce_df, pe_df = ...  # load cached Postgres data or call db_pg_sync.fetch_ohlcv_range
bt = StrategyBacktester(strategy_params=params)
results = bt.backtest(ce_df, pe_df, initial_capital=100_000)
print(results['combined'])
PY
```

The `StrategyBacktester` applies:
* ATR-based position sizing
* Stop-loss & trailing stop
* Daily loss-cap

All OHLCV data is pulled **directly from Postgres**—there is no CSV fallback.

---

## 3. Fresh Optuna Run

```powershell
# Run 100 trials (≈ 50–60 s per trial on the current machine)
python backtest/optuna_search.py --trials 100
```

* Progress is displayed via a live `tqdm` bar.
* After completion, the best params are written to `backtest_results/optuna/best_params_<timestamp>.json`.
* The full study (all trials) is stored in **SQLite** at `backtest_results/optuna/optuna_study.db`.

---

## 4. Resuming / Adding Trials

Once `optuna_study.db` exists you can append more trials at any time:

```powershell
# Append 200 more trials to the existing study
python backtest/optuna_search.py --trials 200 --resume
```

You may interrupt with `Ctrl-C`—no progress is lost. To impose a wall-clock cap:

```powershell
python backtest/optuna_search.py --trials 1000 --timeout 7200 --resume  # stop after 2 h
```

---

## 5. Quick Date-Range Optimisation

`optuna_search_range.py` lets you run fast experiments on a slice of data, which is handy when you don’t need the whole history:

```powershell
# one-week window, 50 trials
python backtest/optuna_search_range.py --trials 50 --start 2025-06-24 --end 2025-07-01
```

Arguments:
* `--start` / `--end` – inclusive `YYYY-MM-DD` bounds (omit for open-ended).
* `--trials`, `--timeout` – same as the main driver.

It writes `best_params_range_<timestamp>.json` to the same `backtest_results/optuna/` folder.

---

## 6. Consuming the Best Parameters

Typical workflow:

1. Pick the latest `best_params_*.json`.
2. Load it in the Strategy tab of the UI via `/strategy_params`, or hard-code as new defaults in `strategy.py`.
3. Re-run a final validation back-test or start paper/live trading.

---

## 7. Key Modules Quick Reference

| File | Purpose |
|------|---------|
| **backtest.py** | Contains `StrategyBacktester`. Core risk management logic & performance metrics. |
| **db_pg_sync.py** | Fast synchronous PG helper (`fetch_ohlcv_range`) used by Optuna to minimise overhead. |
| **db_postgres.py** | Async PG utilities + sync wrapper. Can be used in other async contexts. |
| **optuna_search.py** | Orchestrates Optuna, pulls data via helpers, scores trials and saves results. |

---

## 8. Directory structure & housekeeping

All optimisation and back-test artefacts now live under a single tree at project-root:

```
backtest_results/
  optuna/
    bars_5s/
      best_params_bars_5s_<timestamp>.json
    bars_1m/
      best_params_bars_1m_<timestamp>.json
  backtests/
    compare_results_*.csv / .json / .pdf / _detail.pdf
```

The helper scripts automatically create these folders; you can safely delete or archive runs by removing their sub-folders.

### 9. Timeframe optimisation across multiple materialised views in Postgres expose multiple granularities: 5-second, 10-second, 30-second, 1-minute, 5-minute.
To discover which timeframe suits the strategy best, run **one Optuna search per view**:

```powershell
# 50 trials on each interval
python backtest/optuna_from_psql.py --interval bars_5s  --trials 50
python backtest/optuna_from_psql.py --interval bars_10s --trials 50
python backtest/optuna_from_psql.py --interval bars_30s --trials 50
python backtest/optuna_from_psql.py --interval bars_1m  --trials 50  # 1-minute (already used above)
python backtest/optuna_from_psql.py --interval bars_5m  --trials 50
```

Each run saves `best_params_<interval>_<timestamp>.json` under `backtest_results/optuna/`.

---

## 9. Validation & Equity-Curve Comparison

`backtest_optuna_params.py` compares an arbitrary set of parameter files over a common date window, captures
key metrics and draws the equity curves.

```powershell
python backtest_optuna_params.py `
    --params backtest_results/optuna/best_params_*s*.json `
    --start 2025-06-24 --end 2025-07-01 `
    --out timeframe_comparison
```

Outputs:

* `timeframe_comparison.csv`  – tabular metrics (net profit, Sharpe, draw-down, trade count, etc.)
* `timeframe_comparison.json` – same in JSON.
* `timeframe_comparison.pdf`  – equity-curve plot for quick visual inspection.

Additional assumptions baked into the script:

* Commission **₹75 per round trade** (buy + sell).
* **1 lot = 100 units**; the report includes average P/L **per lot**.
* The start/end dates are echoed in every metrics row.

Examine the CSV or console output to decide which timeframe delivers the highest Sharpe ratio / profit factor.



# compare *all* Optuna-best files you have
python backtest_optuna_params.py `
    --params backtest_results/optuna/best_params_*.json `
    --start 2025-06-24 --end 2025-07-01 `
    --out timeframe_comparison
---


python backtest_optuna_params.py `
    --params backtest_results/optuna/best_params_bars_1m_20250706_014242.json `
            backtest_results/optuna/best_params_bars_5s_20250706_021500.json `
    --start 2025-06-24 --end 2025-07-01


python backtest/optuna_from_psql.py --interval bars_5s --trials 50

**What this does**
1. Connects to PostgreSQL and reads the 5-second materialised view `bars_5s`.
2. Runs an Optuna study for **50 trials**, each trial proposing new strategy parameters and scoring them by Sharpe ratio.
3. Persists the best parameter set to:
   `backtest_results/optuna/bars_5s/best_params_bars_5s_<timestamp>.json`
4. Validate or compare the saved JSON with:
   ```powershell
   python backtest/scripts/backtest_optuna_params.py \
     --params backtest_results/optuna/bars_5s/best_params_bars_5s_*.json \
     --start 2025-06-24 --end 2025-07-01
   ```


Happy optimisation & profitable trading!
