# Build Prompt: "IndexEdge" — Nifty 50 & Bank Nifty Trading Assistant for Angel One

Paste everything below into Claude Code.

---

You are building **IndexEdge**, a self-hosted trading assistant for Indian index trading (Nifty 50 and Bank Nifty — index, futures and options). It connects to **Angel One SmartAPI**, analyses market data in real time, produces trade signals with **calibrated probabilities**, explains every signal, can place/close orders on Angel One under strict risk controls, and keeps a full history for review and study.

Work in phases (listed at the end). Before writing code, produce `docs/ARCHITECTURE.md` and a task plan, then build phase by phase with tests. Ask me before making any decision that affects real-money order placement.

## 1. Hard constraints

1. **No LLM / AI-token usage at runtime.** The running app must never call Claude, OpenAI, Gemini or any paid LLM API. All intelligence must come from: rule-based strategies, technical/statistical calculations, and classical ML models trained and run locally (e.g. LightGBM/XGBoost, logistic regression). Local open-source NLP models (e.g. FinBERT via `transformers`, or VADER/lexicon methods) are allowed for news sentiment because they run on my machine with no tokens. Internet access is allowed for market data and news.
2. **Regulatory compliance (India, SEBI retail algo framework in force since 1 April 2026).** Design for these, and verify each against the current SmartAPI docs (https://smartapi.angelone.in/docs) before implementing:
   - Orders are accepted only from my **registered static IP** linked to my SmartAPI app. The order-placing service must run on a machine/VPS with that static IP. Make the IP a config value and show a startup check that warns if the public IP doesn't match.
   - **Market and IOC orders are not allowed for algo orders** on Angel One. Use LIMIT orders (with a configurable price buffer / marketable-limit logic) and SL-L for stop-losses. Implement order chasing/modification for unfilled limit orders.
   - API sessions end daily; implement a **daily login** flow (client code + PIN + TOTP via `pyotp`). If Angel One requires manual 2FA, show a "Login for today" prompt in the UI instead.
   - Stay **below 10 orders per second** (hard rate limiter) so no exchange algo registration is needed for personal use.
   - No AMO orders from the algo.
   - Keep this app strictly for my own account — no multi-user features, no selling signals.
3. **Safety first.** Default mode is **Paper Trading**. Live trading requires an explicit toggle, a typed confirmation, and passing all risk checks. A global **Kill Switch** must cancel open orders and square off all positions in one click.
4. **Honesty about accuracy.** Never display a made-up "accuracy %". Every probability shown must come from out-of-sample backtests / walk-forward validation, with sample size and confidence interval, and be **calibrated** (reliability curve, Brier score). If a setup has fewer than N historical occurrences (configurable, default 50), label it "insufficient data".
5. Secrets (API key, client code, PIN, TOTP secret) go in `.env` / OS keyring, never in code, logs or git. Add `.env.example` and `.gitignore`.

## 2. Tech stack (adjust only with justification)

- **Backend:** Python 3.11+, FastAPI, asyncio, `smartapi-python` (official SDK) incl. SmartWebSocketV2 for live ticks and the order-update WebSocket.
- **Data:** PostgreSQL + TimescaleDB (or DuckDB/Parquet for a lighter setup) for ticks/candles; Redis for live state and pub/sub.
- **Analytics:** pandas, numpy, TA-Lib or `pandas-ta`, scikit-learn, LightGBM, SHAP, vectorbt or a custom event-driven backtester.
- **Scheduling:** APScheduler (pre-market jobs, news polling, EOD jobs).
- **Frontend:** React + TypeScript + Vite, TradingView **Lightweight Charts** for candles/indicators, Tailwind, WebSocket updates from the backend. Must work on desktop and phone browser (PWA).
- **Notifications:** Telegram bot and/or browser push for alerts (Telegram bot API is free, no AI tokens).
- **Deployment:** Docker Compose; one service designated as the order executor that runs on the static-IP VPS.

## 3. Modules to build

### 3.1 Broker connector (Angel One)
- Login/session management, auto token refresh within the day, daily re-login.
- Download and cache the **instrument master** daily; derive Nifty/Bank Nifty futures and option tokens, lot sizes and **expiry dates from the master, never hard-coded** (expiry rules and lot sizes change).
- Live market data via WebSocket (LTP, OHLC, volume, OI, market depth), reconnect with backoff, gap backfill from historical API.
- Historical candles (1m, 3m, 5m, 15m, 1h, 1D) with rate-limit handling and local caching.
- Orders: place, modify, cancel, order book, trade book, positions, holdings, funds/margin, GTT where useful. Listen to order-status updates.
- A **broker abstraction interface** so a simulated broker (paper trading) and Angel One share the same API.

### 3.2 Market data & feature engine
Compute in real time for Nifty 50, Bank Nifty, their futures and the nearby option strikes:
- Price/trend: EMAs (9/21/50/200), Supertrend, VWAP + bands, ADX, Ichimoku (optional), market structure (HH/HL/LH/LL), CPR, pivot points, previous day/week high-low, opening range.
- Momentum: RSI, MACD, Stochastic RSI, rate of change, divergences.
- Volatility: ATR, Bollinger Bands/Keltner squeeze, India VIX, realised vs implied volatility.
- **Volume:** relative volume, volume spikes, OBV, volume profile (POC/VAH/VAL), anchored VWAP, futures volume vs price.
- **Options data:** option chain (OI, change in OI, PCR, max pain, OI build-up classification — long build-up/short covering/etc.), IV and IV percentile, Greeks (compute with Black-Scholes locally), straddle price, support/resistance from OI walls.
- **Breadth & context:** heavyweight constituents' moves (e.g. HDFC Bank, ICICI Bank, Reliance, etc. — load weights from config), advance/decline, Bank Nifty vs Nifty relative strength, GIFT Nifty and global cues where a free source exists.
- **Candlestick & chart patterns** detected algorithmically (engulfing, pin bar, inside bar, flags, breakouts/retests), with each detection stored as a feature.
- Market regime classifier: trending up / trending down / range / high-volatility, used to switch strategies on/off.

### 3.3 Strategy library (rule-based, each a plug-in class)
Each strategy declares: instruments, timeframe, regime it works in, entry rules, stop-loss, targets, trailing logic, time filters, and **human-readable reasons** for each condition. Start with:
1. Opening Range Breakout (15-min) with volume confirmation.
2. VWAP pullback / VWAP reclaim.
3. EMA 9/21 crossover + Supertrend trend-follow with ADX filter.
4. CPR + pivot breakout/rejection.
5. Bollinger/Keltner squeeze breakout.
6. RSI divergence reversal at key levels.
7. OI-based support/resistance + PCR shift.
8. Expiry-day strategies (e.g. straddle/strangle selling with defined risk, gamma-move breakout) — gated behind a separate toggle.
9. Gap-up/gap-down fill or continuation.

Strategies must be **data-driven, not assumed to work**: every strategy is backtested and only "enabled for live signals" if it passes configurable thresholds (e.g. profit factor > 1.3, sufficient trades, max drawdown limit, positive expectancy after costs).

### 3.4 Prediction & probability engine (local ML)
- Label historical setups (e.g. "did price hit target before stop within X minutes").
- Train LightGBM/logistic models on the feature set with **walk-forward / time-series cross-validation only** (no random shuffles, no look-ahead leakage). Include a leakage test.
- Calibrate with isotonic or Platt scaling; show reliability diagram.
- Final signal = strategy trigger + model probability + news/event adjustment + regime filter → **confidence score** with expected win rate, expected R:R, expected value per trade after brokerage, STT, exchange charges, GST, stamp duty and slippage.
- Retrain on a schedule (weekly, after market) and track model drift; auto-disable a model whose live performance falls outside its backtest confidence band.

### 3.5 Explanation engine ("why it worked / didn't")
- For every signal: list each satisfied condition in plain English, top SHAP feature contributions, current regime, relevant news, and the historical stats of similar setups.
- After each trade closes, generate a **post-trade analysis** using templates (no LLM): entry quality, how price behaved vs the plan (MFE/MAE), which conditions held or broke, whether news/volatility changed, slippage, rule violations, and a classification of the outcome (good trade/good result, good trade/bad result, bad trade/good result, bad trade/bad result).
- Weekly and monthly summaries: which strategies/regimes/times of day/days of week work best for me.

### 3.6 News & event intelligence (no AI tokens)
- Poll free sources via RSS/HTML: e.g. NSE corporate announcements, RBI press releases, SEBI updates, Moneycontrol/Economic Times/LiveMint/Business Standard markets RSS, PIB, and global macro headlines. Make the source list configurable; respect robots.txt and terms of use.
- Economic/event calendar: RBI policy, Union Budget, US Fed (FOMC), US CPI/NFP, India CPI/GDP, F&O expiry days, results of Bank Nifty and Nifty heavyweights, market holidays (fetch NSE holiday list).
- Classify each item locally: relevance (keyword + entity matching to Nifty/Bank Nifty constituents and sectors), sentiment (FinBERT or lexicon), and impact level (rules + historical reaction of similar events).
- **Alerts** to UI + Telegram for high-impact items; automatically widen stops, reduce size or pause new entries around scheduled high-impact events (configurable).
- Feed a numeric news-sentiment / event-risk score into the prediction engine.

### 3.7 Execution & trade management ("agent")
Three modes, switchable per strategy:
- **Signal only** — alerts with full plan (entry, SL, targets, size, probability, reasons).
- **One-tap confirm** — the app prepares the order; I approve in the UI or via Telegram button; then it executes.
- **Auto** — executes and manages trades within risk limits.
Trade management: limit-order entry with chase logic, SL-L stop orders placed immediately, partial booking at targets, trailing stop (ATR/Supertrend/structure), time-based exit, forced square-off before the broker's intraday cutoff, and handling of partial fills, rejections and disconnections (reconcile positions with the broker on reconnect).

### 3.8 Risk management (enforced in code, not just displayed)
- Position sizing by fixed % of capital at risk per trade; lot-size aware.
- Max daily loss, max daily trades, max consecutive losses → auto-lock trading for the day.
- Max open positions, max exposure per index, margin check before each order.
- No trading in the first/last N minutes (configurable), during event blackout windows, or when the data feed is stale.
- "Tilt" protection: cooldown after a loss; block revenge-sized trades.
- All limits editable only outside market hours (prevents emotional changes mid-session).

### 3.9 Backtesting & paper trading lab
- Event-driven backtester using the same strategy code as live (no separate logic), with realistic Indian F&O costs and slippage, lot sizes and expiry handling.
- Walk-forward optimisation with overfitting warnings (too many parameters, unstable results across periods).
- Monte Carlo of trade sequences for drawdown estimates.
- Replay mode: replay any past day candle by candle, see signals as they would have appeared — for study and practice.
- Paper trading uses live data and the simulated broker, logging identical records to live trades.

### 3.10 Journal, history & study
- Every signal (taken or not), order, fill, and exit is stored with the full feature snapshot, reasons, news context and chart screenshot data.
- Searchable/filterable history; add my own notes, tags and emotions per trade.
- Analytics dashboard: equity curve, drawdown, win rate, profit factor, expectancy, average R, by strategy/regime/time/day/instrument; comparison of planned vs actual execution; signals I skipped and how they would have done.
- Export to CSV/Excel.

### 3.11 UI pages
1. **Dashboard** — live Nifty/Bank Nifty charts, regime, VIX, PCR, key levels, active signals with probability gauges, open positions with live P&L, risk meters (daily loss used, trades used), kill switch.
2. **Signals** — current and recent signals with expandable "why" panel.
3. **Option Chain** — OI, change in OI, IV, Greeks, OI build-up heatmap, max pain.
4. **News & Events** — live feed with impact/sentiment tags, upcoming events calendar, blackout windows.
5. **Trades & Journal** — history, post-trade analysis, notes.
6. **Analytics** — performance breakdowns.
7. **Strategy Lab** — backtests, walk-forward results, enable/disable strategies, parameter settings.
8. **Replay** — past-day replay.
9. **Settings** — broker connection status, static IP check, mode (paper/live), risk limits, notifications, data sources.
Include a **pre-market checklist** screen (global cues, gap expectation, key levels, events today, which strategies are enabled for today's regime) and an **end-of-day report** (auto-generated, also sent to Telegram).

### 3.12 Reliability & monitoring
- Health panel: WebSocket status, data latency, last tick time, broker API errors, order rate.
- Structured logs, audit trail of every order request/response.
- Automatic safe state on errors: stop new entries, keep protective stops alive, alert me.
- Unit tests for indicators, strategies, risk rules and order flows; integration tests against the simulated broker; a dry-run test against Angel One that does not place orders.

## 4. Project structure (suggested)

```
indexedge/
  backend/
    broker/        # angel_one.py, paper_broker.py, base.py
    data/          # ingestion, instrument master, storage
    features/      # indicators, volume, options, patterns, regime
    strategies/    # one file per strategy + base class
    models/        # training, calibration, inference, drift
    news/          # sources, parsers, classifier, calendar
    execution/     # order manager, trade manager, rate limiter
    risk/          # risk engine
    backtest/      # engine, costs, walk-forward, monte carlo, replay
    journal/       # trade records, post-trade analysis
    api/           # FastAPI routes + websocket
    notify/        # telegram, push
    config/
    tests/
  frontend/
  docker-compose.yml
  docs/
```

## 5. Build phases

1. **Foundation:** repo, config, secrets handling, Angel One login, instrument master, historical data download & storage, live WebSocket ticks, basic dashboard with live chart.
2. **Analysis:** feature engine, option chain, regime classifier, chart overlays.
3. **Strategies + backtester:** strategy plug-ins, cost model, walk-forward, Strategy Lab UI.
4. **Probability engine:** labelling, ML training, calibration, explanation engine.
5. **News & events:** sources, classifier, calendar, alerts, Telegram.
6. **Paper trading:** simulated broker, trade manager, risk engine, journal, analytics, replay.
7. **Live execution:** Angel One order flow, one-tap confirm, auto mode, kill switch, reconciliation, static-IP check — only after I confirm paper results.
8. **Polish:** PWA, EOD reports, pre-market checklist, monitoring, documentation (`README`, setup guide incl. static IP/VPS setup, user guide).

At the end of each phase: run tests, show me what works, list known limitations, and wait for my go-ahead before the next phase.

## 6. Extra features to include if time allows
- Multi-timeframe confluence score.
- Correlation monitor (Nifty vs Bank Nifty vs USD/INR vs US futures).
- "What-if" position simulator for options payoff before taking a trade.
- Smart alerts on custom conditions I define in the UI (no code).
- Personal-edge finder: discovers which conditions my *own* best trades have in common.
- Daily discipline score (rules followed vs broken).

## 7. Disclaimer to show in the app
Display on first launch and in Settings: signals are probabilistic, past performance does not guarantee future results, F&O trading carries high risk of loss, and the user is responsible for all trades.
