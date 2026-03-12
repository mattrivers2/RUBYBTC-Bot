# RUBYBTC-Bot — Ledger Edition v4.0

A Python-based cryptocurrency paper trading bot with a Mean Reversion + Bollinger Bands core strategy, enhanced by an Intelligence Advisor layer (Supertrend, Fear & Greed, recommendation engine), persistent paper trading ledger, and dynamic virtual wallet system.

## Project Structure

- `main.py` — Entry point; launches the live paper trading loop for BTC/USDT on Binance US
- `optimize.py` — Parameter sweep tool: tests 50 RSI/Bollinger Band combinations and reports best params
- `strategies/mean_reversion.py` — `MeanReversionBollingerStrategy` with `MeanReversionConfig`; computes Bollinger Bands, RSI, ATR signals
- `execution/live_trader.py` — Main live trading loop; fetches real-time OHLCV from Binance, runs strategy, paper-logs trades
- `execution/safety_module.py` — `SafetyModule` enforcing a 5% daily loss limit with optional Discord/Telegram alerts
- `backtester/engine.py` — Historical backtesting engine used by optimize.py
- `data/fetch_data.py` — Fetches 2 years of 15-minute OHLCV data for BTC/USDT and ETH/USDT
- `data/BTCUSDT_15m.csv`, `data/ETHUSDT_15m.csv` — Historical data files
- `live_trades.log` — Runtime trade log
- `paper_trades.log` — Paper trade records
- `ruby_performance.csv` — Persistent ledger: Timestamp, Asset, Price, Signal, Units_Recommended, Dollar_Value, Current_Virtual_Balance
- `wallet.txt` — Persistent virtual balance; edit manually to adjust recommendation sizing

## Ledger & Wallet System (v4.0)

- **`wallet.txt`** — Stores the current virtual balance. Read on every (re)start so balance survives restarts. Edit the number manually (e.g., `120`) to change the sizing base.
- **`ruby_performance.csv`** — Append-only CSV ledger written on every BUY and SELL signal. All writes use a `threading.Lock` to prevent file corruption.
- **`load_wallet()` / `save_wallet()`** — Reads and writes `wallet.txt` with error handling.
- **`log_trade_signal()`** — Creates headers on first run; appends rows thread-safely thereafter.
- **Dynamic sizing** — `build_recommendation()` now takes `account_balance` as a live argument instead of a hardcoded constant. Changing `wallet.txt` to `120` scales all recommendations to `$120` base automatically.
- **P&L Tracker** — Every Discord embed includes a "📈 Performance Tracker" block: Current Paper Wallet and Total Growth % vs the $100 starting baseline.

## Intelligence Advisor Layer (v3.0)

- **Supertrend** — `pandas_ta.supertrend(length=10, multiplier=3.0)` on the live 15m dataframe. BULLISH if direction=1, BEARISH if direction=-1.
- **Fear & Greed** — `get_sentiment()` calls `https://api.alternative.me/fng/`. Returns `value` (0-100) and `classification`. Fails gracefully to "Neutral" if the API is down.
- **Recommendation Engine** — `build_recommendation()` calculates unit size:
  - Base: RSI < 35 AND price < BB lower → 3.0 units
  - +1.0 if Supertrend is BULLISH
  - +1.3 if Fear & Greed < 25
  - HOLD if no conditions met
  - `ACCOUNT_BALANCE = 100`, `BASE_UNIT = 1.00` (1% of balance per unit)
- **Discord embed** — Scan embeds now include a "💎 Ruby Executive Strategy" section with Recommendation, Conviction (🔥 scale), and a 2-line Analysis Summary.

## Dependencies

- `ccxt==4.4.98` — Crypto exchange connectivity (pinned; 4.5.x has a broken lighter_client dependency)
- `pandas==3.0.1` — Data manipulation
- `pandas_ta` — Technical indicators (Supertrend)
- `requests==2.32.3` — HTTP for Discord/Telegram webhooks and Fear & Greed API
- `python-dotenv==1.0.1` — Environment variable loading

## Environment Variables (Optional)

Set these in Secrets for notifications:
- `DISCORD_WEBHOOK_URL` — General Discord updates
- `DISCORD_ALERTS_WEBHOOK_URL` — Trade alert channel
- `DISCORD_STATUS_WEBHOOK_URL` — Status/system channel
- `TELEGRAM_BOT_TOKEN` — Telegram bot token
- `TELEGRAM_CHAT_ID` — Telegram chat ID
- `BINANCE_API_KEY` / `BINANCE_API_SECRET` — For live (non-paper) trading

## Workflow

- **Start application** — Runs `python3 main.py` (console mode); starts live paper trading on BTC/USDT

## Deployment

Configured as a `vm` deployment (always-running bot) using `python3 main.py`.

## Notes

- The bot runs in public/paper mode by default — no API keys required for data fetching
- ccxt 4.5.x versions (4.5.41+) have a broken `lighter_client` module; use 4.4.98
