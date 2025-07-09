import psycopg2
from datetime import datetime

# -------------------------
# 💡 Best Optuna Parameters
# -------------------------
params = {
    "fast_ema_period": 2,
    "slow_ema_period": 10,
    "rsi_period": 8,
    "atr_period": 6,
    "vwap_period": 18,
    "use_fast_ema": True,
    "use_slow_ema": True,
    "use_rsi": True,
    "use_atr": True,
    "use_vwap": True,
    "rsi_oversold": 39,
    "rsi_overbought": 74,
    "volume_surge_factor": 1.166226893223003,
    "atr_volatility_factor": 0.005555243602297695
}

# -------------------------
# 📊 Backtest Result Metrics
# -------------------------
metrics = {
    "total_pnl": 17890.0,
    "max_drawdown": -0.052,
    "sharpe_ratio": 1.28,
    "total_trades": 90,
    "winning_trades": 62,
    "losing_trades": 28
}

# -------------------------
# 🛠 PostgreSQL Connection
# -------------------------
conn = psycopg2.connect(
    dbname="algotrade_db",
    user="postgres",
    password="postgres",
    host="localhost",
    port="5432"
)
cur = conn.cursor()

# -------------------------
# 🚀 INSERT INTO backtest_result
# -------------------------
cur.execute("""
    INSERT INTO backtest_result (
        strategy_id, start_date, end_date,
        total_trades, winning_trades, losing_trades,
        total_pnl, max_drawdown, sharpe_ratio,
        fast_ema_period, slow_ema_period, rsi_period,
        atr_period, vwap_period,
        use_fast_ema, use_slow_ema, use_rsi, use_atr, use_vwap,
        rsi_oversold, rsi_overbought,
        volume_surge_factor, atr_volatility_factor,
        created_at
    ) VALUES (
        %s, %s, %s,
        %s, %s, %s,
        %s, %s, %s,
        %s, %s, %s,
        %s, %s,
        %s, %s, %s, %s, %s,
        %s, %s,
        %s, %s,
        %s
    )
""", (
    1,  # strategy_id (ensure this exists in your `strategy` table)
    datetime(2025, 6, 1),
    datetime(2025, 6, 30),
    metrics["total_trades"],
    metrics["winning_trades"],
    metrics["losing_trades"],
    metrics["total_pnl"],
    metrics["max_drawdown"],
    metrics["sharpe_ratio"],
    params["fast_ema_period"],
    params["slow_ema_period"],
    params["rsi_period"],
    params["atr_period"],
    params["vwap_period"],
    params["use_fast_ema"],
    params["use_slow_ema"],
    params["use_rsi"],
    params["use_atr"],
    params["use_vwap"],
    params["rsi_oversold"],
    params["rsi_overbought"],
    params["volume_surge_factor"],
    params["atr_volatility_factor"],
    datetime.now()
))

conn.commit()
cur.close()
conn.close()

print("✅ Optuna trial inserted into backtest_result table.")
