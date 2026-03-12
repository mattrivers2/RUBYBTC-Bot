"""
execution/ruby_state.py
========================
Shared state management for the Ruby Trading System.
Handles wallet persistence, trade state (JSON), ledger logging,
and all Discord embed posting. Imported by both live_trader.py
and ruby_cmd.py so there is one source of truth for every file path.
"""

from __future__ import annotations

import csv
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

# ─── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR         = Path(__file__).resolve().parent.parent
DATA_DIR         = BASE_DIR / "rubylogsdata"
DATA_DIR.mkdir(parents=True, exist_ok=True)

WALLET_FILE      = DATA_DIR / "wallet.txt"
LEDGER_FILE      = DATA_DIR / "ruby_performance.csv"
TRADE_STATE_FILE = DATA_DIR / "trade_state.json"
LOG_FILE         = DATA_DIR / "live_trades.log"
PAPER_LOG_FILE   = DATA_DIR / "paper_trades.log"

# ─── Constants ────────────────────────────────────────────────────────────────
STARTING_BALANCE = 100.0    # baseline for % growth calculation
BASE_UNIT_PCT    = 1.0      # 1 unit = 1% of wallet

VERSION          = "v5.0 State-Aware Terminal"
FOOTER           = f"Ruby Trading Systems | {VERSION}"

# ─── Thread safety ────────────────────────────────────────────────────────────
_file_lock = threading.Lock()

# ─── Discord URLs ─────────────────────────────────────────────────────────────
DISCORD_WEBHOOK_URL        = os.getenv("DISCORD_WEBHOOK_URL", "")
DISCORD_ALERTS_WEBHOOK_URL = os.getenv("DISCORD_ALERTS_WEBHOOK_URL", "")
DISCORD_STATUS_WEBHOOK_URL = os.getenv("DISCORD_STATUS_WEBHOOK_URL", "")


# ═══════════════════════════════════════════════════════════════════════════════
# WALLET
# ═══════════════════════════════════════════════════════════════════════════════

def load_wallet() -> float:
    """Read balance from wallet.txt. Creates at STARTING_BALANCE if missing."""
    try:
        if WALLET_FILE.exists():
            return float(WALLET_FILE.read_text().strip())
    except (ValueError, OSError):
        pass
    save_wallet(STARTING_BALANCE)
    return STARTING_BALANCE


def save_wallet(balance: float) -> None:
    """Thread-safe write to wallet.txt."""
    try:
        with _file_lock:
            WALLET_FILE.write_text(f"{balance:.2f}")
    except OSError as e:
        print(f"⚠️  Could not save wallet: {e}")


def unit_dollar_value(balance: float) -> float:
    """1 unit = 1% of wallet."""
    return round(balance * BASE_UNIT_PCT / 100.0, 4)


# ═══════════════════════════════════════════════════════════════════════════════
# TRADE STATE
# ═══════════════════════════════════════════════════════════════════════════════

def _default_state() -> dict:
    return {
        "mode":             "hunting",   # "hunting" | "in_trade"
        "entry_price":      None,
        "entry_time":       None,
        "units":            0.0,
        "dollar_allocated": 0.0,
    }


