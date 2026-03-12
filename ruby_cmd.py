#!/usr/bin/env python3
"""
ruby_cmd.py — Ruby v5.0 Manual Trade Terminal
==============================================
Run commands from the Replit console while the hunting loop runs
in the background.

Usage
-----
    python3 ruby_cmd.py status
    python3 ruby_cmd.py add_funds 50
    python3 ruby_cmd.py bought 69000
    python3 ruby_cmd.py sold 70000
    python3 ruby_cmd.py cancel

All results are printed to the console AND posted to your Discord channels.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── path fix so we can import execution.* from the repo root ──────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent))

import ccxt

from execution.ruby_state import (
    STARTING_BALANCE,
    load_wallet, save_wallet, unit_dollar_value,
    load_trade_state, save_trade_state, reset_trade_state,
    log_trade,
    post_embed, post_trade_ticket_open, post_trade_ticket_close,
    post_status_embed,
)

EXCHANGE_ID = os.getenv("EXCHANGE_ID", "binanceus")
SYMBOL      = os.getenv("SYMBOL", "BTC/USDT")


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _fetch_price() -> float:
    """Pull the latest BTC price from the exchange (public, no key needed)."""
    try:
        exchange = ccxt.binanceus({"enableRateLimit": True})
        ticker   = exchange.fetch_ticker(SYMBOL)
        return float(ticker["last"])
    except Exception as e:
        print(f"⚠️  Could not fetch live price: {e}")
        return 0.0


def _print_banner(cmd: str) -> None:
    print(f"\n{'─'*50}")
    print(f"  💎 Ruby Terminal  |  /{cmd}")
    print(f"{'─'*50}")


# ═══════════════════════════════════════════════════════════════════════════════
# COMMANDS
# ═══════════════════════════════════════════════════════════════════════════════

def cmd_status() -> None:
    """Display current price, wallet, unit value, and trade mode."""
    _print_banner("status")
    price       = _fetch_price()
    wallet      = load_wallet()
    trade_state = load_trade_state()
    unit_val    = unit_dollar_value(wallet)
    mode        = trade_state.get("mode", "hunting")
    growth      = (wallet - STARTING_BALANCE) / STARTING_BALANCE * 100
    g_sign      = "+" if growth >= 0 else ""

    print(f"  💰 {SYMBOL} Price : ${price:,.2f}")
    print(f"  💼 Wallet        : ${wallet:.2f}  ({g_sign}{growth:.1f}%)")
    print(f"  ⚡ 1 Unit Value  : ${unit_val:.4f}")
    print(f"  🤖 Mode          : {mode.upper()}")

    if mode == "in_trade":
        ep   = trade_state.get("entry_price", 0)
        alloc = trade_state.get("dollar_allocated", 0)
        unrl  = (price - ep) / ep * alloc if ep and price else 0
        u_sign = "+" if unrl >= 0 else ""
        print(f"  📍 Open Entry    : ${ep:,.2f}")
        print(f"  💸 Unrealised P&L: {u_sign}${unrl:.4f}")

    print(f"{'─'*50}\n")
    post_status_embed(SYMBOL, price, wallet, trade_state)
    print("  ✅ Status posted to Discord.")


def cmd_add_funds(args: list[str]) -> None:
    """Add funds to wallet.txt and recalculate unit value."""
    _print_banner("add_funds")
    if not args:
        print("  ❌ Usage: python3 ruby_cmd.py add_funds [amount]")
        return
    try:
        amount = float(args[0])
    except ValueError:
        print(f"  ❌ Invalid amount: '{args[0]}'")
        return

    if amount <= 0:
        print("  ❌ Amount must be positive.")
        return

    old_wallet = load_wallet()
    new_wallet = old_wallet + amount
    save_wallet(new_wallet)

    unit_val = unit_dollar_value(new_wallet)
    growth   = (new_wallet - STARTING_BALANCE) / STARTING_BALANCE * 100
    g_sign   = "+" if growth >= 0 else ""

    print(f"  ➕ Added          : ${amount:.2f}")
    print(f"  💼 Old Wallet     : ${old_wallet:.2f}")
    print(f"  💼 New Wallet     : ${new_wallet:.2f}  ({g_sign}{growth:.1f}%)")
    print(f"  ⚡ New 1 Unit     : ${unit_val:.4f}")
    print(f"{'─'*50}\n")

    post_embed(
        title="💰 FUNDS ADDED",
        color=0x2ECC71,
        fields=[
            {"name": "➕ AMOUNT ADDED",  "value": f"${amount:.2f}",                            "inline": True},
            {"name": "💼 NEW WALLET",    "value": f"${new_wallet:.2f}  ({g_sign}{growth:.1f}%)", "inline": True},
            {"name": "⚡ NEW 1 UNIT",    "value": f"${unit_val:.4f}",                           "inline": True},
        ],
        destination="status",
    )
    print("  ✅ Wallet update posted to Discord.")


def cmd_bought(args: list[str]) -> None:
    """
    Manually record a BUY entry.
    Generates a Trade Ticket with TP/SL levels and saves state.
    """
    _print_banner("bought")
    if not args:
        print("  ❌ Usage: python3 ruby_cmd.py bought [entry_price]")
        return
    try:
        entry_price = float(args[0])
    except ValueError:
        print(f"  ❌ Invalid price: '{args[0]}'")
        return

    trade_state = load_trade_state()
    if trade_state.get("mode") == "in_trade":
        print("  ⚠️  Already in a trade! Use /sold or /cancel first.")
        return

    wallet   = load_wallet()
    unit_val = unit_dollar_value(wallet)
    tp       = entry_price * 1.03
    sl       = entry_price * 0.98

    # Default to 4.0 units (base 3 + supertrend 1) when called manually
    units         = 4.0
    dollar_alloc  = round(units * unit_val, 4)

    print(f"  📍 Entry Price    : ${entry_price:,.2f}")
    print(f"  🎯 Take-Profit    : ${tp:,.2f}  (+3%)")
    print(f"  🛑 Stop-Loss      : ${sl:,.2f}  (−2%)")
    print(f"  💰 Units Used     : {units:.1f}  (${dollar_alloc:.4f})")
    print(f"  ⚡ 1 Unit Value   : ${unit_val:.4f}")
    print(f"  💼 Wallet         : ${wallet:.2f}")
    print(f"{'─'*50}\n")

    new_state = {
        "mode":             "in_trade",
        "entry_price":      entry_price,
        "entry_time":       datetime.now(timezone.utc).isoformat(),
        "units":            units,
        "dollar_allocated": dollar_alloc,
    }
    save_trade_state(new_state)

    log_trade(
        asset           = SYMBOL,
        signal_type     = "MANUAL_BUY",
        price           = entry_price,
        units           = units,
        dollar_value    = dollar_alloc,
        current_balance = wallet,
    )

    post_trade_ticket_open(
        symbol           = SYMBOL,
        entry_price      = entry_price,
        units            = units,
        dollar_allocated = dollar_alloc,
        wallet           = wallet,
        rsi              = 0.0,
        supertrend       = "MANUAL",
        fg_label         = "Manual Entry",
    )
    print("  ✅ Trade Ticket posted to Discord.  State → IN TRADE.")


def cmd_sold(args: list[str]) -> None:
    """
    Manually record a SELL exit.
    Calculates P&L, updates wallet, resets state to Hunting Mode.
    """
    _print_banner("sold")
    if not args:
        print("  ❌ Usage: python3 ruby_cmd.py sold [exit_price]")
        return
    try:
        exit_price = float(args[0])
    except ValueError:
        print(f"  ❌ Invalid price: '{args[0]}'")
        return

    trade_state = load_trade_state()
    if trade_state.get("mode") != "in_trade":
        print("  ⚠️  No open trade found. Use /bought [price] to open one.")
        return

    entry_price  = trade_state["entry_price"]
    dollar_alloc = trade_state["dollar_allocated"]
    units        = trade_state["units"]

    pct_chg  = (exit_price - entry_price) / entry_price
    pnl      = dollar_alloc * pct_chg
    pnl_sign = "+" if pnl >= 0 else ""
    result   = "WIN ✅" if pnl >= 0 else "LOSS ❌"

    old_wallet = load_wallet()
    new_wallet = old_wallet + pnl
    save_wallet(new_wallet)

    growth = (new_wallet - STARTING_BALANCE) / STARTING_BALANCE * 100
    g_sign = "+" if growth >= 0 else ""

    print(f"  📍 Entry Price    : ${entry_price:,.2f}")
    print(f"  🏁 Exit Price     : ${exit_price:,.2f}")
    print(f"  📊 Result         : {result}")
    print(f"  💸 P&L            : {pnl_sign}${pnl:.4f}  ({pnl_sign}{pct_chg*100:.2f}%)")
    print(f"  💼 Old Wallet     : ${old_wallet:.2f}")
    print(f"  💼 New Wallet     : ${new_wallet:.2f}  ({g_sign}{growth:.1f}%)")
    print(f"{'─'*50}\n")

    log_trade(
        asset           = SYMBOL,
        signal_type     = "MANUAL_SELL",
        price           = exit_price,
        units           = units,
        dollar_value    = pnl,
        current_balance = new_wallet,
    )

    post_trade_ticket_close(
        symbol           = SYMBOL,
        entry_price      = entry_price,
        exit_price       = exit_price,
        dollar_allocated = dollar_alloc,
        pnl              = pnl,
        new_wallet       = new_wallet,
        reason           = "Manual exit via /sold command",
    )

    reset_trade_state()
    print("  ✅ Close Ticket posted to Discord.  State → HUNTING.")


def cmd_cancel() -> None:
    """Reset trade state to Hunting Mode without changing the wallet."""
    _print_banner("cancel")
    trade_state = load_trade_state()
    mode        = trade_state.get("mode", "hunting")

    if mode == "hunting":
        print("  ℹ️  Already in Hunting Mode — nothing to cancel.")
    else:
        ep = trade_state.get("entry_price", "N/A")
        print(f"  🚫 Cancelling open trade  (entry was ${ep})")
        reset_trade_state()

        post_embed(
            title="🚫 TRADE CANCELLED",
            description=f"Open position @ ${ep} cancelled manually. No P&L recorded. Returning to **Hunting Mode**.",
            color=0xE67E22,
            destination="status",
        )
        print("  ✅ Cancel notice posted to Discord.  State → HUNTING.")

    print(f"{'─'*50}\n")


# ═══════════════════════════════════════════════════════════════════════════════
# DISPATCHER
# ═══════════════════════════════════════════════════════════════════════════════

COMMANDS = {
    "status":    (cmd_status,    []),
    "add_funds": (cmd_add_funds, ["amount"]),
    "bought":    (cmd_bought,    ["entry_price"]),
    "sold":      (cmd_sold,      ["exit_price"]),
    "cancel":    (cmd_cancel,    []),
}


def print_help() -> None:
    print("\n  💎 Ruby v5.0 — Available Commands\n")
    print("  python3 ruby_cmd.py status")
    print("  python3 ruby_cmd.py add_funds  [amount]")
    print("  python3 ruby_cmd.py bought     [entry_price]")
    print("  python3 ruby_cmd.py sold       [exit_price]")
    print("  python3 ruby_cmd.py cancel\n")


def main() -> None:
    raw_args = sys.argv[1:]

    # strip leading slash if user types /status etc.
    if raw_args and raw_args[0].startswith("/"):
        raw_args[0] = raw_args[0][1:]

    if not raw_args or raw_args[0] not in COMMANDS:
        print_help()
        return

    cmd_name  = raw_args[0]
    cmd_args  = raw_args[1:]
    func, _   = COMMANDS[cmd_name]

    if cmd_args:
        func(cmd_args)
    else:
        func()


if __name__ == "__main__":
    main()
