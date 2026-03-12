"""
execution/discord_bot.py
========================
Discord Bot with App Commands (Slash Commands) for Ruby Trading System.

Slash commands: /status  /bought  /sold  /add_funds  /cancel

All commands respond in-channel with a rich Discord embed AND push to
the dedicated webhook channels (alerts/status) for routing.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
from datetime import datetime, timezone
from typing import Optional

import ccxt
import discord
from discord import app_commands

from execution.ruby_state import (
    STARTING_BALANCE, FOOTER,
    load_wallet, save_wallet, unit_dollar_value,
    load_trade_state, save_trade_state, reset_trade_state,
    log_trade,
    post_embed, post_trade_ticket_open, post_trade_ticket_close,
    post_status_embed,
)

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_GUILD_ID  = int(os.getenv("DISCORD_GUILD_ID", "0") or "0")
EXCHANGE_ID       = os.getenv("EXCHANGE_ID", "binanceus")
SYMBOL            = os.getenv("SYMBOL", "BTC/USDT")

_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _make_embed(
    title: str,
    description: str = "",
    color: int = 0x3498DB,
    fields: Optional[list[dict]] = None,
) -> discord.Embed:
    """Build a discord.Embed from the same field-dict format used by webhooks."""
    embed = discord.Embed(
        title=title,
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
    if description:
        embed.description = description
    embed.set_footer(text=FOOTER)
    if fields:
        for f in fields:
            embed.add_field(
                name=f["name"],
                value=f["value"],
                inline=f.get("inline", True),
            )
    return embed


def _fetch_price_sync() -> float:
    """Blocking price fetch — run in executor to stay off the event loop."""
    try:
        exchange = ccxt.binanceus({"enableRateLimit": True})
        return float(exchange.fetch_ticker(SYMBOL)["last"])
    except Exception as e:
        print(f"⚠️  Price fetch error: {e}")
        return 0.0


async def _fetch_price() -> float:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _fetch_price_sync)


# ═══════════════════════════════════════════════════════════════════════════════
# BOT SETUP
# ═══════════════════════════════════════════════════════════════════════════════

class RubyBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        """Called once before on_ready — sync the command tree."""
        if DISCORD_GUILD_ID:
            guild = discord.Object(id=DISCORD_GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            try:
                synced = await self.tree.sync(guild=guild)
                print(f"✅ Discord: {len(synced)} commands synced to Guild {DISCORD_GUILD_ID} (instant)")
            except discord.errors.Forbidden:
                print(
                    "⚠️  Guild sync failed (403 Forbidden) — bot is not yet in the server.\n"
                    "    Falling back to global sync (commands appear in ~1 hour after invite)."
                )
                synced = await self.tree.sync()
                print(f"✅ Discord: {len(synced)} global commands queued.")
        else:
            synced = await self.tree.sync()
            print(f"✅ Discord: {len(synced)} global commands synced (Discord propagates in ~1h)")

    async def on_ready(self) -> None:
        print(f"🤖 Bot online: {self.user}  (ID: {self.user.id})")
        post_embed(
            title="🤖 BOT ONLINE",
            description=(
                f"Ruby Discord Terminal is live.\n"
                f"Commands: **/status** · **/bought** · **/sold** · **/add_funds** · **/cancel**"
            ),
            color=0x9B59B6,
            destination="status",
        )


client = RubyBot()


# ═══════════════════════════════════════════════════════════════════════════════
# /status
# ═══════════════════════════════════════════════════════════════════════════════

@client.tree.command(
    name="status",
    description="Show current BTC price, wallet balance, unit value, and live trade P/L",
)
async def slash_status(interaction: discord.Interaction) -> None:
    await interaction.response.defer(thinking=True)

    price    = await _fetch_price()
    wallet   = load_wallet()
    state    = load_trade_state()
    unit_val = unit_dollar_value(wallet)
    mode     = state.get("mode", "hunting")
    growth   = (wallet - STARTING_BALANCE) / STARTING_BALANCE * 100
    g_sign   = "+" if growth >= 0 else ""

    if mode == "in_trade":
        ep        = state.get("entry_price", 0)
        alloc     = state.get("dollar_allocated", 0)
        unrl      = (price - ep) / ep * alloc if ep else 0
        unrl_pct  = (price - ep) / ep * 100 if ep else 0
        u_sign    = "+" if unrl >= 0 else ""
        mode_str  = f"🔴  IN TRADE @ ${ep:,.2f}"
        extra = [
            {"name": "📍 OPEN ENTRY",     "value": f"${ep:,.2f}",                                         "inline": True},
            {"name": "💸 UNREALISED P&L", "value": f"{u_sign}${unrl:.4f}  ({u_sign}{unrl_pct:.2f}%)",    "inline": True},
            {"name": "💰 ALLOCATED",       "value": f"${alloc:.4f}",                                       "inline": True},
        ]
    else:
        mode_str = "🟢  HUNTING"
        extra    = []

    fields = [
        {"name": f"💰 {SYMBOL} PRICE", "value": f"**${price:,.2f}**",                       "inline": True},
        {"name": "💼 WALLET",           "value": f"${wallet:.2f}  ({g_sign}{growth:.1f}%)", "inline": True},
        {"name": "⚡ 1 UNIT VALUE",     "value": f"${unit_val:.4f}",                        "inline": True},
        {"name": "🤖 MODE",             "value": mode_str,                                  "inline": False},
        *extra,
    ]
    await interaction.followup.send(embed=_make_embed("💼 RUBY STATUS", color=0x9B59B6, fields=fields))
    post_status_embed(SYMBOL, price, wallet, state)


# ═══════════════════════════════════════════════════════════════════════════════
# /add_funds
# ═══════════════════════════════════════════════════════════════════════════════

@client.tree.command(
    name="add_funds",
    description="Add funds to your trading wallet and recalculate the unit value",
)
@app_commands.describe(amount="Dollar amount to deposit into your wallet")
async def slash_add_funds(interaction: discord.Interaction, amount: float) -> None:
    if amount <= 0:
        await interaction.response.send_message("❌ Amount must be positive.", ephemeral=True)
        return

    old_wallet = load_wallet()
    new_wallet = old_wallet + amount
    save_wallet(new_wallet)

    unit_val = unit_dollar_value(new_wallet)
    growth   = (new_wallet - STARTING_BALANCE) / STARTING_BALANCE * 100
    g_sign   = "+" if growth >= 0 else ""

    fields = [
        {"name": "➕ AMOUNT ADDED", "value": f"${amount:.2f}",                              "inline": True},
        {"name": "💼 NEW WALLET",   "value": f"${new_wallet:.2f}  ({g_sign}{growth:.1f}%)", "inline": True},
        {"name": "⚡ NEW 1 UNIT",   "value": f"${unit_val:.4f}",                            "inline": True},
    ]
    await interaction.response.send_message(
        embed=_make_embed("💰 FUNDS ADDED", color=0x2ECC71, fields=fields)
    )
    post_embed(title="💰 FUNDS ADDED", color=0x2ECC71, fields=fields, destination="status")


# ═══════════════════════════════════════════════════════════════════════════════
# /bought
# ═══════════════════════════════════════════════════════════════════════════════

@client.tree.command(
    name="bought",
    description="Record a BUY entry — generates a Trade Ticket with TP/SL and pauses the scanner",
)
@app_commands.describe(price="Your BTC entry price")
async def slash_bought(interaction: discord.Interaction, price: float) -> None:
    state = load_trade_state()
    if state.get("mode") == "in_trade":
        await interaction.response.send_message(
            "⚠️  Already in a trade. Use **/sold** or **/cancel** first.", ephemeral=True
        )
        return

    wallet       = load_wallet()
    unit_val     = unit_dollar_value(wallet)
    tp           = price * 1.03
    sl           = price * 0.98
    units        = 4.0
    dollar_alloc = round(units * unit_val, 4)
    growth       = (wallet - STARTING_BALANCE) / STARTING_BALANCE * 100
    g_sign       = "+" if growth >= 0 else ""

    save_trade_state({
        "mode":             "in_trade",
        "entry_price":      price,
        "entry_time":       datetime.now(timezone.utc).isoformat(),
        "units":            units,
        "dollar_allocated": dollar_alloc,
    })
    log_trade(
        asset=SYMBOL, signal_type="MANUAL_BUY",
        price=price, units=units,
        dollar_value=dollar_alloc, current_balance=wallet,
    )

    fields = [
        {"name": "📍 ENTRY PRICE",        "value": f"**${price:,.2f}**",                              "inline": True},
        {"name": "🎯 TAKE-PROFIT  (+3%)", "value": f"**${tp:,.2f}**",                                 "inline": True},
        {"name": "🛑 STOP-LOSS  (−2%)",   "value": f"**${sl:,.2f}**",                                 "inline": True},
        {"name": "💰 UNITS USED",          "value": f"{units:.1f} units  (${dollar_alloc:.4f})",       "inline": True},
        {"name": "⚡ 1 UNIT VALUE",        "value": f"${unit_val:.4f}",                                "inline": True},
        {"name": "💼 WALLET",              "value": f"${wallet:.2f}  ({g_sign}{growth:.1f}%)",         "inline": True},
    ]
    await interaction.response.send_message(embed=_make_embed(
        title=f"🎯 TRADE TICKET — LONG {SYMBOL}",
        description="Entry recorded. TP/SL levels set. Scanner paused — **Hunting Mode OFF**.",
        color=0x2ECC71,
        fields=fields,
    ))
    post_trade_ticket_open(
        symbol=SYMBOL, entry_price=price, units=units,
        dollar_allocated=dollar_alloc, wallet=wallet,
        rsi=0.0, supertrend="MANUAL", fg_label="Manual Discord Entry",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# /sold
# ═══════════════════════════════════════════════════════════════════════════════

@client.tree.command(
    name="sold",
    description="Record a SELL exit, calculate final P/L, update wallet, and resume the scanner",
)
@app_commands.describe(price="Your BTC exit price")
async def slash_sold(interaction: discord.Interaction, price: float) -> None:
    state = load_trade_state()
    if state.get("mode") != "in_trade":
        await interaction.response.send_message(
            "⚠️  No open trade found. Use **/bought [price]** to open one.", ephemeral=True
        )
        return

    entry_price  = state["entry_price"]
    dollar_alloc = state["dollar_allocated"]
    units        = state["units"]

    pct_chg  = (price - entry_price) / entry_price
    pnl      = dollar_alloc * pct_chg
    pnl_sign = "+" if pnl >= 0 else ""
    result   = "WIN ✅" if pnl >= 0 else "LOSS ❌"
    color    = 0x2ECC71 if pnl >= 0 else 0xE74C3C

    old_wallet = load_wallet()
    new_wallet = old_wallet + pnl
    save_wallet(new_wallet)
    reset_trade_state()

    growth = (new_wallet - STARTING_BALANCE) / STARTING_BALANCE * 100
    g_sign = "+" if growth >= 0 else ""

    log_trade(
        asset=SYMBOL, signal_type="MANUAL_SELL",
        price=price, units=units,
        dollar_value=pnl, current_balance=new_wallet,
    )

    fields = [
        {"name": "📍 ENTRY PRICE", "value": f"${entry_price:,.2f}",                                      "inline": True},
        {"name": "🏁 EXIT PRICE",  "value": f"${price:,.2f}",                                            "inline": True},
        {"name": "📊 RESULT",      "value": result,                                                       "inline": True},
        {"name": "💸 P&L",         "value": f"**{pnl_sign}${pnl:.4f}  ({pnl_sign}{pct_chg*100:.2f}%)**", "inline": True},
        {"name": "💼 NEW WALLET",  "value": f"${new_wallet:.2f}  ({g_sign}{growth:.1f}%)",               "inline": True},
        {"name": "🤖 MODE",        "value": "🟢  Back to HUNTING",                                       "inline": True},
    ]
    await interaction.response.send_message(embed=_make_embed(
        title=f"🏁 TRADE CLOSED — {SYMBOL}",
        description="Position closed. P/L recorded. Scanner resumed — **Hunting Mode ON**.",
        color=color,
        fields=fields,
    ))
    post_trade_ticket_close(
        symbol=SYMBOL, entry_price=entry_price, exit_price=price,
        dollar_allocated=dollar_alloc, pnl=pnl, new_wallet=new_wallet,
        reason="Manual exit via /sold Discord command",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# /cancel
# ═══════════════════════════════════════════════════════════════════════════════

@client.tree.command(
    name="cancel",
    description="Cancel the open trade and return to Hunting Mode — wallet is unchanged",
)
async def slash_cancel(interaction: discord.Interaction) -> None:
    state = load_trade_state()
    mode  = state.get("mode", "hunting")

    if mode == "hunting":
        await interaction.response.send_message(
            "ℹ️  Already in **Hunting Mode** — nothing to cancel.", ephemeral=True
        )
        return

    ep = state.get("entry_price", "N/A")
    reset_trade_state()

    ep_str = f"${ep:,.2f}" if isinstance(ep, (int, float)) else str(ep)
    fields = [
        {"name": "📍 CANCELLED ENTRY", "value": ep_str,                     "inline": True},
        {"name": "💰 WALLET CHANGE",   "value": "None  (no P/L recorded)", "inline": True},
        {"name": "🤖 NEW MODE",         "value": "🟢  HUNTING",            "inline": True},
    ]
    await interaction.response.send_message(embed=_make_embed(
        title="🚫 TRADE CANCELLED",
        description="Open position removed. Wallet unchanged. Hunting Mode resumed.",
        color=0xE67E22,
        fields=fields,
    ))
    post_embed(
        title="🚫 TRADE CANCELLED",
        description=f"Position @ {ep_str} cancelled via Discord. No P/L. Returning to Hunting Mode.",
        color=0xE67E22,
        destination="status",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

async def run_discord_bot() -> None:
    """Async entry point called from main.py."""
    if not DISCORD_BOT_TOKEN:
        print("⚠️  DISCORD_BOT_TOKEN not set — Discord bot disabled.")
        return
    async with client:
        await client.start(DISCORD_BOT_TOKEN)
