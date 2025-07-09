"""Synchronous Postgres helpers for Flask back-test endpoints.
Using psycopg2 connection pool avoids async-event-loop overhead.
"""

from typing import Tuple, Optional
import os
import io 
import pandas as pd
import psycopg2
from psycopg2.pool import SimpleConnectionPool
from dotenv import load_dotenv
from sqlalchemy import create_engine  # <-- SQLAlchemy for pandas read_sql
from datetime import datetime
import pytz  


# Load environment variables from .env
load_dotenv()

# Database config
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", 5432))
DB_NAME = os.getenv("DB_NAME", "quantalgo_db")

# SQLAlchemy engine (preferred for pandas)
ENGINE = create_engine(
    f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}",
    pool_size=10,
    max_overflow=0,
    pool_pre_ping=True,
)

# Connection pool (legacy psycopg2, still available if needed elsewhere)
_POOL: Optional[SimpleConnectionPool] = None

def _get_pool() -> SimpleConnectionPool:
    global _POOL
    if _POOL is None:
        _POOL = SimpleConnectionPool(
            1,
            10,
            user=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
        )
    return _POOL


def _copy_leg(conn, option_type: str, start_ts, end_ts) -> pd.DataFrame:
    """
    Stream CE / PE rows with Postgres binary COPY → PyArrow → pandas.
    Returns a DataFrame with parsed dtypes in ~1-2 s for 200 k rows.
    """
    buf = io.BytesIO()

    with conn.cursor() as cur:
        q = sql.SQL(
            "COPY ("
            " SELECT timestamp, strike, expiry, open, high, low, close, volume"
            " FROM ohlcv"
            " WHERE option_type = {opt}"
            "   AND timestamp BETWEEN {start} AND {end}"
            " ORDER BY timestamp"
            ") TO STDOUT (FORMAT binary)"
        ).format(
            opt=sql.Literal(option_type),
            start=sql.Literal(start_ts),
            end=sql.Literal(end_ts)
        )
        cur.copy_expert(q.as_string(cur), buf)

    buf.seek(0)
    # PyArrow reads the Postgres binary format via its Feather/IPC reader
    reader = pa_ipc.open_file(buf)
    table = reader.read_all()
    return table.to_pandas(timestamp_as_object=False)





def fetch_ohlcv_range(start_ts: datetime, end_ts: datetime) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """High-performance fetch of CE & PE data between start_ts and end_ts."""
    pool = _get_pool()
    conn = pool.getconn()
    try:
        ce_df = _copy_leg(conn, "CE", start_ts, end_ts)
        pe_df = _copy_leg(conn, "PE", start_ts, end_ts)
    finally:
        pool.putconn(conn)

    return ce_df, pe_df





"""Synchronous Postgres helpers for Flask back-test endpoints.
Using psycopg2 connection pool avoids async-event-loop overhead.
"""

from typing import Tuple, Optional
import os
import pandas as pd
import psycopg2
from psycopg2.pool import SimpleConnectionPool
from dotenv import load_dotenv
from sqlalchemy import create_engine  # <-- SQLAlchemy for pandas read_sql
from datetime import datetime
from datetime import datetime
import pytz  # <--- add this
from psycopg2 import sql 


# Load environment variables from .env
load_dotenv()

# Database config
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", 5432))
DB_NAME = os.getenv("DB_NAME", "quantalgo_db")

# SQLAlchemy engine (preferred for pandas)
ENGINE = create_engine(
    f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}",
    pool_size=10,
    max_overflow=0,
    pool_pre_ping=True,
)

# Connection pool (legacy psycopg2, still available if needed elsewhere)
_POOL: Optional[SimpleConnectionPool] = None

def _get_pool() -> SimpleConnectionPool:
    global _POOL
    if _POOL is None:
        _POOL = SimpleConnectionPool(
            1,
            10,
            user=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
        )
    return _POOL


def _copy_leg(conn, option_type: str, start_ts, end_ts) -> pd.DataFrame:
    """Stream CE/PE rows via COPY → pandas."""
    buf = io.StringIO()
    # build the SQL with mogrify so values are still safely quoted
    with conn.cursor() as cur:
        q = sql.SQL(
            "COPY ("
            " SELECT timestamp, strike, expiry, open, high, low, close, volume"
            " FROM ohlcv"
            " WHERE option_type = {opt}"
            "   AND timestamp BETWEEN {start} AND {end}"
            " ORDER BY timestamp"
            ") TO STDOUT WITH CSV HEADER"
        ).format(
            opt=sql.Literal(option_type),
            start=sql.Literal(start_ts),
            end=sql.Literal(end_ts)
        )
        cur.copy_expert(q.as_string(cur), buf)
    buf.seek(0)
    return pd.read_csv(buf, parse_dates=["timestamp"],
                       dtype={"strike": float, "volume": int})


def fetch_ohlcv_range(start_ts: datetime, end_ts: datetime) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """High-performance fetch of CE & PE data between start_ts and end_ts using COPY streaming."""
    pool = _get_pool()
    conn = pool.getconn()
    try:
        ce_df = _copy_leg(conn, "CE", start_ts, end_ts)
        pe_df = _copy_leg(conn, "PE", start_ts, end_ts)
    finally:
        pool.putconn(conn)

    return ce_df, pe_df


def get_date_range() -> Tuple[str, str]:
    pool = _get_pool()
    conn = pool.getconn()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM ohlcv")
        min_ts, max_ts = cursor.fetchone()
        return str(min_ts.date()), str(max_ts.date())
    finally:
        pool.putconn(conn)

def print_data_summary():
    """Print basic stats: timestamp range and row counts for CE and PE."""
    ce, pe = fetch_ohlcv_range(datetime(1900, 1, 1), datetime(2100, 1, 1))
    print(f"📈 CE: {ce['timestamp'].min()} → {ce['timestamp'].max()} | Rows: {len(ce):,}")
    print(f"📉 PE: {pe['timestamp'].min()} → {pe['timestamp'].max()} | Rows: {len(pe):,}")


# ✅ MAIN TEST BLOCK
if __name__ == "__main__":
    from datetime import datetime
    import pytz

    tz = pytz.timezone("Asia/Kolkata")
    start_ts = tz.localize(datetime(2025, 6, 3, 9, 0))
    end_ts = tz.localize(datetime(2025, 6, 3, 15, 30))

    ce, pe = fetch_ohlcv_range(start_ts, end_ts)

    print("\n✅ Fetch complete.")
    print("\n📈 CE Data (first 5 rows):")
    print(ce.head())

    print("\n📉 PE Data (first 5 rows):")
    print(pe.head())

    print("✅ Fetching CE/PE data summary from Postgres…")
    print_data_summary()
    # Add this:
    print("\n🗓️   Date range in DataBase: ")
    start_date, end_date = get_date_range()
    print(f"\n🧨 StartDate : {start_date} → EndDate : {end_date}")
