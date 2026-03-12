from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Optional

import requests


@dataclass
class SafetyConfig:
    """
    Configuration for safety limits and notifications.
    """

    # Risk control
    max_daily_loss_pct: float = 0.05  # 5% daily loss

    # Notification endpoints (fill in with your own)
    discord_webhook_url: Optional[str] = None
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None

    # Time zone for "day" boundaries (UTC by default)
    tz: dt.tzinfo = field(default_factory=lambda: dt.timezone.utc)


class SafetyModule:
    """
    Monitors account balance and enforces a hard daily loss limit.

    Behaviour:
    - Tracks starting balance for each calendar day (in a given tz).
    - If current_balance drops more than `max_daily_loss_pct` from today's start,
      it will:
        1. Close all open positions via the provided exchange client.
        2. Send a notification via Discord and/or Telegram.
        3. Enter a 24-hour shutdown cooldown where trading is disabled.
    - "No exceptions": once triggered, the cooldown must fully elapse
      before trading can resume.
    """

    def __init__(self, config: Optional[SafetyConfig] = None) -> None:
        self.config = config or SafetyConfig()
        self._today_start_balance: Optional[float] = None
        self._today: Optional[dt.date] = None
        self._shutdown_until: Optional[dt.datetime] = None

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def check_and_enforce(self, current_balance: float, exchange) -> bool:
        """
        Check daily loss and enforce safety rules.

        Parameters
        ----------
        current_balance : float
            Current account equity (e.g. from exchange.fetch_balance()).
        exchange :
            An exchange client (e.g. ccxt) with:
              - fetch_positions() or equivalent
              - create_order() / close-all logic (see close_all_positions)

        Returns
        -------
        bool
            True if trading is allowed, False if the bot must stay shut down.
        """
        now = dt.datetime.now(self.config.tz)

        # If currently in enforced shutdown window, do not trade.
        if self._shutdown_until is not None and now < self._shutdown_until:
            return False

        # Reset daily start balance at a new calendar day.
        self._ensure_today_start(now, current_balance)

        if (
            self._today_start_balance is None
            or self._today_start_balance <= 0
            or current_balance <= 0
        ):
            # Cannot compute a meaningful drawdown; allow trading.
            return True

        drawdown = (self._today_start_balance - current_balance) / self._today_start_balance

        if drawdown >= self.config.max_daily_loss_pct:
            # Breach: enforce immediate shutdown and risk-off behaviour.
            self._handle_breach(now, current_balance, exchange, drawdown)
            return False

        return True

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _ensure_today_start(self, now: dt.datetime, current_balance: float) -> None:
        today = now.date()
        if self._today != today:
            self._today = today
            self._today_start_balance = current_balance

    def _handle_breach(
        self,
        now: dt.datetime,
        current_balance: float,
        exchange,
        drawdown: float,
    ) -> None:
        # Enforce 24-hour shutdown from the time of breach.
        self._shutdown_until = now + dt.timedelta(hours=24)

        # 1. Close all open positions.
        self._close_all_positions(exchange)

        # 2. Send notifications.
        msg = (
            f"[SAFETY TRIGGERED] Daily loss limit breached.\n"
            f"Date: {now.isoformat()}\n"
            f"Start balance: {self._today_start_balance:.2f}\n"
            f"Current balance: {current_balance:.2f}\n"
            f"Drawdown: {drawdown * 100:.2f}%\n"
            f"Trading is disabled for 24 hours (until {self._shutdown_until.isoformat()})."
        )
        self._notify_discord(msg)
        self._notify_telegram(msg)

    def _close_all_positions(self, exchange) -> None:
        """
        Close all positions on the exchange.

        This implementation assumes a ccxt-like client. You may need to adapt it
        to your specific broker / exchange and market type (spot vs futures).
        """
        try:
            # For derivatives / margin: use fetch_positions if available.
            if hasattr(exchange, "fetch_positions"):
                positions = exchange.fetch_positions()
                for pos in positions:
                    size = float(pos.get("contracts") or pos.get("size") or 0.0)
                    if size == 0:
                        continue
                    symbol = pos["symbol"]
                    side = pos.get("side")

                    # Simplest assumption: send a market order in the opposite direction
                    # for the same size to fully close.
                    if side == "long":
                        exchange.create_market_sell_order(symbol, size)
                    elif side == "short":
                        exchange.create_market_buy_order(symbol, size)
            # For spot: use fetch_open_orders and cancel them; selling remaining balances
            # is exchange-specific and should be implemented carefully.
            elif hasattr(exchange, "fetch_open_orders"):
                open_orders = exchange.fetch_open_orders()
                for order in open_orders:
                    try:
                        exchange.cancel_order(order["id"], order["symbol"])
                    except Exception:
                        # Best-effort; in safety mode we still proceed with shutdown.
                        continue
        except Exception:
            # In safety context we don't allow exceptions to prevent shutdown.
            # Logging can be added here if you have a logger.
            pass

    # ------------------------------------------------------------------ #
    # Notification helpers
    # ------------------------------------------------------------------ #
    def _notify_discord(self, message: str) -> None:
        url = self.config.discord_webhook_url
        if not url:
            return
        try:
            requests.post(url, json={"content": message}, timeout=5)
        except Exception:
            # Swallow exceptions; safety logic must not crash on notification issues.
            pass

    def _notify_telegram(self, message: str) -> None:
        token = self.config.telegram_bot_token
        chat_id = self.config.telegram_chat_id
        if not token or not chat_id:
            return
        try:
            api_url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {"chat_id": chat_id, "text": message}
            requests.post(api_url, json=payload, timeout=5)
        except Exception:
            # Swallow exceptions; safety logic must not crash on notification issues.
            pass

