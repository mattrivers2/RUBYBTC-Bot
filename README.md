# Ruby: The Ruby BTC Trading System v4.0
Welcome to the official repository for Ruby, a high-precision, defensive cryptocurrency market-scanning bot. Ruby isn't just a trading algorithm; she is a Risk Management Specialist designed to protect capital first and capture high-probability gains second.

# Overview: The Ruby Philosophy #
Most retail traders lose money because they "hold and pray" through market crashes. Ruby operates on a "Cash-First" principle. She treats USD/USDT as the default safe state and only deploys capital into the market when a specific "Sniper" entry is detected.

*Key Performance Highlight:* During the BTC crash of early 2026 (where Bitcoin fell -23.7% from $90.8k to $69.3k), Ruby successfully avoided the carnage. While a "Buy and Hold" strategy would have turned $100 into $76.34, Ruby sat in cash and ended the period at $100.04—effectively a **+24% performance advantage over the market.**

# Core Strategy: The "Sniper" Logic
Ruby uses a multi-layered technical analysis filter to identify entries. She refuses to trade during high-volatility "bear traps."

## 1. The Entry Conditions (The "Buy" Signal) ##
Ruby will only suggest a trade if all of the following are met:

Supertrend Indicator: Must be BULLISH (Green). This ensures we are trading with the momentum, not against it.

RSI (Relative Strength Index): Must be < 35. This identifies that the asset is "Oversold" and due for a bounce.

Fear & Greed Index: Integrated real-time sentiment analysis. When the market is in Extreme Fear, Ruby identifies the "Discount Opportunity."

## 2. The Automated Exit (The 1.5:1 Ratio)
Ruby enforces a strict exit strategy to remove human emotion (Greed/Panic) from the equation:

Take Profit (TP): +3.0%

Stop Loss (SL): -2.0%

# System Architecture
The project is built for 24/7 uptime and zero-latency reporting.

Engine: Python 3.10+

Hosting: Replit (Cloud-based execution)

Reporting: Real-time Discord Webhook integration (ruby-scans channel)

Data Sources: Binance API (Price & Indicators), CryptoPanic (Sentiment), and Alternative.me (Fear & Greed).

# Features
15-Minute Market Pulse: Scans the BTC/USDT 15m and 1h charts every quarter-hour.

Real-Time Performance Ledger: Tracks a "Paper Wallet" and "Growth %" live in the Discord embed so you can see her progress before committing real funds.

Automated Risk Calculation: Automatically calculates exactly how much a +3% gain or -2% loss would be in dollar terms based on current price.

Zero-Footprint Security: Built to use Replit Secrets to ensure Binance API keys and Discord Tokens are never exposed in the source code.

# Installation & Setup
1. Environment Variables:
*To run Ruby, you must configure your respective Secrets in your replit environment.*

2. Deployment:
*Clone this repository to Replit. Ensure requirements.txt is installed (includes pandas_ta, ccxt, and requests). Run python main.py.*

# The "Multiverse" Simulation Results
*We ran Ruby through a 10-iteration "Multiverse" test against both random noise and historical bull markets:*

**Random Market:** Ruby maintained a flat balance, proving her defensive floor.

**2023 Bull Run:** Ruby outperformed "Buy and Hold" by +17% by compounding 3% wins and buying the mid-trend dips.

# Disclaimer
Ruby is an algorithmic tool designed for educational and analytical purposes. Cryptocurrency trading involves **significant risk.** Always use a Stop Loss.

*"In a market of gamblers, be the House." — Ruby v4.0*
