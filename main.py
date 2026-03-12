from pathlib import Path
from execution.live_trader import run_live_trading_loop

def ensure_project_structure(base_dir: Path) -> None:
    """
    Ensure the core folder structure exists.
    """
    for folder in ("data", "strategies", "backtester", "execution"):
        (base_dir / folder).mkdir(parents=True, exist_ok=True)

def main() -> None:
    base_dir = Path(__file__).resolve().parent
    ensure_project_structure(base_dir)

    print("--- 🚀 Launching Live Paper Trading Mode ---")
    
    try:
        # We are only passing TWO arguments now:
        # 1. exchange_id
        # 2. symbol
        run_live_trading_loop(
            exchange_id='binanceus', 
            symbol='BTC/USDT'
        )
    except KeyboardInterrupt:
        print("\nBot stopped by user.")
    except Exception as e:
        print(f"\nCritical Error: {e}")

if __name__ == "__main__":
    main()