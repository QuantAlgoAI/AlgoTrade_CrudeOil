"""Bulk loader for historical option CSVs into PostgreSQL `ohlcv` table.

Usage::
    python load_csv_to_pg.py --csv_dir ../data/backtest

If DB credentials are in .env they are picked up automatically.
Only plain CSV with columns: timestamp,open,high,low,close,volume,option_type
are accepted.  If your combined files are named
`historical_data_ce.csv` or `historical_data_pe.csv` the option_type will be
deduced automatically.
"""
import argparse
import os
import glob
import logging
import sys
from typing import List

import pandas as pd
import psycopg2
from psycopg2 import sql
from psycopg2.extras import execute_values
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
load_dotenv()
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", 5432))
DB_NAME = os.getenv("DB_NAME", "quantalgo_db")

CONN_INFO = dict(
    user=DB_USER,
    password=DB_PASSWORD,
    host=DB_HOST,
    port=DB_PORT,
    dbname=DB_NAME,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ---------------------------------------------------------------------------

def find_csv_files(directory: str) -> List[str]:
    patterns = ["historical_data_ce.csv", "historical_data_pe.csv", "*_ce.csv", "*_pe.csv"]
    files: List[str] = []
    for ptn in patterns:
        files.extend(glob.glob(os.path.join(directory, ptn)))
    return files


def dataframe_iterator(df: pd.DataFrame, chunksize: int = 5000):
    total = len(df)
    for i in range(0, total, chunksize):
        yield df.iloc[i : i + chunksize]


def load_single_csv(path: str, conn):
    option_type = "CE" if "_ce" in path.lower() else "PE"
    logging.info("Loading %s (%s)", path, option_type)
    df = pd.read_csv(path)

    # ensure timestamp column is datetime
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"])

    # add option_type column so that COPY matches table structure
    df["option_type"] = option_type

    # reorder columns
    df = df[["timestamp", "option_type", "open", "high", "low", "close", "volume"]]

    with conn.cursor() as cur:
        # psycopg2 COPY expects file-like; use execute_values for portability
        # Convert numpy types to native Python (psycopg2 cannot adapt numpy types)
        records = [
            (
                row.timestamp.to_pydatetime(),
                row.option_type,
                float(row.open) if pd.notna(row.open) else None,
                float(row.high) if pd.notna(row.high) else None,
                float(row.low) if pd.notna(row.low) else None,
                float(row.close) if pd.notna(row.close) else None,
                int(row.volume) if pd.notna(row.volume) else None,
            )
            for row in df.itertuples(index=False)
        ]
        query = sql.SQL("INSERT INTO ohlcv (timestamp, option_type, open, high, low, close, volume) VALUES %s ON CONFLICT DO NOTHING")
        execute_values(cur, query, records, page_size=5000)
    conn.commit()
    logging.info("Inserted %d rows", len(df))


def main():
    parser = argparse.ArgumentParser(description="Bulk load historical option CSVs into Postgres.")
    parser.add_argument("--csv_dir", default=".", help="Directory containing CSV files")
    args = parser.parse_args()

    files = find_csv_files(args.csv_dir)
    if not files:
        logging.error("No CSVs found in %s", args.csv_dir)
        sys.exit(1)

    conn = psycopg2.connect(**CONN_INFO)
    try:
        for f in files:
            load_single_csv(f, conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
