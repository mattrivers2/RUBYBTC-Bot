import asyncio
import os
import threading
from pathlib import Path


def ensure_project_structure(base_dir: Path) -> None:
    for folder in ("data", "strategies", "backtester", "execution"):
        (base_dir / folder).mkdir(parents=True, exist_ok=True)


def _start_trading_loop() -> None:
    """Run the 15-minute hunting loop in a background daemon thread."""
    from execution.live_trader import run_live_trading_loop
    try:
        run_live_trading_loop(
            exchange_id=os.getenv("EXCHANGE_ID", "binanceus"),
            symbol=os.getenv("SYMBOL", "BTC/USDT"),
        )
    except Exception as e:
        print(f"\n❌ Trading loop error: {e}")


async def _keepalive(thread: threading.Thread) -> None:
    """Block the event loop until the trading thread exits (fallback when no bot token)."""
    while thread.is_alive():
        await asyncio.sleep(10)


async def _run_all(trading_thread: threading.Thread) -> None:
    from execution.discord_bot import run_discord_bot
    bot_token = os.getenv("DISCORD_BOT_TOKEN", "")
    if bot_token:
        await run_discord_bot()
    else:
        print("ℹ️  DISCORD_BOT_TOKEN not set — running in scanner-only mode.")
        await _keepalive(trading_thread)


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    ensure_project_structure(base_dir)

    print("--- 🚀 Launching Ruby v5.0 State-Aware Terminal ---")

    trading_thread = threading.Thread(target=_start_trading_loop, daemon=True, name="RubyTrader")
    trading_thread.start()

    try:
        asyncio.run(_run_all(trading_thread))
    except KeyboardInterrupt:
        print("\nBot stopped by user.")


if __name__ == "__main__":
    main()
