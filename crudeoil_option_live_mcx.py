import asyncio
import math
from datetime import datetime
from typing import List, Dict, Tuple

import pandas as pd
from scipy.stats import norm

from mcxlib.market_data import (
    get_recent_expires,
    get_option_chain,
    get_market_watch,
)

# ------------------------- Terminal styles ---------------------------
RESET = "\033[0m"

def _wrap(code: str, txt: str) -> str:
    return f"\033[{code}m{txt}{RESET}"

def bold(t): return _wrap("1", t)

def cyan(t): return _wrap("96", t)

def yellow(t): return _wrap("93", t)

def green(t): return _wrap("92", t)

def red(t): return _wrap("91", t)

def gray(t): return _wrap("90", t)

# ------------------------- Helpers -----------------------------------

def round_nearest(x: float, step: int = 50) -> int:
    return int(round(x / step) * step)


def calculate_greeks(S, K, T, r, sigma, option_type="call"):
    if T <= 0 or sigma == 0:
        return 0, 0, 0, 0
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    delta = norm.cdf(d1) if option_type == "call" else -norm.cdf(-d1)
    gamma = norm.pdf(d1) / (S * sigma * math.sqrt(T))
    vega = S * norm.pdf(d1) * math.sqrt(T) / 100
    theta = (
        -S * norm.pdf(d1) * sigma / (2 * math.sqrt(T))
        - r * K * math.exp(-r * T) * (norm.cdf(d2) if option_type == "call" else norm.cdf(-d2))
    ) / 365
    return delta, gamma, theta, vega

# ------------------------- Core logic --------------------------------

def nearest_expiry() -> str:
    df_exp = get_recent_expires("CRUDEOIL")
    # ensure date ordering
    df_exp["_dt"] = pd.to_datetime(df_exp["Expiry"], format="%d%b%Y", errors="coerce")
    df_exp = df_exp.sort_values("_dt")
    for exp in df_exp["Expiry"]:
        try:
            oc = get_option_chain("CRUDEOIL", exp)
            if not oc.empty and ((oc.get("CE_OpenInterest", 0) + oc.get("PE_OpenInterest", 0)).sum() > 0):
                return exp
        except Exception:
            continue
    # fallback to first
    return df_exp.iloc[0]["Expiry"]


def spot_price() -> float:
    mw = get_market_watch()
    instr_col = "Instrument" if "Instrument" in mw.columns else (
        "InstrumentType" if "InstrumentType" in mw.columns else None
    )
    if instr_col:
        mask = (mw["Symbol"] == "CRUDEOIL") & (mw[instr_col].str.contains("FUT", na=False))
    else:
        mask = mw["Symbol"] == "CRUDEOIL"
    row = mw[mask]
    if row.empty:
        raise ValueError("CRUDEOIL future not found in market watch response")
    for col in ["LastRate", "Last", "LTP", "Price", "Close", "Sell", row.columns[-1]]:
        if col in row.columns:
            try:
                return float(row.iloc[0][col])
            except (ValueError, TypeError):
                continue
    raise ValueError("Numeric price not found in market watch response")


def fetch_chain(expiry: str) -> pd.DataFrame:
    return get_option_chain("CRUDEOIL", expiry)


