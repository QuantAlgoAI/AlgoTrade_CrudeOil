import asyncio
import os
import pandas as pd
from datetime import datetime
from db_postgres import get_pool, connection, ensure_schema

CSV_CE_PATH = "backtest/historical_data_ce.csv"
CSV_PE_PATH = "backtest/historical_data_pe.csv"

# 🧹 Clean old ohlcv data
async def truncate_ohlcv():
    async with connection() as conn:
        before = await conn.fetchval("SELECT COUNT(*) FROM ohlcv")
        print(f"🔍 Before TRUNCATE: {before} rows")
        await conn.execute("TRUNCATE ohlcv")
        after = await conn.fetchval("SELECT COUNT(*) FROM ohlcv")
        print(f"✅ After TRUNCATE: {after} rows")

def extract_strike_expiry_from_symbol(symbol: str):
    # Example: CRUDEOIL17JUL257700CE
    try:
        raw = symbol.replace("CRUDEOIL", "")
        expiry = raw[:7]  # e.g., 17JUL25
        strike = int(raw[7:-2])
        return strike, expiry
    except Exception:
        return None, None

# ⬆️ Insert CSV Data
async def insert_ohlcv(df: pd.DataFrame, option_type: str):
    if df.empty:
        print(f"⚠️ Empty DataFrame for {option_type}, skipping...")
        return

    df = df.copy()
    df["option_type"] = option_type

    # Extract strike and expiry from symbol
    df[["strike", "expiry"]] = df["symbol"].map(extract_strike_expiry_from_symbol).apply(pd.Series)
    df = df.dropna(subset=["strike", "expiry"]).copy()

    # Parse expiry
    df["expiry"] = pd.to_datetime(df["expiry"], format="%d%b%y", errors='coerce').dt.date

    # Ensure timestamp column is datetime
    if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
        df["timestamp"] = pd.to_datetime(df["timestamp"])

    # Drop potential duplicate keys
    df = df.drop_duplicates(subset=["timestamp", "option_type", "strike"]).copy()

    # Ensure proper column order and types
    df = df[["timestamp", "option_type", "strike", "expiry", "open", "high", "low", "close", "volume"]]

    async with connection() as conn:
        await conn.copy_records_to_table(
            "ohlcv",
            records=df.itertuples(index=False, name=None),
            columns=["timestamp", "option_type", "strike", "expiry", "open", "high", "low", "close", "volume"],
        )
        print(f"✅ Inserted {len(df)} rows for {option_type}")

# 🚀 Entry point
async def main():
    print("🚀 Starting CSV-to-Postgres sync...")
    await ensure_schema()
    await truncate_ohlcv()

    if not os.path.exists(CSV_CE_PATH):
        print(f"❌ File not found: {CSV_CE_PATH}")
        return
    ce_df = pd.read_csv(CSV_CE_PATH)
    await insert_ohlcv(ce_df, "CE")

    if not os.path.exists(CSV_PE_PATH):
        print(f"❌ File not found: {CSV_PE_PATH}")
        return
    pe_df = pd.read_csv(CSV_PE_PATH)
    await insert_ohlcv(pe_df, "PE")

    print("🎉 Sync complete.")

if __name__ == "__main__":
    asyncio.run(main())
