from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ccxt
import pandas as pd
import pandas_ta as ta
import requests

from execution.safety_module import SafetyConfig, SafetyModule
from execution.ruby_state import (
    DATA_DIR, LOG_FILE, PAPER_LOG_FILE, LEDGER_FILE,
    DISCORD_WEBHOOK_URL, DISCORD_ALERTS_WEBHOOK_URL, DISCORD_STATUS_WEBHOOK_URL,
    STARTING_BALANCE, FOOTER,
    load_wallet, save_wallet, unit_dollar_value,
    load_trade_state, save_trade_state, reset_trade_state,
    log_trade, post_embed, post_trade_ticket_open,
)
from strategies.mean_reversion import (
    MeanReversionBollingerStrategy,
    MeanReversionConfig,
)

# ─── Advisor Constants ────────────────────────────────────────────────────────
BASE_UNIT = 1.00    # 1 unit = 1% of wallet


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
    try:
        resp = requests.get("https://api.alternative.me/fng/", timeout=8)
        resp.raise_for_status()
        data = resp.json()["data"][0]
        return {"value": int(data["value"]), "classification": data["value_classification"]}
    except Exception as e:
        print(f"⚠️ Sentiment API unavailable: {e}")
        return {"value": 50, "classification": "Neutral (API unavailable)"}


# ═══════════════════════════════════════════════════════════════════════════════
# RECOMMENDATION ENGINE  (dynamic: 1 unit = 1% of wallet)
# ═══════════════════════════════════════════════════════════════════════════════

