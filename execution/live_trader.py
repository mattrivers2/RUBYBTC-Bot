from __future__ import annotations

import csv
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import ccxt
import pandas as pd
import pandas_ta as ta
import requests

from execution.safety_module import SafetyConfig, SafetyModule
from strategies.mean_reversion import (
    MeanReversionBollingerStrategy,
    MeanReversionConfig,
)

# ─── File Paths ────────────────────────────────────────────────────────────────
BASE_DIR        = Path(__file__).resolve().parent.parent
LOG_FILE        = BASE_DIR / "live_trades.log"
PAPER_LOG_FILE  = BASE_DIR / "paper_trades.log"
LEDGER_FILE     = BASE_DIR / "ruby_performance.csv"
WALLET_FILE     = BASE_DIR / "wallet.txt"

# ─── Thread-Safety ────────────────────────────────────────────────────────────
_file_lock = threading.Lock()

# ─── Webhook Configuration ────────────────────────────────────────────────────
DISCORD_WEBHOOK_URL        = os.getenv("DISCORD_WEBHOOK_URL", "")
DISCORD_ALERTS_WEBHOOK_URL = os.getenv("DISCORD_ALERTS_WEBHOOK_URL", "")
DISCORD_STATUS_WEBHOOK_URL = os.getenv("DISCORD_STATUS_WEBHOOK_URL", "")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# ─── Advisor Constants ────────────────────────────────────────────────────────
STARTING_BALANCE = 100.0   # immutable baseline for % growth calc
BASE_UNIT        = 1.00    # 1% of balance per unit


# ═══════════════════════════════════════════════════════════════════════════════
# VIRTUAL WALLET  (Tasks 2 & 3)
# ═══════════════════════════════════════════════════════════════════════════════

def load_wallet() -> float:
    """
    Read current virtual balance from wallet.txt.
    Creates the file at STARTING_BALANCE if it doesn't exist.
    """
    try:
        if WALLET_FILE.exists():
            raw = WALLET_FILE.read_text().strip()
            return float(raw)
    except (ValueError, OSError):
        pass
    save_wallet(STARTING_BALANCE)
    return STARTING_BALANCE