def process_chain(df: pd.DataFrame, fut_ltp: float, expiry: str) -> Tuple[List[Dict], Dict]:
    atm = round_nearest(fut_ltp, 50)
    lower = atm - 500
    upper = atm + 500
    strike_col = "StrikePrice" if "StrikePrice" in df.columns else (
        "CE_StrikePrice" if "CE_StrikePrice" in df.columns else None
    )
    if strike_col is None:
        raise ValueError("Strike price column not found in option chain")
    subset = df[(df[strike_col].between(lower, upper))].copy()

    oi_data: List[Dict] = []
    ce_oi_tot = pe_oi_tot = 0
    atm_ce_oi = atm_pe_oi = 0

    high_ce = []
    high_pe = []
    ce_prem = {}
    pe_prem = {}

    T = max((datetime.strptime(expiry, "%d%b%Y") - datetime.now()).days, 1) / 365
    r = 0.06

    for _, row in subset.iterrows():
        strike = int(row[strike_col])
        ce_oi = int(row.get("CE_OpenInterest", 0))
        pe_oi = int(row.get("PE_OpenInterest", 0))
        ce_ltp = float(row.get("CE_LTP", 0))
        pe_ltp = float(row.get("PE_LTP", 0))
        ce_vol = int(row.get("CE_Volume", 0))
        pe_vol = int(row.get("PE_Volume", 0))
        iv_ce = float(row.get("CE_ImpliedVolatility", 22)) / 100
        iv_pe = float(row.get("PE_ImpliedVolatility", 22)) / 100

        delta_ce, gamma_ce, theta_ce, vega_ce = calculate_greeks(fut_ltp, strike, T, r, iv_ce, "call")
        delta_pe, gamma_pe, theta_pe, vega_pe = calculate_greeks(fut_ltp, strike, T, r, iv_pe, "put")

        oi_data.append({
            "Symbol": "CRUDEOIL", "Strike": strike,
            "CE OI": ce_oi,
            "PE OI": pe_oi,
            "CE IV%": round(iv_ce * 100, 1),
            "PE IV%": round(iv_pe * 100, 1),
            "CE Δ": round(delta_ce, 3),
            "PE Δ": round(delta_pe, 3),
            "Γ": round((gamma_ce + gamma_pe) / 2, 4),
            "Θ": round((theta_ce + theta_pe) / 2, 2),
            "Vega": round((vega_ce + vega_pe) / 2, 2),
            "PE Γ": round(gamma_pe, 4),
            "PE Θ": round(theta_pe, 2),
            "PE Vega": round(vega_pe, 2),
            "CE Vol": ce_vol,
            "PE Vol": pe_vol,
            "CE LTP": ce_ltp,
            "PE LTP": pe_ltp,
        })

        ce_prem[strike] = ce_ltp
        pe_prem[strike] = pe_ltp
        ce_oi_tot += ce_oi
        pe_oi_tot += pe_oi
        if ce_vol > 500:
            high_ce.append((strike, ce_vol))
        if pe_vol > 500:
            high_pe.append((strike, pe_vol))
        if strike == atm:
            atm_ce_oi = ce_oi
            atm_pe_oi = pe_oi

    stats = {
        "pcr": pe_oi_tot / ce_oi_tot if ce_oi_tot else 0,
        "atm_pcr": atm_pe_oi / atm_ce_oi if atm_ce_oi else 0,
        "high_ce": sorted(high_ce, key=lambda x: x[1], reverse=True),
        "high_pe": sorted(high_pe, key=lambda x: x[1], reverse=True),
        "ce_prem": ce_prem,
        "pe_prem": pe_prem,
        "atm": atm,
    }
    return oi_data, stats


def support_resistance(oi_data: List[Dict]) -> Tuple[int, int]:
    if not oi_data:
        return 0, 0
    highest_ce = max(oi_data, key=lambda x: x["CE OI"])
    highest_pe = max(oi_data, key=lambda x: x["PE OI"])
    return highest_pe["Strike"], highest_ce["Strike"]


def trend(pcr: float, atm_pcr: float) -> str:
    if pcr > 1.2 and atm_pcr > 1.1:
        return "BULLISH"
    if pcr < 0.7 and atm_pcr < 0.8:
        return "BEARISH"
    return "NEUTRAL"


def display(expiry: str, fut_ltp: float, oi_data: List[Dict], stats: Dict, sup: int, res: int):
    print(bold(cyan("\n🛢️  CRUDEOIL OPTION CHAIN (MCX)")))
    print(f"LTP: {fut_ltp:.2f} | Expiry: {expiry} | PCR: {stats['pcr']:.2f} | ATM PCR: {stats['atm_pcr']:.2f}")
    t = trend(stats['pcr'], stats['atm_pcr'])
    color = green if t == "BULLISH" else red if t == "BEARISH" else lambda x: x
    print(f"Trend: {color(t)}")
    df = pd.DataFrame(oi_data)
    if not df.empty:
        print(bold("\nOption Snapshot:"))
        print(df.sort_values("Strike").to_string(index=False))
    else:
        print(yellow("No option data in selected range."))
    print(bold("\nSupport / Resistance:"))
    print(f"Support: {yellow(sup)}  |  Resistance: {yellow(res)}")

# ------------------------- Async loop --------------------------------
async def live_loop(interval_sec: int = 10):
    while True:
        try:
            expiry = nearest_expiry()
            chain_df = fetch_chain(expiry)
            fut_ltp = float(chain_df["UnderlyingValue"].iloc[0]) if "UnderlyingValue" in chain_df.columns else spot_price()
            if fut_ltp < 100:
                fut_ltp *= 100
            oi_data, stats = process_chain(chain_df, fut_ltp, expiry)
            sup, res = support_resistance(oi_data)
            display(expiry, fut_ltp, oi_data, stats, sup, res)
        except Exception as e:
            print(red(f"Error: {e}"))
        for i in range(interval_sec, 0, -1):
            print(gray(f"\rRefreshing in {i}s"), end="")
            await asyncio.sleep(1)
        print()

if __name__ == "__main__":
    asyncio.run(live_loop())