def build_recommendation(
    rsi: float,
    price: float,
    bb_lower: float,
    supertrend: str,
    fg_value: int,
    account_balance: float,
) -> dict:
    total_units = 0.0
    reasons: list[str] = []
    is_oversold = rsi < 35 and price < bb_lower

    if is_oversold:
        total_units += 3.0
        reasons.append(f"RSI oversold ({rsi:.1f}) below lower Bollinger Band")
        if supertrend == "BULLISH":
            total_units += 1.0
            reasons.append("Supertrend confirms bullish momentum")
        if fg_value < 25:
            total_units += 1.3
            reasons.append(f"Extreme Fear ({fg_value}) — high-probability bounce")

    unit_val     = unit_dollar_value(account_balance)
    dollar_value = round(total_units * unit_val, 4)

    if total_units > 0:
        recommendation = f"BUY {total_units:.1f} UNITS  (${dollar_value:.2f})"
        fires          = "🔥🔥🔥🔥🔥" if total_units >= 4 else "🔥🔥"
        conviction     = f"{fires} ({'HIGH' if total_units >= 4 else 'LOW'})"
        summary        = ".  ".join(r.capitalize() for r in reasons[:2]) + "."
    else:
        recommendation = "HOLD"
        conviction     = "🔥 (WAIT)"
        summary        = "No oversold signal. RSI and price are within normal ranges."

    return {
        "recommendation": recommendation,
        "conviction":     conviction,
        "summary":        summary,
        "total_units":    total_units,
        "dollar_value":   dollar_value,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════════════════════════════════════

def run_live_trading_loop(exchange_id: str, symbol: str):
    logger = setup_logger()

    virtual_balance = load_wallet()
    trade_state     = load_trade_state()
    mode            = trade_state.get("mode", "hunting")

    print(f"💼 Wallet loaded: ${virtual_balance:.2f}")
    print(f"🤖 Mode: {mode.upper()}")

    post_embed(
        title="💎 SYSTEM ONLINE",
        description=(
            f"Ruby {FOOTER.split('|')[1].strip()} is live.\n"
            f"Wallet: **${virtual_balance:.2f}** | Mode: **{mode.upper()}**"
        ),
        color=0x9B59B6,
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
    safety         = SafetyModule(SafetyConfig(discord_webhook_url=DISCORD_ALERTS_WEBHOOK_URL))
    last_bar_time  = None
    last_heartbeat = datetime.now(timezone.utc)

    print(f"🚀 Ruby Initialized ({FOOTER}) | {exchange_id} | {symbol}")

    while True:
        try:
            # 1. Timing
            now_ms      = exchange.milliseconds()
            interval_ms = 15 * 60 * 1000
            wait_ms     = interval_ms - (now_ms % interval_ms)
            print(f"⏰ {datetime.now().strftime('%H:%M:%S')} | Next candle in {int(wait_ms/1000)}s...")
            time.sleep(max(wait_ms / 1000, 1.0))

            # 2. Reload wallet and state (may have been updated by ruby_cmd.py)
            virtual_balance = load_wallet()
            trade_state     = load_trade_state()
            mode            = trade_state.get("mode", "hunting")

            # 3. Market data
            df           = fetch_ohlcv_15m(exchange, symbol)
            strat_df     = strategy.generate_signals(df)
            last_row     = strat_df.iloc[-1]
            current_time = strat_df.index[-1]

            curr_price = last_row["close"]
            low_bb     = last_row["bb_lower"]
            rsi_val    = last_row["rsi"]
            dist       = curr_price - low_bb

            # 4. Intelligence layer
            supertrend = get_supertrend(df)
            sentiment  = get_sentiment()
            fg_value   = sentiment["value"]
            fg_label   = sentiment["classification"]

            rec = build_recommendation(
                rsi_val, curr_price, low_bb, supertrend, fg_value, virtual_balance
            )

            # 5. P&L stats
            growth_pct  = ((virtual_balance - STARTING_BALANCE) / STARTING_BALANCE) * 100
            g_sign      = "+" if growth_pct >= 0 else ""
            unit_val    = unit_dollar_value(virtual_balance)

            # 6. Mode label for console + Discord
            if mode == "in_trade":
                ep       = trade_state.get("entry_price", 0)
                alloc    = trade_state.get("dollar_allocated", 0)
                unrl     = (curr_price - ep) / ep * alloc if ep else 0
                u_sign   = "+" if unrl >= 0 else ""
                mode_tag = f"🔴 IN TRADE @ ${ep:,.2f}  (unrealised {u_sign}${unrl:.4f})"
            else:
                mode_tag = "🟢 HUNTING"

            # 7. Terminal dashboard
            print(f"\n📊 --- {symbol} INTELLIGENCE DASHBOARD ---")
            print(f"   💰 Price:      ${curr_price:,.2f} | BB Lower: ${low_bb:,.2f}")
            print(f"   🌡️  RSI:        {rsi_val:.2f} | Dist: ${dist:,.2f}")
            print(f"   📈 Supertrend: {supertrend}")
            print(f"   😨 Fear/Greed: {fg_value} — {fg_label}")
            print(f"   💎 Advisor:    {rec['recommendation']}")
            print(f"   💼 Wallet:     ${virtual_balance:.2f} ({g_sign}{growth_pct:.1f}%)  |  1 unit = ${unit_val:.4f}")
            print(f"   🤖 Mode:       {mode_tag}")
            print("-" * 45)

            # 8. Routine scan Discord embed
            current_dt = datetime.now().strftime("%b %d | %H:%M")
            scan_fields = [
                {"name": "💰 PRICE",         "value": f"${curr_price:,.2f}",                             "inline": True},
                {"name": "📉 BB LOWER",      "value": f"${low_bb:,.2f}",                                 "inline": True},
                {"name": "📏 DISTANCE",      "value": f"${dist:,.2f}",                                   "inline": True},
                {"name": "🌡️ RSI",            "value": f"{rsi_val:.2f}",                                  "inline": True},
                {"name": "📈 SUPERTREND",    "value": supertrend,                                         "inline": True},
                {"name": "😨 FEAR & GREED",  "value": f"{fg_value} — {fg_label}",                        "inline": True},
                {"name": "💎 ADVISOR",       "value": rec["recommendation"],                              "inline": True},
                {"name": "💼 WALLET",        "value": f"${virtual_balance:.2f} ({g_sign}{growth_pct:.1f}%)", "inline": True},
                {"name": "⚡ 1 UNIT",        "value": f"${unit_val:.4f}",                                "inline": True},
                {"name": "🤖 MODE",          "value": mode_tag,                                          "inline": False},
            ]
            post_embed(
                title=f"📡 {current_dt} SCAN  |  {symbol}",
                color=0x3498DB,
                fields=scan_fields,
                destination="scans",
            )

            if last_bar_time is not None and current_time <= last_bar_time:
                continue
            last_bar_time = current_time

            # 9. Heartbeat (every 4h)
            now_utc = datetime.now(timezone.utc)
            if now_utc - last_heartbeat >= timedelta(hours=4):
                post_embed(
                    title="❤️ HEARTBEAT",
                    description=f"Ruby is alive and tracking {symbol}. Wallet: **${virtual_balance:.2f}**",
                    color=0x9B59B6,
                    destination="status",
                )
                last_heartbeat = now_utc

            # 10. Buy signal — only fire when HUNTING and conditions met
            if mode == "hunting" and rec["total_units"] > 0:
                post_trade_ticket_open(
                    symbol=symbol,
                    entry_price=curr_price,
                    units=rec["total_units"],
                    dollar_allocated=rec["dollar_value"],
                    wallet=virtual_balance,
                    rsi=rsi_val,
                    supertrend=supertrend,
                    fg_label=fg_label,
                )
                log_trade(
                    asset           = symbol,
                    signal_type     = "BUY_SIGNAL",
                    price           = curr_price,
                    units           = rec["total_units"],
                    dollar_value    = rec["dollar_value"],
                    current_balance = virtual_balance,
                )
                logger.info(f"BUY SIGNAL @ ${curr_price:,.2f} | {rec['recommendation']}")
                print(f"🚨 BUY SIGNAL sent to Discord: {rec['recommendation']}")

            # 11. Paper log for all bar transitions
            with open(PAPER_LOG_FILE, "a") as f:
                f.write(
                    f"{datetime.now(timezone.utc).isoformat()} | {mode.upper()} | "
                    f"Price: {curr_price:.2f} | RSI: {rsi_val:.2f} | {supertrend}\n"
                )

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
