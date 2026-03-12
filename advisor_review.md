## Project State Summary (Phase 6)

### Directory tree (trading-bot scope)

Current relevant project structure under the workspace root:

- `main.py`
- `requirements.txt`
- `data/`
  - `__init__.py`
- `strategies/`
  - `__init__.py`
  - `mean_reversion.py`
- `backtester/`
  - `__init__.py`
  - `engine.py`
- `execution/`
  - `safety_module.py`
  - `live_trader.py`
- `live_trades.log` (created at runtime by the live trader)

### Major modules and their responsibilities

- **`main.py`**
  - Ensures the core folder structure (`data/`, `strategies/`, `backtester/`, `execution/`) exists.
  - Currently serves as a simple initializer and placeholder for future orchestration.

- **`strategies/mean_reversion.py`**
  - **`MeanReversionConfig`**: Dataclass holding all strategy parameters:
    - Capital and risk: `initial_capital`, `risk_per_trade`.
    - Indicator settings: Bollinger Bands (20-period, 2 standard deviations), RSI (14-period, 30/70 bounds), ATR (14-period).
    - Risk management: fixed 1.5% stop-loss and 3% take-profit per trade.
  - **`MeanReversionBollingerStrategy`**:
    - Computes indicators on OHLC data:
      - Bollinger Bands (`bb_middle`, `bb_upper`, `bb_lower`) on `close`.
      - RSI on `close`.
      - ATR on `high/low/close`.
    - Generates:
      - **Entry signals**:
        - Long: `close <= lower_band` and `RSI < 30` (oversold).
        - Short: `close >= upper_band` and `RSI > 70` (overbought).
      - **Positions**:
        - Converts discrete signals into held positions (long / short until reversal).
      - **Risk levels**:
        - Per-position `entry_price`, `stop_loss`, `take_profit`.
      - **Position sizing**:
        - Uses ATR-based effective risk per unit (max of price-based stop distance and ATR).
        - Sizes each trade so that per-trade risk is ~1% of the configured capital.
    - Public API: `generate_signals(df)` returns a DataFrame with all derived columns for downstream backtesting/execution.

- **`backtester/engine.py`**
  - **Constants**:
    - `FEE_RATE = 0.001`: 0.1% exchange fee per trade side.
    - `SLIPPAGE_PCT_ENTRY = 0.0005`: 0.05% adverse slippage applied to entry execution prices.
  - **`BacktestResult`**:
    - Aggregated performance metrics:
      - `total_net_profit`
      - `win_rate` (percentage of profitable trades)
      - `max_drawdown` (fractional peak-to-trough)
      - `profit_factor` (gross profit / |gross loss|)
      - `equity_curve` (`pandas.Series`)
      - `trades` (`pandas.DataFrame` of all closed trades)
      - `risk_of_ruin` (Monte Carlo-estimated probability of hitting zero equity)
      - `position_size_scale` (recommended linear scaling factor for position sizes / per-trade risk)
  - **`load_ohlcv(csv_path)`**:
    - Loads historical OHLCV from CSV, normalizes column names to lower case, and (optionally) sets a `timestamp` index.
  - **`run_backtest(csv_path, strategy=None)`**:
    - Runs the `MeanReversionBollingerStrategy` over historical data.
    - Simulates trades bar-by-bar:
      - **Entry**:
        - Enters when the strategy’s `position` changes from flat to long/short and `position_size` is non-zero.
        - Applies **slippage**:
          - Long entries at `close * (1 + 0.0005)`.
          - Short entries at `close * (1 - 0.0005)`.
        - Deducts entry fees (0.1% of notional).
      - **Exit**:
        - Exits when price hits stop-loss or take-profit (on close), or when the strategy reverses/flat-lines.
        - Deducts exit fees (0.1% of notional).
        - Records each trade with entry/exit time, side, prices, size, PnL (net and gross), and total fees.
      - Tracks equity over time and computes `max_drawdown`.
    - Aggregates trade statistics into a `BacktestResult`.
  - **Monte Carlo risk of ruin**:
    - **`_monte_carlo_risk_of_ruin(pnls, initial_capital, num_runs=1000, scale=1.0)`**:
      - Randomly permutes the sequence of historical trade PnLs 1,000 times.
      - For each run, applies scaled PnLs sequentially from the initial capital and checks whether equity ever falls to zero or below.
      - Returns the fraction of runs that go bust (estimated **Risk of Ruin**).
    - **`_estimate_risk_of_ruin_and_scale(pnls, initial_capital, num_runs=1000, target_ror=0.01)`**:
      - Computes risk of ruin at current sizing (`scale = 1.0`).
      - If risk of ruin is above 1%, iteratively tests lower linear scalings of trade PnLs (e.g., 0.5x, 0.25x, …) to approximate a scaling factor where risk of ruin falls at or below 1%.
      - Returns:
        - `risk_of_ruin`: simulated probability of ruin at the chosen scale.
        - `position_size_scale`: suggested multiplier to apply to position sizes / `risk_per_trade` to respect the 1% ruin threshold.

- **`execution/safety_module.py`**
  - **`SafetyConfig`**:
    - `max_daily_loss_pct = 0.05` (5% max daily loss).
    - Optional Discord Webhook URL and Telegram bot credentials for alerting.
    - Timezone configuration (UTC by default) for defining “daily” boundaries.
  - **`SafetyModule`**:
    - Tracks **start-of-day account balance** and monitors equity for daily drawdown.
    - If daily loss exceeds 5%:
      - Closes **all positions** (via ccxt-style `fetch_positions` / opposing market orders, and best-effort open-order cancellations).
      - Sends notifications via Discord and/or Telegram.
      - Enforces a **24-hour shutdown** (no trading allowed until the cooldown expires).
    - Exposed via `check_and_enforce(current_balance, exchange) -> bool`, which is intended to gate any trading logic (returns `False` during shutdown).

