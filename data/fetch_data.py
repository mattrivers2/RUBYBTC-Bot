from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ccxt
import pandas as pd


DATA_DIR = Path(__file__).resolve().parent


def _fetch_symbol_ohlcv(
    exchange: ccxt.Exchange,
    symbol: str,
    timeframe: str,
    since_ms: int,
    limit: int = 1000,
) -> pd.DataFrame:
    """
    Fetch OHLCV data in batches from an exchange and return as a DataFrame.
    """
    all_candles = []
    since = since_ms

    while True:
        candles = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=limit)
        if not candles:
            break

        all_candles.extend(candles)

        # Advance 'since' to the last candle's timestamp + 1ms to avoid overlap
        last_ts = candles[-1][0]
        since = last_ts + 1

        # Respect rate limits
        time.sleep(exchange.rateLimit / 1000.0)

        # Safety: stop if we've fetched enough history (exchange-dependent)
        if len(candles) < limit:
            break

    if not all_candles:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    df = pd.DataFrame(
        all_candles, columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df


def fetch_last_two_years_15m(symbols: list[str]) -> None:
    """
    Fetch the last 2 years of 15-minute OHLCV data for the given symbols from
    Binance (or compatible) and save them as CSV files in the data directory.
    """
    exchange = ccxt.binanceus({"enableRateLimit": True})

    now = datetime.now(timezone.utc)
    since_dt = now - timedelta(days=365 * 2)
    since_ms = int(since_dt.timestamp() * 1000)

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    for symbol in symbols:
        print(f"Fetching 15m OHLCV for {symbol} from {since_dt.isoformat()} to now...")
        df = _fetch_symbol_ohlcv(exchange, symbol, timeframe="15m", since_ms=since_ms)
        if df.empty:
            print(f"No data returned for {symbol}.")
            continue

        base = symbol.replace("/", "")
        out_path = DATA_DIR / f"{base}_15m.csv"
        df.to_csv(out_path, index=False)
        print(f"Saved {len(df)} candles for {symbol} to {out_path}")


def main() -> None:
    symbols = ["BTC/USDT", "ETH/USDT"]
    fetch_last_two_years_15m(symbols)


if __name__ == "__main__":
    main()

