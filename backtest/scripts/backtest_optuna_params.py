"""Compare one or more Optuna parameter JSON files over a given date window.

Outputs generated in the working directory (basename can be customised with --out):
1. CSV  – tabular performance metrics for each parameter file.
2. JSON – same metrics in JSON format.
3. PDF  – equity-curve comparison plot.

Assumptions:
• Commission ₹75 per round trade (buy+sell).
• 1 lot = 100 units (barrels/shares).  Average P/L per lot is reported.

Example:
    python backtest_optuna_params.py \
        --params backtest_results/optuna/best_params_*.json \
        --start 2025-06-24 --end 2025-07-01 \
        --out compare_jun24_1w
"""
from __future__ import annotations

import argparse
import glob
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import matplotlib.pyplot as plt
import pandas as pd
import pytz
from tqdm import tqdm

# Local imports
from backtest.backtest import StrategyBacktester
from backtest import db_pg_sync

# ---------------------------------------------------------------------------
# Time zone helpers
# ---------------------------------------------------------------------------

tz = pytz.timezone("Asia/Kolkata")

def parse_date(ds: str | None):
    if not ds:
        return None
    return tz.localize(datetime.strptime(ds, "%Y-%m-%d"))

# ---------------------------------------------------------------------------
# Metric extraction
# ---------------------------------------------------------------------------

NUMERIC_FIELDS = [
    "net_profit",
    "total_return",
    "sharpe_ratio",
    "max_drawdown",
    "profit_factor",
    "win_rate",
    "volatility",
]


