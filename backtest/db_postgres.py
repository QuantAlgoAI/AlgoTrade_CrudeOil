"""Async PostgreSQL utilities dedicated to the back-testing module."""

import asyncio
from typing import Optional
import os
from contextlib import asynccontextmanager
from datetime import datetime
import asyncpg
import pandas as pd

# ---------------------------------------------------------------------------
# Environment configuration
# ---------------------------------------------------------------------------
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", 5432))
DB_NAME = os.getenv("DB_NAME", "quantalgo_db")

_POOL: Optional[asyncpg.pool.Pool] = None

# ---------------------------------------------------------------------------
# Connection Pool
# ---------------------------------------------------------------------------
async def get_pool() -> asyncpg.pool.Pool:
    global _POOL
    if _POOL is None:
        print("🔌 Creating DB pool...")
        _POOL = await asyncpg.create_pool(
            user=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME,
            min_size=1,
            max_size=10,
        )
    return _POOL


@asynccontextmanager
async def connection():
    pool = await get_pool()
    async with pool.acquire() as conn:
        yield conn

# ---------------------------------------------------------------------------
# Schema Creation
# ---------------------------------------------------------------------------
CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS ohlcv (
    timestamp TIMESTAMPTZ NOT NULL,
    option_type CHAR(2) NOT NULL,
    strike NUMERIC,
    expiry DATE,
    open  NUMERIC(18,6),
    high  NUMERIC(18,6),
    low   NUMERIC(18,6),
    close NUMERIC(18,6),
    volume BIGINT,
    PRIMARY KEY (timestamp, option_type, strike)
);
"""

async def ensure_schema():
    async with connection() as conn:
        await conn.execute(CREATE_TABLES_SQL)

# ---------------------------------------------------------------------------
# Fetch OHLCV
# ---------------------------------------------------------------------------
async def fetch_ohlcv(option_type: str, start: datetime, end: datetime) -> pd.DataFrame:
    query = """
        SELECT timestamp, open, high, low, close, volume
        FROM ohlcv
        WHERE option_type = $1 AND timestamp BETWEEN $2 AND $3
        ORDER BY timestamp
    """
    async with connection() as conn:
        records = await conn.fetch(query, option_type.upper(), start, end)
    df = pd.DataFrame(records, columns=["timestamp", "open", "high", "low", "close", "volume"])
    if not df.empty:
        df.set_index("timestamp", inplace=True)
    return df

# ---------------------------------------------------------------------------
# Async Test Runner
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Convenience sync wrapper for legacy sync code paths
# ---------------------------------------------------------------------------

def fetch_ohlcv_sync(option_type: str, start: str | datetime, end: str | datetime) -> pd.DataFrame:
    """Blocking wrapper around async fetch_ohlcv for sync callers."""
    return asyncio.run(fetch_ohlcv(option_type, start, end))

# ---------------------------------------------------------------------------
# Async Test Runner
# ---------------------------------------------------------------------------
async def main():
    print("🚀 Running async DB test...")

    await ensure_schema()
    print("✅ Schema ensured.")

    # Use actual datetime objects
    start = datetime(2025, 6, 3, 9, 0)
    end = datetime(2025, 6, 3, 15, 30)

    print(f"📦 Fetching OHLCV from {start} to {end}")

    ce_df = await fetch_ohlcv("CE", start, end)
    pe_df = await fetch_ohlcv("PE", start, end)

    if ce_df.empty:
        print("⚠️ No CE data found in that time range.")
    else:
        print("📈 Sample CE Data:")
        print(ce_df.head())

    if pe_df.empty:
        print("⚠️ No PE data found in that time range.")
    else:
        print("📉 Sample PE Data:")
        print(pe_df.head())


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("✅ Python script is executing!")
    asyncio.run(main())