def load_trade_state() -> dict:
    """Read trade_state.json. Returns default hunting state if missing/corrupt."""
    try:
        if TRADE_STATE_FILE.exists():
            return json.loads(TRADE_STATE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        pass
    return _default_state()


def save_trade_state(state: dict) -> None:
    """Thread-safe write to trade_state.json."""
    try:
        with _file_lock:
            TRADE_STATE_FILE.write_text(json.dumps(state, indent=2))
    except OSError as e:
        print(f"⚠️  Could not save trade state: {e}")


def reset_trade_state() -> None:
    """Return to Hunting Mode without touching the wallet."""
    save_trade_state(_default_state())


# ═══════════════════════════════════════════════════════════════════════════════
# LEDGER
# ═══════════════════════════════════════════════════════════════════════════════

_LEDGER_HEADERS = [
    "Timestamp", "Asset", "Price", "Signal",
    "Units", "Dollar_Value", "Current_Balance",
]


def log_trade(
    asset: str,
    signal_type: str,
    price: float,
    units: float,
    dollar_value: float,
    current_balance: float,
) -> None:
    """Thread-safe append to ruby_performance.csv. Auto-creates headers."""
    try:
        with _file_lock:
            new_file = not LEDGER_FILE.exists()
            with open(LEDGER_FILE, "a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=_LEDGER_HEADERS)
                if new_file:
                    w.writeheader()
                w.writerow({
                    "Timestamp":       datetime.now(timezone.utc).isoformat(),
                    "Asset":           asset,
                    "Price":           f"{price:.2f}",
                    "Signal":          signal_type,
                    "Units":           f"{units:.2f}",
                    "Dollar_Value":    f"{dollar_value:.4f}",
                    "Current_Balance": f"{current_balance:.2f}",
                })
    except OSError as e:
        print(f"⚠️  Could not write ledger: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# DISCORD — RICH EMBEDS
# ═══════════════════════════════════════════════════════════════════════════════

def _discord_url(destination: str) -> str:
    if destination == "alerts":
        return DISCORD_ALERTS_WEBHOOK_URL
    if destination == "status":
        return DISCORD_STATUS_WEBHOOK_URL
    return DISCORD_WEBHOOK_URL


def post_embed(
    title: str,
    description: str = "",
    color: int = 0x3498DB,
    fields: Optional[list[dict]] = None,
    destination: str = "scans",
) -> None:
    """
    Post a Discord embed with optional structured fields.
    fields format: [{"name": "...", "value": "...", "inline": True}, ...]
    """
    url = _discord_url(destination)
    if not url:
        return
    try:
        embed: dict = {
            "title":     title,
            "color":     color,
            "footer":    {"text": FOOTER},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if description:
            embed["description"] = description
        if fields:
            embed["fields"] = fields
        requests.post(url, json={"embeds": [embed]}, timeout=5)
    except Exception as e:
        print(f"❌ Discord Error: {e}")


# ─── Pre-built Trade Ticket helpers ───────────────────────────────────────────

def post_trade_ticket_open(
    symbol: str,
    entry_price: float,
    units: float,
    dollar_allocated: float,
    wallet: float,
    rsi: float,
    supertrend: str,
    fg_label: str,
) -> None:
    """Richly formatted BUY ticket sent to the alerts channel."""
    tp = entry_price * 1.03
    sl = entry_price * 0.98
    unit_val = unit_dollar_value(wallet)
    growth = ((wallet - STARTING_BALANCE) / STARTING_BALANCE) * 100
    g_sign = "+" if growth >= 0 else ""

    fields = [
        {"name": "📍 ENTRY PRICE",        "value": f"**${entry_price:,.2f}**",                  "inline": True},
        {"name": "🎯 TAKE-PROFIT  (+3%)", "value": f"**${tp:,.2f}**",                           "inline": True},
        {"name": "🛑 STOP-LOSS  (−2%)",   "value": f"**${sl:,.2f}**",                           "inline": True},
        {"name": "💰 UNITS USED",          "value": f"{units:.1f} units  (${dollar_allocated:.2f})", "inline": True},
        {"name": "⚡ 1 UNIT VALUE",        "value": f"${unit_val:.2f}",                          "inline": True},
        {"name": "💼 WALLET",              "value": f"${wallet:.2f}  ({g_sign}{growth:.1f}%)",   "inline": True},
        {"name": "🌡️ RSI",                 "value": f"{rsi:.1f}",                                "inline": True},
        {"name": "📈 SUPERTREND",          "value": supertrend,                                  "inline": True},
        {"name": "😨 SENTIMENT",           "value": fg_label,                                    "inline": True},
    ]
    post_embed(
        title=f"🎯 TRADE TICKET — LONG {symbol}",
        description="Entry conditions met. Levels have been set.",
        color=0x2ECC71,
        fields=fields,
        destination="alerts",
    )


def post_trade_ticket_close(
    symbol: str,
    entry_price: float,
    exit_price: float,
    dollar_allocated: float,
    pnl: float,
    new_wallet: float,
    reason: str,
) -> None:
    """Richly formatted SELL/CLOSE ticket sent to the alerts channel."""
    pnl_pct  = (pnl / dollar_allocated * 100) if dollar_allocated else 0
    pnl_sign = "+" if pnl >= 0 else ""
    growth   = ((new_wallet - STARTING_BALANCE) / STARTING_BALANCE) * 100
    g_sign   = "+" if growth >= 0 else ""
    color    = 0x2ECC71 if pnl >= 0 else 0xE74C3C
    result   = "✅  WIN" if pnl >= 0 else "❌  LOSS"

    fields = [
        {"name": "📍 ENTRY PRICE",   "value": f"${entry_price:,.2f}",                             "inline": True},
        {"name": "🏁 EXIT PRICE",    "value": f"${exit_price:,.2f}",                              "inline": True},
        {"name": "📊 RESULT",        "value": result,                                              "inline": True},
        {"name": "💸 P&L",           "value": f"**{pnl_sign}${pnl:.4f}  ({pnl_sign}{pnl_pct:.1f}%)**", "inline": True},
        {"name": "💼 NEW WALLET",    "value": f"${new_wallet:.2f}  ({g_sign}{growth:.1f}%)",       "inline": True},
        {"name": "📝 REASON",        "value": reason,                                              "inline": False},
    ]
    post_embed(
        title=f"🏁 TRADE CLOSED — {symbol}",
        color=color,
        fields=fields,
        destination="alerts",
    )


def post_status_embed(
    symbol: str,
    price: float,
    wallet: float,
    trade_state: dict,
) -> None:
    """Status embed with current price, wallet, unit value, and mode."""
    unit_val = unit_dollar_value(wallet)
    growth   = ((wallet - STARTING_BALANCE) / STARTING_BALANCE) * 100
    g_sign   = "+" if growth >= 0 else ""
    mode     = trade_state.get("mode", "hunting")

    if mode == "in_trade":
        ep       = trade_state["entry_price"]
        alloc    = trade_state["dollar_allocated"]
        unrl_pnl = (price - ep) / ep * alloc
        unrl_pct = (price - ep) / ep * 100
        u_sign   = "+" if unrl_pnl >= 0 else ""
        mode_str = f"🔴  IN TRADE @ ${ep:,.2f}"
        extra_fields = [
            {"name": "📍 OPEN ENTRY",       "value": f"${ep:,.2f}",                                    "inline": True},
            {"name": "💸 UNREALISED P&L",   "value": f"{u_sign}${unrl_pnl:.4f}  ({u_sign}{unrl_pct:.2f}%)", "inline": True},
            {"name": "💰 ALLOCATED",         "value": f"${alloc:.4f}",                                  "inline": True},
        ]
    else:
        mode_str     = "🟢  HUNTING"
        extra_fields = []

    fields = [
        {"name": f"💰 {symbol} PRICE", "value": f"**${price:,.2f}**",                       "inline": True},
        {"name": "💼 WALLET",           "value": f"${wallet:.2f}  ({g_sign}{growth:.1f}%)", "inline": True},
        {"name": "⚡ 1 UNIT VALUE",     "value": f"${unit_val:.4f}",                        "inline": True},
        {"name": "🤖 MODE",             "value": mode_str,                                  "inline": False},
        *extra_fields,
    ]
    post_embed(
        title="💼 RUBY STATUS",
        color=0x9B59B6,
        fields=fields,
        destination="status",
    )
