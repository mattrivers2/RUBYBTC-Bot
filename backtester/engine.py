from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from strategies.mean_reversion import (
    MeanReversionBollingerStrategy,
    MeanReversionConfig,
)


FEE_RATE = 0.001  # 0.1% per trade side
# Assume entry price is 0.05% worse than signal price.
SLIPPAGE_PCT_ENTRY = 0.0005


@dataclass
class BacktestResult:
    total_net_profit: float
    win_rate: float
    max_drawdown: float
    profit_factor: float
    equity_curve: pd.Series
    trades: pd.DataFrame
    risk_of_ruin: float
    position_size_scale: float


def load_ohlcv(csv_path: str | Path) -> pd.DataFrame:
    """
    Load historical OHLCV data from a CSV file.

    Expected columns (case-sensitive):
    - timestamp (optional, used as index if present)
    - open, high, low, close, volume
    """
    csv_path = Path(csv_path)
    df = pd.read_csv(csv_path)

    # Normalize column names to lower-case for safety
    df.columns = [c.lower() for c in df.columns]

    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df.set_index("timestamp", inplace=True)

    required = {"open", "high", "low", "close"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"CSV is missing required columns: {missing}")

    return df


def run_backtest(
    csv_path: str | Path,
    strategy: Optional[MeanReversionBollingerStrategy] = None,
) -> BacktestResult:
    """
    Run a backtest of the mean reversion strategy on the given OHLCV CSV file.
    """
    strat = strategy or MeanReversionBollingerStrategy(MeanReversionConfig())

    raw = load_ohlcv(csv_path)
    df = strat.generate_signals(raw)

    initial_capital = strat.config.initial_capital
    equity = initial_capital
    peak_equity = initial_capital
    max_drawdown = 0.0

    equity_curve: list[float] = []
    equity_index: list[pd.Timestamp] = []

    trades: list[dict] = []

    current_side: int = 0  # 1 = long, -1 = short, 0 = flat
    current_size: float = 0.0
    entry_price: Optional[float] = None

    prev_row: Optional[pd.Series] = None
    prev_index: Optional[pd.Timestamp] = None

    for idx, row in df.iterrows():
        # We always use the current bar's OPEN for executions to avoid lookahead.
        price_open = float(row["open"])

        # ------------------------------------------------------------------ #
        # Exit logic: exits are evaluated using previous bar's stop/tp levels
        # and executed at the current bar's OPEN.
        # ------------------------------------------------------------------ #
        if current_side != 0 and entry_price is not None and prev_row is not None:
            exit_now = False

            stop = float(prev_row["stop_loss"])
            take = float(prev_row["take_profit"])

            if current_side == 1:
                if price_open <= stop or price_open >= take:
                    exit_now = True
            elif current_side == -1:
                if price_open >= stop or price_open <= take:
                    exit_now = True

            # Or a reversal / flat signal from the previous bar
            prev_side = int(prev_row["position"])
            if prev_side != current_side:
                exit_now = True

            if exit_now:
                exit_price = price_open
                notional_exit = abs(current_size * exit_price)
                fee_exit = notional_exit * FEE_RATE

                pnl = (exit_price - entry_price) * current_size
                equity += pnl - fee_exit

                trades.append(
                    {
                        "entry_time": entry_time,
                        "exit_time": idx,
                        "side": current_side,
                        "entry_price": entry_price,
                        "exit_price": exit_price,
                        "size": current_size,
                        "pnl": pnl - fee_exit,
                        "gross_pnl": pnl,
                        "fees": fee_exit + entry_fee,
                    }
                )

                current_side = 0
                current_size = 0.0
                entry_price = None

        # ------------------------------------------------------------------ #
        # Entry logic: signals are generated from previous bar's close, and
        # trades are executed at the NEXT bar's OPEN with slippage.
        # ------------------------------------------------------------------ #
        if (
            prev_row is not None
            and current_side == 0
            and prev_row.get("position_size", 0.0) != 0.0
        ):
            signal_side = int(prev_row["position"])
            if signal_side != 0:
                current_side = signal_side
                current_size = float(prev_row["position_size"])

                # Apply 0.05% adverse slippage to entry execution price
                if current_side == 1:
                    entry_price = price_open * (1.0 + SLIPPAGE_PCT_ENTRY)
                else:  # short
                    entry_price = price_open * (1.0 - SLIPPAGE_PCT_ENTRY)

                entry_time = idx

                notional_entry = abs(current_size * entry_price)
                entry_fee = notional_entry * FEE_RATE
                equity -= entry_fee

        # Track equity curve and drawdown
        equity_index.append(idx)
        equity_curve.append(equity)

        peak_equity = max(peak_equity, equity)
        if peak_equity > 0:
            dd = (peak_equity - equity) / peak_equity
            max_drawdown = max(max_drawdown, dd)

        prev_row = row
        prev_index = idx

    equity_series = pd.Series(equity_curve, index=equity_index, name="equity")

    # Export equity curve for visualization (timestamp + cumulative profit).
    equity_df = equity_series.reset_index()
    equity_df.columns = ["timestamp", "equity"]
    equity_df["cumulative_profit"] = equity_df["equity"] - initial_capital
    out_path = Path(csv_path).resolve().parent / "equity_curve.csv"
    equity_df.to_csv(out_path, index=False)

    # Aggregate trade statistics and Monte Carlo risk metrics
    if trades:
        trades_df = pd.DataFrame(trades)
        total_net_profit = float(trades_df["pnl"].sum())

        wins = trades_df[trades_df["pnl"] > 0]
        losses = trades_df[trades_df["pnl"] < 0]
        num_trades = len(trades_df)

        win_rate = float(len(wins) / num_trades * 100.0) if num_trades > 0 else 0.0

        gross_profit = float(wins["pnl"].sum()) if not wins.empty else 0.0
        gross_loss = float(losses["pnl"].sum()) if not losses.empty else 0.0
        profit_factor = (
            gross_profit / abs(gross_loss) if gross_loss < 0 else float("inf")
        )

        pnls = trades_df["pnl"].to_numpy(dtype=float)
        risk_of_ruin, size_scale = _estimate_risk_of_ruin_and_scale(
            pnls,
            initial_capital=initial_capital,
            num_runs=1000,
            target_ror=0.01,
        )
    else:
        trades_df = pd.DataFrame(
            columns=[
                "entry_time",
                "exit_time",
                "side",
                "entry_price",
                "exit_price",
                "size",
                "pnl",
                "gross_pnl",
                "fees",
            ]
        )
        total_net_profit = 0.0
        win_rate = 0.0
        profit_factor = 0.0
        risk_of_ruin = 0.0
        size_scale = 1.0

    return BacktestResult(
        total_net_profit=total_net_profit,
        win_rate=win_rate,
        max_drawdown=max_drawdown,
        profit_factor=profit_factor,
        equity_curve=equity_series,
        trades=trades_df,
        risk_of_ruin=risk_of_ruin,
        position_size_scale=size_scale,
    )


