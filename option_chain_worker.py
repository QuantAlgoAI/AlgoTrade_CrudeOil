import json
import os
import time

import redis

from crudeoil_option_live_mcx import (
    nearest_expiry,
    fetch_chain,
    spot_price,
    process_chain,
    support_resistance,
    trend,
)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)

REFRESH_SEC = int(os.getenv("OC_REFRESH_SEC", 8))
KEY = "crudeoil:option_chain"


def build_snapshot():
    expiry = nearest_expiry()
    chain_df = fetch_chain(expiry)
    fut_ltp = float(chain_df["UnderlyingValue"].iloc[0]) if "UnderlyingValue" in chain_df.columns else spot_price()
    if fut_ltp < 100:
        fut_ltp *= 100  # raw price sometimes in smaller units

    rows, stats = process_chain(chain_df, fut_ltp, expiry)
    sup, res = support_resistance(rows)
    stats.update({
        "support": sup,
        "resistance": res,
        "trend": trend(stats["pcr"], stats["atm_pcr"]),
    })
    snapshot = {
        "expiry": expiry,
        "rows": rows,
        "stats": stats,
        "ts": int(time.time()),
    }
    return snapshot


def main():
    print(f"[worker] connected to Redis @ {REDIS_URL}, key '{KEY}', refresh {REFRESH_SEC}s")
    while True:
        start = time.time()
        try:
            snap = build_snapshot()
            redis_client.set(KEY, json.dumps(snap), ex=60)  # expire 1 min
            print(f"[worker] updated snapshot ({len(snap['rows'])} rows) in {time.time() - start:.2f}s")
        except Exception as e:
            print(f"[worker] error: {e}")
        time.sleep(REFRESH_SEC)


if __name__ == "__main__":
    main()
