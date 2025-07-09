import psycopg2
import pandas as pd
from tqdm import tqdm
from datetime import datetime

# Add your backtest import (adjust path if needed)
import sys
from pathlib import Path as _P
sys.path.append(str(_P(__file__).resolve().parent))  # backtest dir in path
from backtest import StrategyBacktester


def fetch_optuna_params(conn):
    query = "SELECT * FROM optuna_params ORDER BY trial_id"
    return pd.read_sql(query, conn)


def run_backtest_for_params(params):
    backtester = StrategyBacktester(strategy_params=params)
    # Load your CE/PE data files here (adjust path if needed)
    ce_data = pd.read_csv('backtest/historical_data_ce.csv', parse_dates=['timestamp'])
    pe_data = pd.read_csv('backtest/historical_data_pe.csv', parse_dates=['timestamp'])
    results = backtester.backtest(ce_data, pe_data, initial_capital=100_000)
    
    # Summary info for DB insert
    combined = results.get('combined', {})
    summary = {
        'total_trades': combined.get('total_trades', 0),
        'winning_trades': combined.get('winning_trades', 0),
        'losing_trades': combined.get('losing_trades', 0),
        'total_pnl': combined.get('total_pnl', 0.0),
        'max_drawdown': combined.get('max_drawdown', 0.0),
        'sharpe_ratio': combined.get('sharpe_ratio', 0.0),
    }
    return summary, results


def save_results_to_pg_batch(conn, strategy_name, results_list):
    with conn.cursor() as cur:
        for item in results_list:
            summary = item['summary']
            results = item['results']

            # Insert into backtest_result table
            cur.execute(
                """
                INSERT INTO backtest_result 
                (strategy_name, start_date, end_date, total_trades, winning_trades, losing_trades, total_pnl, max_drawdown, sharpe_ratio, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    strategy_name,
                    summary.get('start_date', None),
                    summary.get('end_date', None),
                    summary['total_trades'],
                    summary['winning_trades'],
                    summary['losing_trades'],
                    summary['total_pnl'],
                    summary['max_drawdown'],
                    summary['sharpe_ratio'],
                    datetime.now(),
                )
            )
            backtest_id = cur.fetchone()[0]

            # Insert into strategy_performance (optional, if exists)
            cur.execute(
                """
                INSERT INTO strategy_performance 
                (backtest_id, total_trades, winning_trades, losing_trades, total_pnl, max_drawdown, sharpe_ratio, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    backtest_id,
                    summary['total_trades'],
                    summary['winning_trades'],
                    summary['losing_trades'],
                    summary['total_pnl'],
                    summary['max_drawdown'],
                    summary['sharpe_ratio'],
                    datetime.now(),
                )
            )

            # Insert trades if available
            trades = results.get('combined', {}).get('trades', [])
            for trade in trades:
                cur.execute(
                    """
                    INSERT INTO trades
                    (backtest_id, option_type, entry_time, exit_time, entry_price, exit_price, qty, pnl, net_pnl, return, reason, type)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        backtest_id,
                        trade.get('option_type'),
                        trade.get('entry_time'),
                        trade.get('exit_time'),
                        trade.get('entry_price'),
                        trade.get('exit_price'),
                        trade.get('qty'),
                        trade.get('pnl'),
                        trade.get('net_pnl'),
                        trade.get('return'),
                        trade.get('reason'),
                        trade.get('type'),
                    )
                )

        conn.commit()


def main():
    conn = psycopg2.connect(
        dbname='quantalgo_db',
        user='postgres',
        password='postgres',
        host='localhost',
        port=5432,
    )

    df_params = fetch_optuna_params(conn)
    results_list = []

    for idx, row in tqdm(df_params.iterrows(), total=len(df_params), desc="Backtesting param sets"):
        # Convert row to dict and boolean columns fix
        param_dict = row.to_dict()
        # Fix boolean fields
        for bcol in ['use_fast_ema', 'use_slow_ema', 'use_rsi', 'use_atr', 'use_vwap']:
            if bcol in param_dict:
                param_dict[bcol] = bool(param_dict[bcol])

        # Remove trial_id and timestamp, bar_interval if unwanted
        param_dict.pop('trial_id', None)
        param_dict.pop('timestamp', None)
        param_dict.pop('bar_interval', None)

        summary, results = run_backtest_for_params(param_dict)
        # Add start_date and end_date for DB (if your backtester returns or you can hardcode)
        summary['start_date'] = results.get('start_date', None)
        summary['end_date'] = results.get('end_date', None)
        results_list.append({'summary': summary, 'results': results})

    save_results_to_pg_batch(conn, 'HighWinRateStrategy', results_list)
    conn.close()
    print("✅ Backtest results saved to PostgreSQL")


if __name__ == '__main__':
    main()
