"""
ruby_multiverse.py — Ruby v4.0 Monte Carlo Simulator
=====================================================
Runs 10 independent 60-day paper trading simulations on synthetic
BTC price data, applying the full Ruby v4.0 entry/exit logic, then
prints a summary report table to the console.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pandas_ta as ta

# ─── Simulation Parameters ────────────────────────────────────────────────────
NUM_SIMULATIONS  = 10
DAYS             = 60
BARS_PER_DAY     = 24 * 4          # 15-minute intervals → 96 bars/day
TOTAL_BARS       = DAYS * BARS_PER_DAY   # 5 760 bars per sim
START_PRICE      = 69_000.0
VOLATILITY       = 0.0015          # 0.15% per bar (random walk σ)

# ─── Ruby v4.0 Logic Constants ────────────────────────────────────────────────
STARTING_BALANCE = 100.0
BASE_UNIT        = 1.00            # 1% of balance per unit
RSI_PERIOD       = 14
ST_LENGTH        = 10
ST_MULTIPLIER    = 3.0
RSI_OVERSOLD     = 35.0
TP_PCT           = 0.03            # +3% take-profit
SL_PCT           = 0.02            # -2% stop-loss


# ═══════════════════════════════════════════════════════════════════════════════
# DATA GENERATORS
# ═══════════════════════════════════════════════════════════════════════════════

def generate_price_df(seed: int) -> pd.DataFrame:
    """
    Build a synthetic 60-day OHLCV DataFrame using a Gaussian random walk.
    High/Low are generated with small intra-bar noise so Supertrend has
    realistic band calculations.
    """
    rng = np.random.default_rng(seed)
    bar_returns = rng.normal(0.0, VOLATILITY, TOTAL_BARS)
    close = START_PRICE * np.cumprod(1.0 + bar_returns)

    # Intra-bar noise for high/low (independent of the close noise)
    hl_noise = rng.uniform(0.0005, VOLATILITY * 1.5, TOTAL_BARS)
    high  = close * (1.0 + hl_noise)
    low   = close * (1.0 - hl_noise)
    open_ = np.empty_like(close)
    open_[0]  = START_PRICE
    open_[1:] = close[:-1]

    return pd.DataFrame({
        "open":   open_,
        "high":   high,
        "low":    low,
        "close":  close,
        "volume": rng.uniform(100.0, 1_000.0, TOTAL_BARS),
    })


def generate_fg_series(seed: int) -> np.ndarray:
    """
    One Fear & Greed value per simulated day (0-100), held constant
    for all 96 bars of that day — mirrors the real API's daily cadence.
    """
    rng = np.random.default_rng(seed + 9999)
    return rng.integers(5, 95, DAYS)


# ═══════════════════════════════════════════════════════════════════════════════
# SINGLE SIMULATION
# ═══════════════════════════════════════════════════════════════════════════════

def run_simulation(sim_index: int) -> dict:
    """
    Run one 60-day simulation with a unique random seed.
    Returns a dict of performance metrics.
    """
    seed = sim_index * 137          # deterministic but varied seeds

    df       = generate_price_df(seed)
    fg_daily = generate_fg_series(seed)

    # ── Indicators ────────────────────────────────────────────────────────────
    rsi_series = ta.rsi(df["close"], length=RSI_PERIOD)

    st_df      = ta.supertrend(
        df["high"], df["low"], df["close"],
        length=ST_LENGTH, multiplier=ST_MULTIPLIER,
    )
    dir_col    = next(c for c in st_df.columns if c.startswith("SUPERTd_"))
    st_dir     = st_df[dir_col]

    # ── State ─────────────────────────────────────────────────────────────────
    balance          = STARTING_BALANCE
    in_position      = False
    entry_price      = 0.0
    position_dollars = 0.0
    total_trades     = 0
    wins             = 0

    # ── Bar loop ──────────────────────────────────────────────────────────────
    for i in range(TOTAL_BARS):
        rsi_val = rsi_series.iloc[i]
        st_val  = st_dir.iloc[i]

        # Skip until both indicators have warmed up
        if pd.isna(rsi_val) or pd.isna(st_val):
            continue

        price   = df["close"].iloc[i]
        day_idx = min(i // BARS_PER_DAY, DAYS - 1)
        fg      = int(fg_daily[day_idx])

        # ── EXIT logic ────────────────────────────────────────────────────────
        if in_position:
            pct_chg = (price - entry_price) / entry_price

            if pct_chg >= TP_PCT:
                # Take-profit hit
                balance += position_dollars * TP_PCT
                wins    += 1
                in_position = False

            elif pct_chg <= -SL_PCT:
                # Stop-loss hit
                balance -= position_dollars * SL_PCT
                in_position = False

        # ── ENTRY logic (only if flat) ─────────────────────────────────────
        if not in_position:
            supertrend_bullish = int(st_val) == 1

            if rsi_val < RSI_OVERSOLD and supertrend_bullish:
                # Ruby v4.0 unit calculation
                units = 3.0 + 1.0          # base + Supertrend bonus (always bullish here)
                if fg < 25:
                    units += 1.3

                # Dollar value = units × 1% of current balance
                dollar_value = min(units * BASE_UNIT * balance / 100.0, balance)

                entry_price      = price
                position_dollars = dollar_value
                in_position      = True
                total_trades    += 1

    # ── Force-close any open position at end of simulation ────────────────────
    if in_position:
        final_price = df["close"].iloc[-1]
        pct_chg     = (final_price - entry_price) / entry_price
        balance    += position_dollars * pct_chg
        if pct_chg > 0:
            wins += 1

    win_rate = (wins / total_trades * 100.0) if total_trades > 0 else 0.0

    return {
        "sim":             sim_index + 1,
        "total_trades":    total_trades,
        "win_rate":        win_rate,
        "ending_balance":  balance,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# REPORT PRINTER
# ═══════════════════════════════════════════════════════════════════════════════

def print_report(results: list[dict]) -> None:
    W = 62
    print()
    print("═" * W)
    print("    💎  RUBY v4.0 MULTIVERSE — 60-DAY SIMULATION REPORT")
    print("═" * W)
    print(
        f"  {'Sim #':<8} {'Trades':>8} {'Win Rate':>12} {'Ending Balance':>16}"
    )
    print("─" * W)

    for r in results:
        balance_str = f"${r['ending_balance']:,.2f}"
        delta       = r["ending_balance"] - STARTING_BALANCE
        delta_str   = f"({'+'if delta>=0 else ''}{delta:.2f})"
        print(
            f"  {'#' + str(r['sim']):<8}"
            f" {r['total_trades']:>8}"
            f" {r['win_rate']:>11.1f}%"
            f" {balance_str:>12}  {delta_str}"
        )

    print("─" * W)
    avg_balance = sum(r["ending_balance"] for r in results) / len(results)
    avg_trades  = sum(r["total_trades"]   for r in results) / len(results)
    avg_wr      = sum(r["win_rate"]       for r in results) / len(results)
    avg_delta   = avg_balance - STARTING_BALANCE
    avg_sign    = "+" if avg_delta >= 0 else ""

    print(
        f"  {'AVG':<8}"
        f" {avg_trades:>8.1f}"
        f" {avg_wr:>11.1f}%"
        f" ${avg_balance:>11,.2f}  ({avg_sign}{avg_delta:.2f})"
    )
    print("═" * W)
    print(f"  Starting balance per sim: ${STARTING_BALANCE:.2f}")
    print(f"  Entry rule : RSI < {RSI_OVERSOLD:.0f} AND Supertrend BULLISH")
    print(f"  Exit rule  : +{TP_PCT*100:.0f}% TP  /  -{SL_PCT*100:.0f}% SL  (forced close at sim end)")
    print(f"  Sizing     : 4.0–5.3 units × 1% of balance")
    print(f"  Universe   : {NUM_SIMULATIONS} sims × {DAYS} days × {BARS_PER_DAY} bars/day = {TOTAL_BARS:,} bars each")
    print("═" * W)
    print()


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    print(f"\n🔄 Running {NUM_SIMULATIONS} simulations × {DAYS} days of synthetic BTC data …")

    results = []
    for i in range(NUM_SIMULATIONS):
        print(f"   Simulation {i + 1:02d}/{NUM_SIMULATIONS} … ", end="", flush=True)
        r = run_simulation(i)
        results.append(r)
        print(f"done  ({r['total_trades']} trades, {r['win_rate']:.1f}% win rate)")

    print_report(results)


if __name__ == "__main__":
    main()