def metrics_from_combined(combined: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten combined dictionary from StrategyBacktester."""
    m: Dict[str, Any] = {k: (combined[k].item() if hasattr(combined[k], "item") else combined[k]) for k in NUMERIC_FIELDS}

    trades: List[Dict[str, Any]] = combined.get("trades", [])
    m["round_trades"] = len(trades)
    m["commission"] = len(trades) * 75  # ₹75 per round trip

    # Average net P/L per lot (100 units)
    per_lot_pnls: List[float] = []
    for t in trades:
        lots = (t.get("qty", 0) or 0) / 100.0
        if lots:
            per_lot_pnls.append(t["net_pnl"] / lots)
    m["avg_pnl_per_lot"] = sum(per_lot_pnls) / len(per_lot_pnls) if per_lot_pnls else 0
    return m

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Compare Optuna parameter files via back-test")
    ap.add_argument("--params", nargs="+", required=True, help="JSON files or glob patterns")
    ap.add_argument("--start", type=str, help="YYYY-MM-DD (inclusive)")
    ap.add_argument("--end", type=str, help="YYYY-MM-DD (inclusive)")
    ap.add_argument("--out", default="compare_results", help="basename for output files")
    args = ap.parse_args()

    # Resolve parameter file list
    param_files: List[str] = []
    for pattern in args.params:
        matches = sorted(glob.glob(pattern))
        if not matches:
            print(f"⚠ No matches for pattern: {pattern}")
        param_files.extend(matches)
    if not param_files:
        raise SystemExit("No parameter files found. Aborting.")

    start_ts = parse_date(args.start) or tz.localize(datetime(1900, 1, 1))
    end_ts = parse_date(args.end) or tz.localize(datetime(2100, 1, 1))

    print(f"📥 Fetching CE/PE data {start_ts.date()} → {end_ts.date()} …")
    ce_df, pe_df = db_pg_sync.fetch_ohlcv_range(start_ts, end_ts)
    print(f"Rows – CE: {len(ce_df):,}, PE: {len(pe_df):,}\n")

    metrics_rows: List[Dict[str, Any]] = []

    plt.figure(figsize=(11, 6))

    for pf in tqdm(param_files, desc="Back-testing"):
        params = json.load(open(pf))
        name = Path(pf).stem

        bt = StrategyBacktester(strategy_params=params)
        res = bt.backtest(ce_df, pe_df, initial_capital=100_000)
        combined = res["combined"]

        # Plot equity curve
        equity = combined["equity_curve"]
        equity.plot(label=name)

        row = metrics_from_combined(combined)
        row["file"] = Path(pf).name
        metrics_rows.append(row)

    # Plot formatting
    plt.title("Equity Curve Comparison")
    plt.xlabel("Timestamp")
    plt.ylabel("Equity (₹)")
    plt.legend()
    plt.tight_layout()

    out_base = Path(args.out)
    csv_path = out_base.with_suffix(".csv")
    json_path = out_base.with_suffix(".json")
    pdf_path = out_base.with_suffix(".pdf")

    # attach timeframe info
    for row in metrics_rows:
        row["start_date"] = start_ts.date().isoformat()
        row["end_date"] = end_ts.date().isoformat()

    df_metrics = pd.DataFrame(metrics_rows)
    df_metrics.to_csv(csv_path, index=False)
    df_metrics.to_json(json_path, orient="records", indent=2)
    plt.savefig(pdf_path)

    # ------------------------------------------------------------------
    # Detailed PDF report (equity + trades table for each param file)
    # ------------------------------------------------------------------
    from matplotlib.backends.backend_pdf import PdfPages
    detailed_pdf = out_base.parent / f"{out_base.stem}_detail.pdf"
    with PdfPages(detailed_pdf) as pdf:
        # cover page – equity comparison
        pdf.savefig(plt.gcf())
        plt.close()

        for row in metrics_rows:
            fname = row["file"]
            param_path = Path(fname)
            if not param_path.is_file():
                alt1 = Path("backtest_results/optuna") / param_path.name
                alt2 = Path("backtest/backtest_results/optuna") / param_path.name
                if alt1.is_file():
                    param_path = alt1
                elif alt2.is_file():
                    param_path = alt2
                else:
                    # try optuna/<any_interval>/file
                    optuna_root = Path("backtest_results/optuna")
                    for p in optuna_root.rglob(param_path.name):
                        param_path = p
                        break

            params = json.load(open(param_path))
            # re-run backtest quickly to get trades (could cache earlier)
            bt = StrategyBacktester(strategy_params=params)
            res = bt.backtest(ce_df, pe_df, initial_capital=100_000)
            trades_df = pd.DataFrame(res["combined"]["trades"])

            fig, ax = plt.subplots(figsize=(11, 6))
            fig.suptitle(f"Trade Detail – {fname}")
            ax.axis("off")

            # show aggregated metrics on top
            text = "\n".join([
                f"Net Profit: ₹{row['net_profit']:,}",
                f"Total Return: {row['total_return']:.2f}%",
                f"Sharpe Ratio: {row['sharpe_ratio']:.2f}",
                f"Max DD: {row['max_drawdown']:.2f}%",
                f"Trades: {row['round_trades']}\n",
                "First 20 trades (₹75 commission already deducted):",
            ])
            ax.text(0, 1, text, va="top", family="monospace")

            # create small table below
            tbl_df = trades_df[["entry_time", "exit_time", "entry_price", "exit_price", "qty", "net_pnl"]].head(20)
            table = ax.table(cellText=tbl_df.values,
                              colLabels=tbl_df.columns,
                              loc='center')
            table.auto_set_font_size(False)
            table.set_fontsize(6)
            table.scale(1, 1.6)
            pdf.savefig(fig)
            plt.close(fig)

    print("  •", detailed_pdf)


    print("\n✅ Outputs saved:")
    for p in [csv_path, json_path, pdf_path]:
        print(" •", p)

    # Brief view
    print(f"\nSummary (timeframe {start_ts.date()} → {end_ts.date()}):")
    print(df_metrics[["file", "net_profit", "total_return", "sharpe_ratio", "max_drawdown", "round_trades", "avg_pnl_per_lot"]])

if __name__ == "__main__":
    main()
