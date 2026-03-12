"""
ruby_realworld.py — Ruby v4.0 Real-World Backtest
==================================================
Downloads the last 60 days of REAL BTC/USDT 1h candles from Binance US,
runs the full Ruby v4.0 entry/exit logic, and prints a detailed P&L report
including a comparison against a simple Buy-and-Hold strategy.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone

import ccxt
import pandas as pd
import pandas_ta as ta

# ─── Strategy Parameters ──────────────────────────────────────────────────────
SYMBOL           = "BTC/USDT"
EXCHANGE_ID      = "binanceus"
LOOKBACK_DAYS    = 60
TIMEFRAME        = "1h"

STARTING_BALANCE = 100.0
BASE_UNIT        = 1.00          # 1% of balance per unit
RSI_PERIOD       = 14
ST_LENGTH        = 10
ST_MULTIPLIER    = 3.0
RSI_THRESHOLD    = 35.0
EXTREME_FEAR_DROP = 0.05         # ≥5% 24h drop → Extreme Fear proxy
TP_PCT           = 0.03          # +3% take-profit
SL_PCT           = 0.02          # -2% stop-loss


# ═══════════════════════════════════════════════════════════════════════════════
# DATA FETCHING  (paginated so we always get the full 60 days)
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_ohlcv_paginated(exchange, symbol: str, timeframe: str,
                          since_ms: int) -> pd.DataFrame:
    """
    Pull OHLCV candles from Binance US in 1 000-bar pages until we reach
    the current time, then assemble into a single DataFrame.
    """
    all_candles: list = []
    fetch_since = since_ms

    print(f"📡 Fetching {timeframe} candles for {symbol} …", end="", flush=True)
    while True:
        batch = exchange.fetch_ohlcv(symbol, timeframe, since=fetch_since, limit=1000)
        if not batch:
            break
        all_candles.extend(batch)
        last_ts = batch[-1][0]
        print(".", end="", flush=True)

        # Stop if the final candle is within one bar of now
        interval_ms = exchange.parse_timeframe(timeframe) * 1000
        if last_ts + interval_ms >= exchange.milliseconds():
            break
        fetch_since = last_ts + 1
        time.sleep(exchange.rateLimit / 1000)

    print(f" {len(all_candles)} candles retrieved.")

    df = pd.DataFrame(
        all_candles,
        columns=["timestamp", "open", "high", "low", "close", "volume"],
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    df.set_index("timestamp", inplace=True)
    return df


# ═══════════════════════════════════════════════════════════════════════════════
# EXTREME FEAR PROXY  (daily 24h drop ≥ 5%)
# ═══════════════════════════════════════════════════════════════════════════════

def build_extreme_fear_set(df: pd.DataFrame) -> set:
    """
    For each calendar date, check if BTC dropped ≥5% from the previous day's
    closing price. Returns a set of date strings (YYYY-MM-DD) where Extreme
    Fear conditions held.
    """
    daily = df["close"].resample("1D").last().dropna()
    daily_pct = daily.pct_change()
    fear_dates = daily_pct[daily_pct <= -EXTREME_FEAR_DROP].index
    return {d.strftime("%Y-%m-%d") for d in fear_dates}


# ═══════════════════════════════════════════════════════════════════════════════
# BACKTEST ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

def run_backtest(df: pd.DataFrame, extreme_fear_dates: set) -> dict:
    """
    Apply Ruby v4.0 logic bar-by-bar on the real OHLCV data.
    Returns a performance dict and a list of trade records.
    """
    # Indicators
    rsi_series = ta.rsi(df["close"], length=RSI_PERIOD)
    st_df      = ta.supertrend(
        df["high"], df["low"], df["close"],
        length=ST_LENGTH, multiplier=ST_MULTIPLIER,
    )
    dir_col  = next(c for c in st_df.columns if c.startswith("SUPERTd_"))
    st_dir   = st_df[dir_col]

    balance      = STARTING_BALANCE
    in_position  = False
    entry_price  = 0.0
    entry_time   = None
    pos_dollars  = 0.0
    pos_units    = 0.0
    trades: list[dict] = []

    for i in range(len(df)):
        ts      = df.index[i]
        price   = df["close"].iloc[i]
        rsi_val = rsi_series.iloc[i]
        st_val  = st_dir.iloc[i]

        if pd.isna(rsi_val) or pd.isna(st_val):
            continue

        date_str = ts.strftime("%Y-%m-%d")

        # ── EXIT ──────────────────────────────────────────────────────────────
        if in_position:
            pct_chg = (price - entry_price) / entry_price

            if pct_chg >= TP_PCT:
                pnl     = pos_dollars * TP_PCT
                balance += pnl
                trades.append({
                    "entry_time":  entry_time,
                    "exit_time":   ts,
                    "entry_price": entry_price,
                    "exit_price":  price,
                    "units":       pos_units,
                    "allocated":   pos_dollars,
                    "pnl":         pnl,
                    "result":      "✅ WIN  (+3% TP)",
                })
                in_position = False

            elif pct_chg <= -SL_PCT:
                pnl     = -pos_dollars * SL_PCT
                balance += pnl
                trades.append({
                    "entry_time":  entry_time,
                    "exit_time":   ts,
                    "entry_price": entry_price,
                    "exit_price":  price,
                    "units":       pos_units,
                    "allocated":   pos_dollars,
                    "pnl":         pnl,
                    "result":      "❌ LOSS (-2% SL)",
                })
                in_position = False

        # ── ENTRY ─────────────────────────────────────────────────────────────
        if not in_position and rsi_val < RSI_THRESHOLD and int(st_val) == 1:
            units = 3.0 + 1.0                            # base + Supertrend
            if date_str in extreme_fear_dates:
                units += 1.3                             # Extreme Fear bonus
            pos_dollars  = min(units * BASE_UNIT * balance / 100.0, balance)
            pos_units    = units
            entry_price  = price
            entry_time   = ts
            in_position  = True

    # Force-close any open trade at end of data
    if in_position:
        final_price = df["close"].iloc[-1]
        pct_chg     = (final_price - entry_price) / entry_price
        pnl         = pos_dollars * pct_chg
        balance    += pnl
        trades.append({
            "entry_time":  entry_time,
            "exit_time":   df.index[-1],
            "entry_price": entry_price,
            "exit_price":  final_price,
            "units":       pos_units,
            "allocated":   pos_dollars,
            "pnl":         pnl,
            "result":      "🔄 OPEN→CLOSED (sim end)",
        })

    wins     = sum(1 for t in trades if t["pnl"] > 0)
    win_rate = (wins / len(trades) * 100) if trades else 0.0
    total_pnl = balance - STARTING_BALANCE

    return {
        "ending_balance": balance,
        "total_pnl":      total_pnl,
        "total_trades":   len(trades),
        "wins":           wins,
        "losses":         len(trades) - wins,
        "win_rate":       win_rate,
        "trades":         trades,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# BUY-AND-HOLD BENCHMARK
# ═══════════════════════════════════════════════════════════════════════════════

def buy_and_hold(df: pd.DataFrame) -> dict:
    first_price = df["close"].iloc[0]
    last_price  = df["close"].iloc[-1]
    btc_held    = STARTING_BALANCE / first_price
    final_value = btc_held * last_price
    pnl         = final_value - STARTING_BALANCE
    pct         = (pnl / STARTING_BALANCE) * 100
    return {
        "first_price": first_price,
        "last_price":  last_price,
        "btc_held":    btc_held,
        "final_value": final_value,
        "pnl":         pnl,
        "pct":         pct,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# REPORT PRINTER
# ═══════════════════════════════════════════════════════════════════════════════

def print_report(df: pd.DataFrame, result: dict, bah: dict,
                 extreme_fear_dates: set) -> None:
    W  = 68
    ts = lambda dt: dt.strftime("%b %d %Y  %H:%M UTC")

    print()
    print("═" * W)
    print("   💎  RUBY v4.0 — REAL-WORLD 60-DAY BACKTEST REPORT")
    print("═" * W)
    print(f"   Asset   : {SYMBOL}")
    print(f"   Period  : {df.index[0].strftime('%b %d %Y')}  →  {df.index[-1].strftime('%b %d %Y')}")
    print(f"   Bars    : {len(df):,}  ({TIMEFRAME} candles)")
    print(f"   Start $  : ${STARTING_BALANCE:.2f}")
    print("─" * W)

    # ── Trade Log ─────────────────────────────────────────────────────────────
    print(f"\n   🎯  RUBY'S SNIPER TRADES  ({result['total_trades']} total)\n")

    if result["trades"]:
        for idx, t in enumerate(result["trades"], 1):
            fear_tag = "  ⚠️ EXTREME FEAR ENTRY" if t["entry_time"].strftime("%Y-%m-%d") in extreme_fear_dates else ""
            print(f"   Trade #{idx}{fear_tag}")
            print(f"     Entry : {ts(t['entry_time'])}  @ ${t['entry_price']:,.2f}")
            print(f"     Exit  : {ts(t['exit_time'])}  @ ${t['exit_price']:,.2f}")
            print(f"     Units : {t['units']:.1f}  |  Allocated: ${t['allocated']:.3f}")
            pnl_sign = "+" if t["pnl"] >= 0 else ""
            print(f"     P&L   : {pnl_sign}${t['pnl']:.4f}   →  {t['result']}")
            print()
    else:
        print("   No trades triggered in this period.")
        print("   (RSI stayed above 35 or Supertrend was not BULLISH at the same time.)\n")

    # ── P&L Summary ───────────────────────────────────────────────────────────
    print("─" * W)
    print("   📊  PERFORMANCE SUMMARY\n")
    pnl_sign = "+" if result["total_pnl"] >= 0 else ""
    print(f"   Ending Balance : ${result['ending_balance']:.4f}")
    print(f"   Total P&L      : {pnl_sign}${result['total_pnl']:.4f}  "
          f"({pnl_sign}{result['total_pnl'] / STARTING_BALANCE * 100:.2f}%)")
    print(f"   Total Trades   : {result['total_trades']}")
    print(f"   Wins / Losses  : {result['wins']} / {result['losses']}")
    print(f"   Win Rate       : {result['win_rate']:.1f}%")

    # ── Buy-and-Hold Comparison ───────────────────────────────────────────────
    print()
    print("─" * W)
    print("   📈  BUY-AND-HOLD BENCHMARK  (bought $100 of BTC 60 days ago)\n")
    bah_sign = "+" if bah["pnl"] >= 0 else ""
    print(f"   BTC price (entry) : ${bah['first_price']:,.2f}")
    print(f"   BTC price (today) : ${bah['last_price']:,.2f}")
    print(f"   BTC held          : {bah['btc_held']:.6f} BTC")
    print(f"   Final Value       : ${bah['final_value']:.4f}")
    print(f"   Buy-Hold P&L      : {bah_sign}${bah['pnl']:.4f}  ({bah_sign}{bah['pct']:.2f}%)")

    # ── Head-to-Head ──────────────────────────────────────────────────────────
    print()
    print("─" * W)
    print("   ⚔️   HEAD-TO-HEAD\n")
    ruby_pct = result["total_pnl"] / STARTING_BALANCE * 100
    diff     = result["ending_balance"] - bah["final_value"]
    diff_sign = "+" if diff >= 0 else ""
    winner = "💎 Ruby" if result["ending_balance"] > bah["final_value"] else "📈 Buy & Hold"
    print(f"   Ruby ending balance    : ${result['ending_balance']:.4f}  ({'+' if ruby_pct>=0 else ''}{ruby_pct:.2f}%)")
    print(f"   Buy-Hold final value   : ${bah['final_value']:.4f}  ({bah_sign}{bah['pct']:.2f}%)")
    print(f"   Difference             : {diff_sign}${abs(diff):.4f}  →  {winner} wins")

    # ── Extreme Fear Days ─────────────────────────────────────────────────────
    if extreme_fear_dates:
        print()
        print("─" * W)
        sorted_fear = sorted(extreme_fear_dates)
        print(f"   ⚠️   EXTREME FEAR DAYS DETECTED ({len(sorted_fear)} days with ≥5% 24h drop)\n")
        for d in sorted_fear:
            print(f"   {d}")

    print()
    print("═" * W)
    print()


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    exchange = ccxt.binanceus({"enableRateLimit": True})

    since_dt = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS + 2)
    since_ms = int(since_dt.timestamp() * 1000)

    df = fetch_ohlcv_paginated(exchange, SYMBOL, TIMEFRAME, since_ms)

    # Trim to exactly the last 60 days
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=LOOKBACK_DAYS)
    df = df[df.index >= cutoff]

    if df.empty:
        print("❌ No data returned. Check your internet connection or exchange status.")
        sys.exit(1)

    print(f"✅ Data ready: {len(df)} bars from "
          f"{df.index[0].strftime('%b %d %Y')} to {df.index[-1].strftime('%b %d %Y')}\n")

    extreme_fear_dates = build_extreme_fear_set(df)
    print(f"⚠️  Extreme Fear days identified: {len(extreme_fear_dates)}\n")

    print("⚙️  Running Ruby v4.0 backtest …")
    result = run_backtest(df, extreme_fear_dates)

    bah = buy_and_hold(df)

    print_report(df, result, bah, extreme_fear_dates)


if __name__ == "__main__":
    main()
