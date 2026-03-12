from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class MeanReversionConfig:
    """
    Configuration for the Bollinger Bands + RSI mean reversion strategy.
    """

    initial_capital: float = 100_000.0
    risk_per_trade: float = 0.01  # 1% of capital

    bb_window: int = 20
    bb_std: float = 2.0

    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0

    atr_period: int = 14

    stop_loss_pct: float = 0.015  # 1.5%
    take_profit_pct: float = 0.03  # 3%

    # Trend filter and dynamic exits
    ema_trend_period: int = 200
    use_trend_filter: bool = True
    use_opposite_band_exit: bool = True


class MeanReversionBollingerStrategy:
    """
    Bollinger Bands + RSI mean reversion strategy with ATR-based position sizing.

    Assumes the input DataFrame has at least the following columns:
    - 'open'
    - 'high'
    - 'low'
    - 'close'

    All calculations are done in-place on a copy of the input DataFrame and a
    new DataFrame with signals and risk management levels is returned.
    """

    def __init__(self, config: Optional[MeanReversionConfig] = None) -> None:
        self.config = config or MeanReversionConfig()

    # --------------------------------------------------------------------- #
    # Public API
    # --------------------------------------------------------------------- #
    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Generate trading signals and risk management levels.

        Returns a DataFrame with the original OHLC data and additional columns:
        - 'bb_middle', 'bb_upper', 'bb_lower'
        - 'rsi'
        - 'atr'
        - 'signal'  (1 = enter long, -1 = enter short, 0 = no new trade)
        - 'position' (1 = long, -1 = short, 0 = flat)
        - 'entry_price'
        - 'stop_loss'
        - 'take_profit'
        - 'position_size' (number of units/shares/contracts)
        """
        df = data.copy()

        self._add_bollinger_bands(df)
        self._add_rsi(df)
        self._add_atr(df)
        self._add_ema_trend(df)

        self._add_entry_signals(df)
        self._add_positions_and_risk(df)

        return df

    # --------------------------------------------------------------------- #
    # Indicator calculations
    # --------------------------------------------------------------------- #
    def _add_bollinger_bands(self, df: pd.DataFrame) -> None:
        close = df["close"]
        rolling_mean = close.rolling(window=self.config.bb_window, min_periods=1).mean()
        rolling_std = close.rolling(window=self.config.bb_window, min_periods=1).std()

        df["bb_middle"] = rolling_mean
        df["bb_upper"] = rolling_mean + self.config.bb_std * rolling_std
        df["bb_lower"] = rolling_mean - self.config.bb_std * rolling_std

    def _add_rsi(self, df: pd.DataFrame) -> None:
        close = df["close"]
        delta = close.diff()

        gain = np.where(delta > 0, delta, 0.0)
        loss = np.where(delta < 0, -delta, 0.0)

        roll_up = pd.Series(gain, index=close.index).ewm(
            span=self.config.rsi_period, adjust=False
        ).mean()
        roll_down = pd.Series(loss, index=close.index).ewm(
            span=self.config.rsi_period, adjust=False
        ).mean()

        rs = roll_up / roll_down.replace(0, np.nan)
        rsi = 100.0 - (100.0 / (1.0 + rs))
        df["rsi"] = rsi

    def _add_atr(self, df: pd.DataFrame) -> None:
        high = df["high"]
        low = df["low"]
        close = df["close"]

        prev_close = close.shift(1)

        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()

        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df["atr"] = true_range.rolling(
            window=self.config.atr_period, min_periods=1
        ).mean()

    def _add_ema_trend(self, df: pd.DataFrame) -> None:
        close = df["close"]
        df["ema_trend"] = close.ewm(
            span=self.config.ema_trend_period, adjust=True
        ).mean()

    # --------------------------------------------------------------------- #
    # Signal generation and risk management
    # --------------------------------------------------------------------- #
    def _add_entry_signals(self, df: pd.DataFrame) -> None:
        """
        Entry rules:
        - Long when price touches (<=) lower band AND RSI < 30
        - Short when price touches (>=) upper band AND RSI > 70
        """
        price = df["close"]

        if self.config.use_trend_filter:
            ema = df["ema_trend"]
            long_cond = (
                (price <= df["bb_lower"])
                & (df["rsi"] < self.config.rsi_oversold)
                & (price > ema)
            )
            short_cond = (
                (price >= df["bb_upper"])
                & (df["rsi"] > self.config.rsi_overbought)
                & (price < ema)
            )
        else:
            long_cond = (price <= df["bb_lower"]) & (
                df["rsi"] < self.config.rsi_oversold
            )
            short_cond = (price >= df["bb_upper"]) & (
                df["rsi"] > self.config.rsi_overbought
            )

        signal = pd.Series(0, index=df.index, dtype="int")
        signal = signal.mask(long_cond, 1)
        signal = signal.mask(short_cond, -1)

        df["signal"] = signal

    def _add_positions_and_risk(self, df: pd.DataFrame) -> None:
        """
        Convert entry signals into positions and attach risk management:

        - 1.5% hard stop-loss
        - 3% take-profit
        - ATR-based position sizing to risk 1% of capital per trade
        """
        # Position: hold until the opposite signal is generated.
        position = df["signal"].replace(to_replace=0, value=None).ffill().fillna(0)
        df["position"] = position.astype("int")

        # Entry prices: when we change from flat or opposite direction to a new position.
        entry_price = pd.Series(np.nan, index=df.index, dtype="float")
        current_entry: Optional[float] = None
        current_side: int = 0

        for i, (idx, row) in enumerate(df.iterrows()):
            side = row["position"]
            price = row["close"]

            if side != current_side:
                # New trade (or flat)
                if side != 0:
                    current_entry = float(price)
                else:
                    current_entry = np.nan
                current_side = int(side)

            entry_price.iat[i] = current_entry if current_side != 0 else np.nan

        df["entry_price"] = entry_price

        # Stop-loss and take-profit levels
        sl_pct = self.config.stop_loss_pct
        tp_pct = self.config.take_profit_pct

        long_mask = df["position"] == 1
        short_mask = df["position"] == -1

        df["stop_loss"] = np.nan
        df["take_profit"] = np.nan

        # Hard stop-loss is always percentage-based.
        df.loc[long_mask, "stop_loss"] = df.loc[long_mask, "entry_price"] * (
            1.0 - sl_pct
        )
        df.loc[short_mask, "stop_loss"] = df.loc[short_mask, "entry_price"] * (
            1.0 + sl_pct
        )

        # Take-profit: either fixed 3% or dynamic at the opposite Bollinger band.
        if self.config.use_opposite_band_exit:
            df.loc[long_mask, "take_profit"] = df.loc[long_mask, "bb_upper"]
            df.loc[short_mask, "take_profit"] = df.loc[short_mask, "bb_lower"]
        else:
            df.loc[long_mask, "take_profit"] = df.loc[long_mask, "entry_price"] * (
                1.0 + tp_pct
            )
            df.loc[short_mask, "take_profit"] = df.loc[
                short_mask, "entry_price"
            ] * (1.0 - tp_pct)

        # ATR-based position sizing:
        # Risk per trade = 1% of capital.
        # We combine the 1.5% hard stop with ATR by taking the larger of:
        # - price * stop_loss_pct
        # - ATR (volatility-based distance)
        risk_capital = self.config.initial_capital * self.config.risk_per_trade

        df["position_size"] = 0.0

        for i, (idx, row) in enumerate(df.iterrows()):
            side = row["position"]
            price = row["entry_price"]
            atr = row["atr"]

            if side == 0 or pd.isna(price) or pd.isna(atr):
                continue

            # Effective per-unit risk (in price terms)
            hard_stop_risk = price * self.config.stop_loss_pct
            effective_risk_per_unit = max(hard_stop_risk, atr)

            if effective_risk_per_unit <= 0 or np.isnan(effective_risk_per_unit):
                continue

            size = risk_capital / effective_risk_per_unit
            if side == -1:
                size = -size

            df.at[idx, "position_size"] = size

