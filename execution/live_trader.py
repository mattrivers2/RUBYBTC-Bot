from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import ccxt
import pandas as pd
import requests
# from dotenv import load_dotenv

from execution.safety_module import SafetyConfig, SafetyModule
from strategies.mean_reversion import (
    MeanReversionBollingerStrategy,
    MeanReversionConfig,
)

# File Paths
LOG_FILE = Path(__file__).resolve().parent.parent / "live_trades.log"
PAPER_LOG_FILE = Path(__file__).resolve().parent.parent / "paper_trades.log"

# load_dotenv()

# Webhook Configuration
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
DISCORD_ALERTS_WEBHOOK_URL = os.getenv("DISCORD_ALERTS_WEBHOOK_URL", "")
DISCORD_STATUS_WEBHOOK_URL = os.getenv("DISCORD_STATUS_WEBHOOK_URL", "")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


def setup_logger() -> logging.Logger:
    logger = logging.getLogger("live_trades")
    logger.setLevel(logging.INFO)
    if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
        handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        formatter = logging.Formatter(
            "%(asctime)s - %(levelname)s - %(message)s", "%Y-%m-%d %H:%M:%S")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger


def create_exchange(exchange_id: str,
                    api_key: str = None,
                    api_secret: str = None):
    exchange_id = exchange_id.lower()

    # Initialize with public access settings
    params = {'enableRateLimit': True}

    # Only add keys if they are valid strings and not placeholders
    if api_key and api_key.lower() not in ["none", "your_key_here", ""]:
        params["apiKey"] = api_key
    if api_secret and api_secret.lower() not in [
            "none", "your_secret_here", ""
    ]:
        params["secret"] = api_secret

    exchange_class = getattr(ccxt, exchange_id)
    return exchange_class(params)


def fetch_ohlcv_15m(exchange, symbol: str, limit: int = 500) -> pd.DataFrame:
    # Public OHLCV does not require authentication
    candles = exchange.fetch_ohlcv(symbol, timeframe="15m", limit=limit)
    df = pd.DataFrame(
        candles,
        columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    return df


def _reason_for_signal(row: pd.Series) -> str:
    if row["signal"] == 1:
        return "Price <= lower BB AND RSI < 25 (Mean-Reversion Long)."
    if row["signal"] == -1:
        return "Price >= upper BB AND RSI > 75 (Mean-Reversion Short)."
    return "No trade."


def _notify_discord(message: str,
                    color: int = 0x3498db,
                    title: str = "Ruby Update",
                    destination: str = "scans") -> None:
    if destination == "alerts":
        url = DISCORD_ALERTS_WEBHOOK_URL
    elif destination == "status":
        url = DISCORD_STATUS_WEBHOOK_URL
    else:
        url = DISCORD_WEBHOOK_URL

    if not url: return
    try:
        payload = {
            "embeds": [{
                "title": title,
                "description": message,
                "color": color,
                "footer": {
                    "text": "Ruby Trading Systems | v2.0"
                },
                "timestamp": datetime.now(timezone.utc).isoformat()
            }]
        }
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"❌ Discord Error: {e}")