- **`execution/live_trader.py`**
  - **Exchange connectivity**:
    - `create_exchange(exchange_id, api_key=None, api_secret=None, password=None, testnet=True)`:
      - Creates a ccxt exchange client configured for **testnet/sandbox** where supported (e.g., Binance, Bybit).
      - Reads API credentials from environment variables when not provided explicitly.
  - **Market data**:
    - `fetch_ohlcv_15m(exchange, symbol, limit=150)`:
      - Wraps `exchange.fetch_ohlcv` for the 15-minute timeframe, returning a timestamp-indexed OHLCV DataFrame.
  - **Live execution loop**:
    - `run_live_trading_loop(exchange_id, symbol, base_currency, poll_interval_seconds=30)`:
      - Connects to the specified exchange in testnet mode.
      - Continuously fetches the latest 15-minute candles, runs `MeanReversionBollingerStrategy.generate_signals`, and reacts only on **newly completed bars**.
      - Calls `SafetyModule.check_and_enforce` with current account equity in `base_currency` to enforce the 5% daily loss and 24-hour shutdown.
      - When a new long/short entry is signaled and position size is non-zero:
        - Sends a market buy/sell order via ccxt.
        - Logs every Buy/Sell signal and its **reason** (Bollinger + RSI conditions) and order outcome.
    - `main()`:
      - Reads configuration (`EXCHANGE_ID`, `SYMBOL`, `BASE_CCY`) from environment variables and starts the live loop.
  - **Logging**:
    - Uses the standard `logging` module to write all live trade signals and order events to `live_trades.log` in the project root.

### Current Monte Carlo and Backtest metrics

At this stage, the backtesting engine is fully instrumented to compute:

- **Backtest performance metrics** (from `BacktestResult`):
  - `total_net_profit`
  - `win_rate`
  - `max_drawdown`
  - `profit_factor`
  - `equity_curve`
  - `trades`
- **Monte Carlo risk metrics**:
  - `risk_of_ruin` (based on 1,000 random permutations of historical trade PnLs).
  - `position_size_scale` (suggested risk scaling to keep risk of ruin at or below 1%).

Because the backtester has not yet been run in this environment with a specific dataset, there are **no concrete numeric results** to paste yet. Once the user runs:

```python
from backtester.engine import run_backtest

result = run_backtest("data/your_ohlcv.csv")
print("Net Profit:", result.total_net_profit)
print("Win Rate (%):", result.win_rate)
print("Max Drawdown:", result.max_drawdown)
print("Profit Factor:", result.profit_factor)
print("Risk of Ruin:", result.risk_of_ruin)
print("Suggested Position Size Scale:", result.position_size_scale)
```

the resulting scalar values can be inserted here directly for advisor review.

### To-Do items and open issues

- **Data layer**
  - Implement concrete CSV/data download utilities in `data/` (e.g., historical fetchers from exchanges and standardized OHLCV storage).
  - Add basic data validation and cleaning (handling gaps, bad ticks, timezone normalization).

- **Backtesting**
  - Build a small CLI or `main.py` wrapper to:
    - Select data files and symbols.
    - Run `run_backtest` and output summary metrics and plots.
  - Add support for:
    - Multiple symbols / portfolios.
    - Parameter sweeps (e.g., Bollinger and RSI parameters) and walk-forward testing.

- **Risk and Monte Carlo**
  - Optionally persist Monte Carlo paths / summary statistics to disk for auditability.
  - Add visualizations (histogram of final equity, distribution of drawdowns across Monte Carlo runs).
  - Integrate `position_size_scale` back into `MeanReversionConfig.risk_per_trade` automatically when running calibration scripts.

- **Execution**
  - Add configurable mapping for different exchanges (Binance vs Bybit nuances, futures vs spot, leverage, contract sizing).
  - Implement more robust error handling and reconnection logic in `live_trader.py` (e.g., exponential backoff, rate-limit handling).
  - Extend order execution to support partial fills and position reconciliation (matching back actual fills with strategy state).

- **Safety module**
  - Enhance `_close_all_positions` to support:
    - Spot-only accounts (selling spot balances safely and idempotently).
    - Explicit logging of which positions/orders could not be closed and why.
  - Add persistence for safety state (e.g., writing shutdown state to disk) so that a restarted bot honors an ongoing 24-hour cooldown.

- **Testing and validation**
  - Add unit tests for:
    - Indicator calculations (Bollinger, RSI, ATR).
    - Strategy signal logic (expected signals on synthetic price paths).
    - Backtester PnL and fee/slippage handling on small, known datasets.
    - Monte Carlo functions (`_monte_carlo_risk_of_ruin`, `_estimate_risk_of_ruin_and_scale`).
    - Safety and live-trading integration.
  - Integrate a simple test dataset in `data/` and a smoke-test script to validate end-to-end behaviour.

- **Current known gaps / non-blocking issues**
  - No real numeric backtest results are available in this environment yet; advisor review is based on **code structure and logic**, not live metrics.
  - Execution connector is designed for ccxt-compatible exchanges; Interactive Brokers support would require a separate integration path outside ccxt.
  - Spot vs derivatives behaviour in `_close_all_positions` is intentionally conservative and may need exchange-specific refinement before production use.

