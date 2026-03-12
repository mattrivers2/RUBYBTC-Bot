from __future__ import annotations

from pathlib import Path

from backtester.engine import run_backtest
from strategies.mean_reversion import MeanReversionBollingerStrategy, MeanReversionConfig


def run_parameter_sweep(data_path: Path) -> None:
    """
    Run a simple parameter sweep over RSI bands and Bollinger std devs and
    report the combination with the highest profit factor and lowest drawdown.
    """
    if not data_path.exists():
        print(f"Data file not found: {data_path}")
        print("Run data/fetch_data.py first to download historical data.")
        return

    # Define parameter grid: 5 RSI band pairs x 10 BB std values = 50 runs.
    rsi_pairs = [
        (20.0, 80.0),
        (25.0, 75.0),
        (30.0, 70.0),
        (35.0, 65.0),
        (40.0, 60.0),
    ]
    bb_stds = [1.5, 1.75, 2.0, 2.25, 2.5, 2.75, 3.0, 3.25, 3.5, 3.75]

    results = []

    for rsi_oversold, rsi_overbought in rsi_pairs:
        for bb_std in bb_stds:
            cfg = MeanReversionConfig(
                rsi_oversold=rsi_oversold,
                rsi_overbought=rsi_overbought,
                bb_std=bb_std,
            )
            strat = MeanReversionBollingerStrategy(cfg)

            bt_result = run_backtest(data_path, strategy=strat)

            results.append(
                {
                    "rsi_oversold": rsi_oversold,
                    "rsi_overbought": rsi_overbought,
                    "bb_std": bb_std,
                    "profit_factor": bt_result.profit_factor,
                    "max_drawdown": bt_result.max_drawdown,
                }
            )

    # Sort by highest profit factor, then lowest max drawdown.
    results.sort(
        key=lambda r: (-r["profit_factor"], r["max_drawdown"])
    )

    best = results[0]

    print("Best parameter combination (by Profit Factor, then Drawdown):")
    print(
        f"  RSI oversold / overbought: {best['rsi_oversold']:.1f} / {best['rsi_overbought']:.1f}"
    )
    print(f"  Bollinger std dev:         {best['bb_std']:.2f}")
    print(f"  Profit Factor:             {best['profit_factor']:.3f}")
    print(f"  Max Drawdown:              {best['max_drawdown']:.2%}")


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    data_path = base_dir / "data" / "BTCUSDT_15m.csv"
    run_parameter_sweep(data_path)


if __name__ == "__main__":
    main()