def run_live_trading_loop(exchange_id: str, symbol: str):
    logger = setup_logger()

    _notify_discord(
        "💎 Ruby is live in Public/Paper mode. No API Keys required.",
        color=0x9b59b6,
        title="SYSTEM ONLINE",
        destination="status")

    # Connect Anonymously
    exchange = create_exchange(exchange_id, os.getenv("BINANCE_API_KEY"),
                               os.getenv("BINANCE_API_SECRET"))

    strategy = MeanReversionBollingerStrategy(
        MeanReversionConfig(rsi_oversold=25.0,
                            rsi_overbought=75.0,
                            bb_std=2.25,
                            ema_trend_period=200))

    safety = SafetyModule(
        SafetyConfig(discord_webhook_url=DISCORD_ALERTS_WEBHOOK_URL))

    last_bar_time, last_position, last_heartbeat = None, 0, datetime.now(
        timezone.utc)

    print(
        f"🚀 Ruby Initialized (Public Mode) | Exchange: {exchange_id} | Symbol: {symbol}"
    )

    while True:
        try:
            # 1. Timing
            now_ms = exchange.milliseconds()
            interval_ms = 15 * 60 * 1000
            wait_ms = interval_ms - (now_ms % interval_ms)
            print(
                f"⏰ {datetime.now().strftime('%H:%M:%S')} | Next candle in {int(wait_ms/1000)}s..."
            )
            time.sleep(max(wait_ms / 1000, 1.0))

            # 2. Fetch Public Data
            df = fetch_ohlcv_15m(exchange, symbol)
            strat_df = strategy.generate_signals(df)
            last_row = strat_df.iloc[-1]
            current_time = strat_df.index[-1]

            curr_price = last_row['close']
            low_bb = last_row['bb_lower']
            rsi_val = last_row['rsi']
            ema_trend = last_row.get('ema_trend', 0)
            dist = curr_price - low_bb

            # 3. Terminal View
            print(f"\n📊 --- {symbol} DASHBOARD ---")
            print(f"   💰 Price: ${curr_price:,.2f} | Target: ${low_bb:,.2f}")
            print(f"   🌡️ RSI: {rsi_val:.2f} | Dist: ${dist:,.2f}")
            print("-" * 30)

            # 4. Discord Routine Scan (Channel: Scans)
            current_dt = datetime.now().strftime("%b %d | %H:%M")
            discord_dash = (f"💰 **Price**: ${curr_price:,.2f}\n"
                            f"📉 **Target**: ${low_bb:,.2f}\n"
                            f"🌡️ **RSI**: {rsi_val:.2f}\n"
                            f"📏 **Distance**: ${dist:,.2f} to goal.")
            _notify_discord(discord_dash,
                            color=0x3498db,
                            title=f"📡 {current_dt} SCAN",
                            destination="scans")

            if last_bar_time is not None and current_time <= last_bar_time:
                continue
            last_bar_time = current_time

            # 5. Fixed Paper Balance (Simulated)
            balance = 1000.0

            # 6. Heartbeat
            now_utc = datetime.now(timezone.utc)
            if now_utc - last_heartbeat >= timedelta(hours=4):
                _notify_discord(
                    f"Ruby is alive and tracking {symbol} anonymously.",
                    color=0x9b59b6,
                    title="❤️ HEARTBEAT",
                    destination="status")
                last_heartbeat = now_utc

            # 7. Signal Execution
            signal = int(last_row["signal"])
            position = int(last_row["position"])

            if signal != 0 and position != last_position:
                side = "buy" if signal == 1 else "sell"
                reason = _reason_for_signal(last_row)

                sig_msg = (f"🚀 **Action**: {side.upper()}\n"
                           f"💰 **Price**: ${curr_price:,.2f}\n"
                           f"📝 **Reason**: {reason}\n"
                           f"🧪 **Mode**: PUBLIC PAPER TRADING")

                print(f"🚀 SIGNAL DETECTED: {side.upper()}")
                _notify_discord(sig_msg,
                                color=0x2ecc71 if signal == 1 else 0xe74c3c,
                                title="💎 TRADE SIGNAL",
                                destination="alerts")

                with open(PAPER_LOG_FILE, "a") as f:
                    f.write(
                        f"{datetime.now(timezone.utc).isoformat()} - PUBLIC PAPER - {side.upper()} @ {curr_price}\n"
                    )

            last_position = position

        except Exception as exc:
            print(f"❌ ERROR: {exc}")
            time.sleep(30)


def main():
    run_live_trading_loop(
        os.getenv(
            "EXCHANGE_ID",
            "binance"),  # Switched to binance global for better public access
        os.getenv("SYMBOL", "BTC/USDT"))


if __name__ == "__main__":
    main()
