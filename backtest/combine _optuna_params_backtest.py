import json
from pathlib import Path
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor

# Adjust your paths & DB params
OPTUNA_RESULTS_DIR = Path('backtest/backtest_results/optuna')
DB_CONN_PARAMS = {
    'host': 'localhost',
    'port': 5432,
    'database': 'quantalgo_db',
    'user': 'postgres',
    'password': 'postgres',
}

def load_optuna_params():
    """Load all best_params_*.json files into a list of dicts with timestamps."""
    params_list = []
    for f in OPTUNA_RESULTS_DIR.glob('best_params_*.json'):
        with open(f) as jf:
            params = json.load(jf)
            timestamp = f.stem.split('_')[-1]
            params['timestamp'] = timestamp
            params_list.append(params)
    return params_list

def fetch_backtest_summary(conn, timestamp):
    """Fetch summary backtest results from DB for the given timestamp."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT *
            FROM backtest_result
            WHERE created_at::text LIKE %s
            ORDER BY created_at DESC
            LIMIT 1
        """, (f"%{timestamp}%",))
        return cur.fetchone()

def fetch_trades_for_backtest(conn, backtest_id):
    """Fetch trade logs for the given backtest_result id."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT *
            FROM trades
            WHERE backtest_id = %s
        """, (backtest_id,))
        return cur.fetchall()

def main():
    params_list = load_optuna_params()

    # Connect to DB
    conn = psycopg2.connect(**DB_CONN_PARAMS)

    combined_data = []

    for params in params_list:
        timestamp = params.pop('timestamp')

        backtest_summary = fetch_backtest_summary(conn, timestamp)
        if not backtest_summary:
            print(f"No backtest result found for timestamp {timestamp}")
            continue
        
        backtest_id = backtest_summary['id']
        trades = fetch_trades_for_backtest(conn, backtest_id)

        combined_data.append({
            'timestamp': timestamp,
            'params': params,
            'summary': backtest_summary,
            'trades': trades,
        })

    conn.close()

    # Convert combined_data to DataFrame or save as JSON/CSV for ML later
    df = pd.DataFrame([{
        'timestamp': d['timestamp'],
        **d['params'],
        'total_pnl': d['summary']['total_pnl'],
        'sharpe_ratio': d['summary']['sharpe_ratio'],
        'max_drawdown': d['summary']['max_drawdown'],
        'num_trades': d['summary']['total_trades'],
        # You can add more summary fields here
    } for d in combined_data])

    print(df.head())

    # Save combined dataset for next step (ML training)
    df.to_csv('combined_optuna_backtest_summary.csv', index=False)
    print("Saved combined summary to combined_optuna_backtest_summary.csv")

if __name__ == "__main__":
    main()