def _monte_carlo_risk_of_ruin(
    pnls: np.ndarray,
    initial_capital: float,
    num_runs: int = 1000,
    scale: float = 1.0,
) -> float:
    """
    Monte Carlo simulation: randomize trade order many times and
    compute the probability the account hits zero (or below).
    """
    if initial_capital <= 0 or pnls.size == 0:
        return 0.0

    ruin_count = 0
    for _ in range(num_runs):
        perm = np.random.permutation(pnls)
        equity = initial_capital
        ruined = False
        for pnl in perm:
            equity += pnl * scale
            if equity <= 0:
                ruined = True
                break
        if ruined:
            ruin_count += 1

    return ruin_count / num_runs


def _estimate_risk_of_ruin_and_scale(
    pnls: np.ndarray,
    initial_capital: float,
    num_runs: int = 1000,
    target_ror: float = 0.01,
) -> tuple[float, float]:
    """
    Estimate risk of ruin at current size (scale=1.0) and, if needed,
    a reduced position-size multiplier such that risk of ruin is at
    or below the target threshold.

    The position-size scale can be used to linearly scale trade sizes
    (or risk_per_trade) down.
    """
    base_ror = _monte_carlo_risk_of_ruin(pnls, initial_capital, num_runs, scale=1.0)
    if base_ror <= target_ror:
        return base_ror, 1.0

    # Simple geometric search for a lower scale factor.
    scale = 0.5
    best_scale = scale
    best_ror = base_ror

    for _ in range(6):
        ror = _monte_carlo_risk_of_ruin(pnls, initial_capital, num_runs, scale=scale)
        if ror <= target_ror:
            best_scale = scale
            best_ror = ror
            break

        best_scale = scale
        best_ror = ror
        scale *= 0.5

    return best_ror, best_scale