def save_wallet(balance: float) -> None:
    """Thread-safe write of current balance to wallet.txt."""
    try:
        with _file_lock:
            WALLET_FILE.write_text(f"{balance:.2f}")
    except OSError as e:
        print(f"⚠️ Could not save wallet: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# PERSISTENT LEDGER  (Task 1)
# ═══════════════════════════════════════════════════════════════════════════════

_LEDGER_HEADERS = [
    "Timestamp", "Asset", "Price", "Signal",
    "Units_Recommended", "Dollar_Value", "Current_Virtual_Balance",
]


def log_trade_signal(
    asset: str,
    signal_type: str,
    price: float,
    units: float,
    dollar_value: float,
    current_balance: float,
) -> None:
    """
    Thread-safe append of one row to ruby_performance.csv.
    Creates the file with headers if it doesn't exist yet.
    """
    try:
        with _file_lock:
            file_exists = LEDGER_FILE.exists()
            with open(LEDGER_FILE, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=_LEDGER_HEADERS)
                if not file_exists:
                    writer.writeheader()
                writer.writerow({
                    "Timestamp":               datetime.now(timezone.utc).isoformat(),
                    "Asset":                   asset,
                    "Price":                   f"{price:.2f}",
                    "Signal":                  signal_type,
                    "Units_Recommended":       f"{units:.2f}",
                    "Dollar_Value":            f"{dollar_value:.2f}",
                    "Current_Virtual_Balance": f"{current_balance:.2f}",
                })
    except OSError as e:
        print(f"⚠️ Could not write to ledger: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# LOGGER
# ═══════════════════════════════════════════════════════════════════════════════

def setup_logger() -> logging.Logger:
    logger = logging.getLogger("live_trades")
    logger.setLevel(logging.INFO)
    if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
        handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        formatter = logging.Formatter(
            "%(asctime)s - %(levelname)s - %(message)s", "%Y-%m-%d %H:%M:%S"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger


# ═══════════════════════════════════════════════════════════════════════════════
# EXCHANGE
# ═══════════════════════════════════════════════════════════════════════════════

def create_exchange(exchange_id: str, api_key: str = None, api_secret: str = None):
    exchange_id = exchange_id.lower()
    params = {"enableRateLimit": True}
    if api_key and api_key.lower() not in ["none", "your_key_here", ""]:
        params["apiKey"] = api_key
    if api_secret and api_secret.lower() not in ["none", "your_secret_here", ""]:
        params["secret"] = api_secret
    exchange_class = getattr(ccxt, exchange_id)
    return exchange_class(params)


def fetch_ohlcv_15m(exchange, symbol: str, limit: int = 500) -> pd.DataFrame:
    candles = exchange.fetch_ohlcv(symbol, timeframe="15m", limit=limit)
    df = pd.DataFrame(
        candles, columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    return df


# ═══════════════════════════════════════════════════════════════════════════════
# SUPERTREND
# ═══════════════════════════════════════════════════════════════════════════════

def get_supertrend(df: pd.DataFrame) -> str:
    """
    Supertrend (Length=10, Multiplier=3.0) via pandas_ta.
    Returns "BULLISH" (direction=1) or "BEARISH" (direction=-1).
    """
    try:
        st = ta.supertrend(df["high"], df["low"], df["close"], length=10, multiplier=3.0)
        direction_col = [c for c in st.columns if c.startswith("SUPERTd_")]
        if not direction_col:
            return "UNKNOWN"
        return "BULLISH" if int(st[direction_col[0]].iloc[-1]) == 1 else "BEARISH"
    except Exception as e:
        print(f"⚠️ Supertrend calc error: {e}")
        return "UNKNOWN"


# ═══════════════════════════════════════════════════════════════════════════════
# FEAR & GREED
# ═══════════════════════════════════════════════════════════════════════════════

def get_sentiment() -> dict:
    """
    Fetch Fear & Greed index from alternative.me.
    Falls back gracefully if the API is unavailable.
    """
    try:
        resp = requests.get("https://api.alternative.me/fng/", timeout=8)
        resp.raise_for_status()
        data = resp.json()["data"][0]
        return {
            "value":          int(data["value"]),
            "classification": data["value_classification"],
        }
    except Exception as e:
        print(f"⚠️ Sentiment API unavailable: {e}")
        return {"value": 50, "classification": "Neutral (API unavailable)"}


# ═══════════════════════════════════════════════════════════════════════════════
# RECOMMENDATION ENGINE  (Task 3 — dynamic account_balance)
# ═══════════════════════════════════════════════════════════════════════════════

def build_recommendation(
    rsi: float,
    price: float,
    bb_lower: float,
    supertrend: str,
    fg_value: int,
    account_balance: float,
) -> dict:
    """
    Calculates unit size and recommendation using the live virtual balance.

    BUY base: RSI < 35 AND price < BB lower  →  3.0 units
    + Supertrend BULLISH                      →  +1.0 unit
    + Fear & Greed < 25                       →  +1.3 units
    HOLD: conditions not met                  →  0 units

    Dollar value scales with account_balance (1 unit = 1% of balance).
    """
    total_units = 0.0
    reasons: list[str] = []

    is_oversold = rsi < 35 and price < bb_lower

    if is_oversold:
        total_units += 3.0
        reasons.append(
            f"RSI is oversold ({rsi:.1f}) and price is below the lower Bollinger Band"
        )
        if supertrend == "BULLISH":
            total_units += 1.0
            reasons.append("Supertrend confirms bullish momentum")
        if fg_value < 25:
            total_units += 1.3
            reasons.append(
                f"Market is in {fg_value} Fear & Greed — high-probability bounce zone"
            )

    dollar_value = total_units * BASE_UNIT * account_balance / 100.0

    if total_units > 0:
        recommendation = f"BUY {total_units:.1f} UNITS (${dollar_value:.2f})"
        fires = "🔥🔥🔥🔥🔥" if total_units >= 4 else "🔥🔥"
        conviction = f"{fires} ({'HIGH' if total_units >= 4 else 'LOW'})"
        if len(reasons) >= 2:
            summary = f"{reasons[0].capitalize()}. {reasons[1].capitalize()}."
        elif reasons:
            summary = f"{reasons[0].capitalize()}."
        else:
            summary = "Oversold conditions detected. Watch for confirmation."
    else:
        recommendation = "HOLD"
        conviction = "🔥 (WAIT)"
        summary = (
            "No oversold signal detected. RSI and price are within normal ranges. "
            "Monitoring for next entry."
        )

    return {
        "recommendation": recommendation,
        "conviction":     conviction,
        "summary":        summary,
        "total_units":    total_units,
        "dollar_value":   dollar_value,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# NOTIFICATIONS
# ═══════════════════════════════════════════════════════════════════════════════

def _reason_for_signal(row: pd.Series) -> str:
    if row["signal"] == 1:
        return "Price <= lower BB AND RSI < 25 (Mean-Reversion Long)."
    if row["signal"] == -1:
        return "Price >= upper BB AND RSI > 75 (Mean-Reversion Short)."
    return "No trade."


def _notify_discord(
    message: str,
    color: int = 0x3498DB,
    title: str = "Ruby Update",
    destination: str = "scans",
    advisor_section: Optional[str] = None,
) -> None:
    if destination == "alerts":
        url = DISCORD_ALERTS_WEBHOOK_URL
    elif destination == "status":
        url = DISCORD_STATUS_WEBHOOK_URL
    else:
        url = DISCORD_WEBHOOK_URL

    if not url:
        return

    try:
        description = message
        if advisor_section:
            description += f"\n\n{advisor_section}"

        payload = {
            "embeds": [
                {
                    "title":       title,
                    "description": description,
                    "color":       color,
                    "footer":      {"text": "Ruby Trading Systems | v4.0 Ledger Edition"},
                    "timestamp":   datetime.now(timezone.utc).isoformat(),
                }
            ]
        }
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"❌ Discord Error: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════════════════════════════════════

def run_live_trading_loop(exchange_id: str, symbol: str):
    logger = setup_logger()

    # ── Task 2 & 3: load persistent balance on every (re)start ────────────────
    virtual_balance = load_wallet()
    print(f"💼 Wallet loaded: ${virtual_balance:.2f}")

    _notify_discord(
        "💎 Ruby Ledger Edition is live. Wallet loaded from persistent storage.",
        color=0x9B59B6,
        title="SYSTEM ONLINE",
        destination="status",
    )

    exchange = create_exchange(
        exchange_id, os.getenv("BINANCE_API_KEY"), os.getenv("BINANCE_API_SECRET")
    )

    strategy = MeanReversionBollingerStrategy(
        MeanReversionConfig(
            rsi_oversold=25.0, rsi_overbought=75.0, bb_std=2.25, ema_trend_period=200
        )
    )

    safety = SafetyModule(SafetyConfig(discord_webhook_url=DISCORD_ALERTS_WEBHOOK_URL))

    last_bar_time  = None
    last_position  = 0
    last_heartbeat = datetime.now(timezone.utc)

    print(
        f"🚀 Ruby Initialized (Ledger Edition) | Exchange: {exchange_id} | Symbol: {symbol}"
    )

    while True:
        try:
            # 1. Timing — wait for next 15m candle
            now_ms      = exchange.milliseconds()
            interval_ms = 15 * 60 * 1000
            wait_ms     = interval_ms - (now_ms % interval_ms)
            print(
                f"⏰ {datetime.now().strftime('%H:%M:%S')} | "
                f"Next candle in {int(wait_ms / 1000)}s..."
            )
            time.sleep(max(wait_ms / 1000, 1.0))

            # 2. Fetch market data
            df       = fetch_ohlcv_15m(exchange, symbol)
            strat_df = strategy.generate_signals(df)
            last_row     = strat_df.iloc[-1]
            current_time = strat_df.index[-1]

            curr_price = last_row["close"]
            low_bb     = last_row["bb_lower"]
            rsi_val    = last_row["rsi"]
            dist       = curr_price - low_bb

            # 3. Intelligence Layer
            supertrend = get_supertrend(df)
            sentiment  = get_sentiment()
            fg_value   = sentiment["value"]
            fg_label   = sentiment["classification"]

            # Task 3: use live virtual_balance for dynamic sizing
            rec = build_recommendation(
                rsi_val, curr_price, low_bb, supertrend, fg_value, virtual_balance
            )

            # Task 4: P&L stats
            growth_pct = ((virtual_balance - STARTING_BALANCE) / STARTING_BALANCE) * 100
            growth_sign = "+" if growth_pct >= 0 else ""
            perf_section = (
                f"**📈 Performance Tracker**\n"
                f"┣ **Paper Wallet:** ${virtual_balance:.2f}\n"
                f"┗ **Total Growth:** {growth_sign}{growth_pct:.1f}%"
            )

            # 4. Terminal Dashboard
            print(f"\n📊 --- {symbol} INTELLIGENCE DASHBOARD ---")
            print(f"   💰 Price:      ${curr_price:,.2f} | BB Lower: ${low_bb:,.2f}")
            print(f"   🌡️  RSI:        {rsi_val:.2f} | Dist: ${dist:,.2f}")
            print(f"   📈 Supertrend: {supertrend}")
            print(f"   😨 Fear/Greed: {fg_value} — {fg_label}")
            print(f"   💎 Advisor:    {rec['recommendation']}")
            print(f"   💼 Wallet:     ${virtual_balance:.2f} ({growth_sign}{growth_pct:.1f}%)")
            print("-" * 40)

            # 5. Build advisor section for Discord embed (with P&L)
            advisor_section = (
                f"**💎 Ruby Executive Strategy**\n"
                f"┣ **Recommendation:** {rec['recommendation']}\n"
                f"┣ **Conviction:** {rec['conviction']}\n"
                f"┣ **Analysis:** {rec['summary']}\n\n"
                f"{perf_section}"
            )

            # 6. Discord Routine Scan
            current_dt  = datetime.now().strftime("%b %d | %H:%M")
            discord_dash = (
                f"💰 **Price**: ${curr_price:,.2f}\n"
                f"📉 **BB Lower**: ${low_bb:,.2f}\n"
                f"🌡️ **RSI**: {rsi_val:.2f}\n"
                f"📏 **Distance**: ${dist:,.2f} to target\n"
                f"📈 **Supertrend**: {supertrend}\n"
                f"😨 **Fear & Greed**: {fg_value} — {fg_label}"
            )
            _notify_discord(
                discord_dash,
                color=0x3498DB,
                title=f"📡 {current_dt} SCAN | {symbol}",
                destination="scans",
                advisor_section=advisor_section,
            )

            if last_bar_time is not None and current_time <= last_bar_time:
                continue
            last_bar_time = current_time

            # 7. Heartbeat (every 4h)
            now_utc = datetime.now(timezone.utc)
            if now_utc - last_heartbeat >= timedelta(hours=4):
                _notify_discord(
                    f"Ruby Ledger Edition is alive and tracking {symbol}. "
                    f"Wallet: ${virtual_balance:.2f}",
                    color=0x9B59B6,
                    title="❤️ HEARTBEAT",
                    destination="status",
                )
                last_heartbeat = now_utc

            # 8. Signal Execution (existing RSI/BB logic preserved)
            signal   = int(last_row["signal"])
            position = int(last_row["position"])

            if signal != 0 and position != last_position:
                side   = "buy" if signal == 1 else "sell"
                reason = _reason_for_signal(last_row)

                # ── Task 1 & 4: log to CSV and update wallet on BUY signals ──
                if signal == 1 and rec["total_units"] > 0:
                    virtual_balance += rec["dollar_value"]
                    save_wallet(virtual_balance)

                    log_trade_signal(
                        asset           = symbol,
                        signal_type     = "BUY",
                        price           = curr_price,
                        units           = rec["total_units"],
                        dollar_value    = rec["dollar_value"],
                        current_balance = virtual_balance,
                    )
                    print(
                        f"📒 Ledger updated | Wallet: ${virtual_balance:.2f} "
                        f"(+${rec['dollar_value']:.2f})"
                    )
                elif signal == -1:
                    log_trade_signal(
                        asset           = symbol,
                        signal_type     = "SELL",
                        price           = curr_price,
                        units           = 0.0,
                        dollar_value    = 0.0,
                        current_balance = virtual_balance,
                    )

                # Rebuild P&L section with updated balance
                growth_pct  = ((virtual_balance - STARTING_BALANCE) / STARTING_BALANCE) * 100
                growth_sign = "+" if growth_pct >= 0 else ""
                perf_section = (
                    f"**📈 Performance Tracker**\n"
                    f"┣ **Paper Wallet:** ${virtual_balance:.2f}\n"
                    f"┗ **Total Growth:** {growth_sign}{growth_pct:.1f}%"
                )
                advisor_section = (
                    f"**💎 Ruby Executive Strategy**\n"
                    f"┣ **Recommendation:** {rec['recommendation']}\n"
                    f"┣ **Conviction:** {rec['conviction']}\n"
                    f"┣ **Analysis:** {rec['summary']}\n\n"
                    f"{perf_section}"
                )

                sig_msg = (
                    f"🚀 **Action**: {side.upper()}\n"
                    f"💰 **Price**: ${curr_price:,.2f}\n"
                    f"📝 **Reason**: {reason}\n"
                    f"🧪 **Mode**: PUBLIC PAPER TRADING"
                )

                print(f"🚀 SIGNAL DETECTED: {side.upper()}")
                _notify_discord(
                    sig_msg,
                    color=0x2ECC71 if signal == 1 else 0xE74C3C,
                    title="💎 TRADE SIGNAL",
                    destination="alerts",
                    advisor_section=advisor_section,
                )

                with open(PAPER_LOG_FILE, "a") as f:
                    f.write(
                        f"{datetime.now(timezone.utc).isoformat()} - PUBLIC PAPER - "
                        f"{side.upper()} @ {curr_price}\n"
                    )

                logger.info(f"SIGNAL: {side.upper()} @ {curr_price:.2f} | {reason}")

            last_position = position

        except Exception as exc:
            print(f"❌ ERROR: {exc}")
            logger.error(f"Loop error: {exc}")
            time.sleep(30)


def main():
    run_live_trading_loop(
        os.getenv("EXCHANGE_ID", "binanceus"),
        os.getenv("SYMBOL", "BTC/USDT"),
    )


if __name__ == "__main__":
    main()
