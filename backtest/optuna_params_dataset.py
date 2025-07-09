import json
from pathlib import Path
import pandas as pd


def load_optuna_results(base_path: str = 'backtest/backtest_results/optuna') -> pd.DataFrame:
    base = Path(base_path)
    param_files = list(base.rglob("best_params_*.json"))

    if not param_files:
        raise FileNotFoundError("No Optuna result files found.")

    rows = []
    for file in param_files:
        with open(file, 'r') as f:
            params = json.load(f)
        params['file_name'] = file.name
        params['timestamp'] = file.stem.replace('best_params_', '')
        params['bar_interval'] = file.parent.name
        rows.append(params)

    df = pd.DataFrame(rows)
    df.insert(0, 'trial_id', range(1, len(df) + 1))
    print(f"✅ Loaded {len(df)} parameter sets from {base_path}")
    return df


if __name__ == "__main__":
    df = load_optuna_results()
    print(df.head())
