# IndexEdge — Architecture & Design Spec

Status: **Draft for review** · Date: 2026-10-16
Source requirements: [`BUILD_PROMPT.md`](BUILD_PROMPT.md) (the full original brief). This document records the
design decisions taken on top of that brief. Where the two differ, this document wins.

---

## 1. Goal and success criteria

IndexEdge is a self-hosted, single-user trading assistant for **Nifty 50 and Bank Nifty** (index, futures, options)
on **Angel One SmartAPI**. It should:

1. Install easily on a **Windows home PC** and be usable from desktop and phone browsers (PWA) on home Wi-Fi.
2. Check **external market context** (global/derivative indices, VIX, currency, commodities, flows, news, events)
   *before* any signal is produced.
3. Produce **early trade suggestions** from validated, widely-used Indian strategies, each with an honest,
   measured probability and a plain-English explanation.
4. Help the user **learn**: strategy explainers, replay practice, post-trade analysis.
5. Optionally execute and manage trades on Angel One under hard risk controls, compliant with SEBI's retail algo
   framework.

Success = signals whose displayed probabilities match realised outcomes (calibration), zero rule violations in
execution, and a one-command install that survives daily use.

## 2. Key decisions (agreed during design)

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | **Accuracy is measured, never claimed.** Every number shown comes from out-of-sample backtests / walk-forward with sample size and 95% CI. | No public intraday index strategy has *verified* 95–98% accuracy; such claims online omit costs, cherry-pick, or hide large losses behind high win rates. |
| D2 | Strategies with raw win rate ≥ 80% get a 🏅 **High win-rate** badge, always displayed beside R:R and expectancy, with a warning when expectancy ≤ 0. | User asked to surface high-accuracy strategies; showing R:R next to it keeps it honest. |
| D3 | **Hosting: Windows home PC** with an ISP static IP registered on SmartAPI. Phone access on home Wi-Fi only; Telegram covers alerts and one-tap confirms when away. | User choice. |
| D4 | **Native Windows install** (PowerShell + `uv`), **no Docker, no Redis, no Postgres**. Single Python process; in-process asyncio event bus; DuckDB + Parquet storage. Optional `docker-compose.yml` kept for a future VPS. | Fewest moving parts; Docker Desktop/WSL2 is heavy and fragile on a home PC. One user × two indices does not need Redis or Timescale. |
| D5 | **Strategy "internet scan" happens at build time**, not runtime. Findings go to `docs/STRATEGY_RESEARCH.md` with sources; each strategy becomes a code plug-in and must pass validation. | Runtime must use no LLM tokens; build-time research yields inspectable, testable rules. |
| D6 | A **context gate** (external indices) is mandatory in the signal pipeline: stale or missing context ⇒ no signal. | Requirement: evaluate external influences before generating suggestions. |
| D7 | New **Learn** page (strategy explainers + replay quizzes). | Market-learning goal. |

## 3. Hard constraints (from brief, unchanged)

- No runtime LLM/paid-AI API calls. Local ML (LightGBM, logistic regression) and local NLP (VADER; FinBERT optional) only.
- SEBI / Angel One algo rules — **each to be re-verified against https://smartapi.angelone.in/docs in Phase 1**:
  registered static IP for orders; LIMIT / SL-L only (no MARKET, IOC, AMO); daily login (client code + PIN + TOTP);
  hard cap of **8 orders/sec** (below the 10/sec threshold); personal account only.
- Paper mode default; live mode needs toggle + typed confirmation + passing risk checks; global kill switch.
- Secrets in Windows Credential Manager via `keyring` (fallback `.env`), never in code, logs or git.

## 4. System architecture

Single process: **FastAPI + asyncio** (`uvicorn`), started by `IndexEdge.bat` / Windows Task Scheduler at 08:25 IST.

```
Angel One SmartAPI ──► broker/  (BrokerBase → AngelOneBroker | PaperBroker)
  ticks WS, order WS            │
                                ▼
external feeds ──► context/ ──► EventBus (asyncio pub/sub) ◄── news/ (RSS, calendar, local sentiment)
                                │
                                ▼
                         features/ (indicators, volume, options chain + Greeks, breadth, patterns, regime)
                                ▼
                         strategies/ (plug-ins) ──► models/ (LightGBM + isotonic calibration)
                                ▼
                         signals/composer: trigger × calibrated P × context gate × news/event gate × regime
                                ▼
                         risk/ (hard checks) ──► execution/ (limit+chase, SL-L, rate limiter, reconciliation)
                                ▼
                         journal/ (DuckDB) ──► api/ (REST + WebSocket) ──► React PWA
                                                                       └► notify/ (Telegram bot)
```

### 4.1 Modules

| Module | Responsibility | Depends on |
|--------|----------------|-----------|
| `config/` | Pydantic settings (YAML + env), secrets via keyring | — |
| `core/bus.py` | Typed async pub/sub topics (`tick`, `candle`, `context`, `news`, `signal`, `order`, `fill`, `health`) | — |
| `core/clock.py` | Market calendar, IST clock, NSE holidays; injectable for replay/backtest | config |
| `broker/` | `BrokerBase` interface; `AngelOneBroker` (smartapi-python, SmartWebSocketV2, order WS); `PaperBroker` (fill simulator on live/replay data) | core |
| `data/` | Instrument master (daily, expiries & lot sizes derived, never hard-coded), historical download + cache, tick→candle aggregation, gap backfill; Parquet store | broker |
| `context/` | External-indices engine (see §5) | data, core |
| `features/` | Indicators, volume, options analytics (BS Greeks, IV, PCR, max pain, OI build-up), breadth, patterns, regime classifier | data |
| `strategies/` | `StrategyBase` plug-ins with rules, SL/targets, trailing, regime, time filters, human-readable reasons | features |
| `models/` | Labelling, walk-forward training, calibration, inference, drift monitor, SHAP | features, strategies |
| `news/` | Source pollers, relevance/sentiment/impact classifier, event calendar, blackout windows | core |
| `signals/` | Composer: combines trigger, probability, context, news, regime → `Signal` with EV after costs | all analytics |
| `risk/` | Pre-trade checks, daily locks, tilt protection, editable-only-off-hours limits | journal, broker |
| `execution/` | Order manager (limit + chase, SL-L), trade manager (partials, trailing, time exit, square-off), rate limiter, reconciliation, kill switch | broker, risk |
| `backtest/` | Event-driven engine reusing strategy code, Indian F&O cost model, walk-forward, Monte Carlo, replay | strategies, data |
| `journal/` | DuckDB records of signals, orders, fills, trades, notes; post-trade analysis templates; analytics; CSV/XLSX export | — |
| `api/` | REST + WebSocket, serves built frontend | all |
| `notify/` | Telegram bot (alerts, EOD report, inline confirm buttons) | signals, execution |
| `frontend/` | React + TS + Vite + Tailwind + Lightweight Charts, PWA | api |

### 4.2 Storage

- **DuckDB** `data/indexedge.duckdb`: signals, orders, fills, trades, notes, news items, model registry, settings audit.
- **Parquet** `data/candles/{symbol}/{tf}/{yyyy}.parquet` and `data/ticks/{date}/…` for market data.
- **Audit log**: JSON-lines file per day of every broker request/response (secrets redacted).

### 4.3 Daily schedule (APScheduler, IST)

| Time | Job |
|------|-----|
| 08:30 | Login (TOTP), instrument master refresh, static-IP check |
| 08:35–09:14 | Context engine warm-up, news poll, pre-market checklist built |
| 09:15–15:30 | Live ticks, features, signals, execution |
| broker cutoff − N min | Forced intraday square-off (cutoff read from config, verified against Angel One) |
| 15:45 | EOD report (UI + Telegram), data compaction |
| Sat | Model retrain + drift report |

## 5. External context engine (`context/`)

Runs before and throughout the session; publishes a `ContextSnapshot` every minute.

| Input | Source (free, to verify in Phase 2) | Use |
|-------|------------------------------------|-----|
| GIFT Nifty | NSE IX / public quote pages | Gap expectation, pre-open bias |
| US index futures (ES/NQ), Dow/S&P/Nasdaq close | public quote APIs (e.g. Yahoo Finance via `yfinance`) | Global risk sentiment |
| Asian indices (Nikkei, Hang Seng, Kospi) | same | Morning cue |
| India VIX | Angel One token / NSE | Volatility regime, position sizing |
| USD/INR, DXY, US 10Y | same | FII flow pressure |
| Brent crude | same | India macro sensitivity |
| FII/DII cash & F&O data (previous day) | NSE reports | Positional bias |
| Heavyweight constituents (weights from config) | Angel One ticks | Breadth, index driver attribution |
| News & event calendar | `news/` | Blackouts, event-risk score |

Output: `macro_bias ∈ [-1, 1]`, `risk_off: bool`, `event_risk ∈ [0, 1]`, per-input freshness, and a short
list of human-readable reasons. **Gate rule:** if any *required* input is stale beyond its configured age, or
`risk_off` is true and the strategy is not flagged as risk-off-capable, the composer emits no signal and records
why. Context features are also fed to the ML model.

## 6. Strategy research & validation

1. **Research (build time, Phase 3):** survey Zerodha Varsity, Angel One learning content, popular Indian trading
   blogs/YouTube/TradingView India. Record each strategy's exact rules, source links and any *claimed* win rate in
   `docs/STRATEGY_RESEARCH.md`.
2. **Initial plug-ins:** the 9 from the brief (ORB-15 with volume, VWAP pullback/reclaim, EMA 9/21 + Supertrend +
   ADX, CPR + pivots, BB/Keltner squeeze, RSI divergence at levels, OI walls + PCR shift, expiry-day setups behind a
   toggle, gap fill/continuation) plus well-cited additions found in research (candidates: 9:20 straddle,
   CPR + VWAP combo, Heikin-Ashi trend, 3-Supertrend).
3. **Validation pipeline:** ≥3 years of data where obtainable; full Indian F&O costs (brokerage, STT, exchange
   charges, SEBI fee, GST, stamp duty) + slippage; walk-forward; Monte Carlo drawdowns; per-year and per-regime
   stability.
4. **Report card:** win rate (Wilson 95% CI), n, avg R:R, expectancy (₹ and R) after costs, profit factor, max DD,
   stability score.
5. **Badges:** 🟢 Validated (PF > 1.3, n ≥ 100, expectancy > 0, DD within limit — thresholds configurable) — the only
   badge eligible for live signals; 🏅 High win-rate (raw win rate ≥ 80%, shown with R:R and expectancy, warning if
   expectancy ≤ 0); ⚪ Insufficient data (n < 50); 🔴 Rejected.
6. **Probability:** LightGBM per strategy family on the feature set + context features, time-series CV only,
   leakage test, isotonic calibration, reliability diagram + Brier score in Strategy Lab. Auto-disable on drift
   outside the backtest confidence band.

**Historical data caveat:** SmartAPI limits intraday history depth and expired-contract data. Phase 1 will verify
these limits; NSE bhavcopy (EOD F&O) supplements older options data. Intraday option backtests may therefore have
shorter history than index/futures backtests, and their report cards will say so.

## 7. Signal composition

`Signal = StrategyTrigger ∧ ContextGate ∧ NewsGate ∧ RegimeFilter`, carrying: instrument, side, entry/SL/targets,
lot-aware size, calibrated probability with CI, expected R:R, EV after costs, badges, reasons (conditions met, top
SHAP features, context reasons, relevant news), and historical stats of similar setups.

## 8. Execution & risk

- **Modes per strategy:** Signal-only · One-tap confirm (UI or Telegram inline button) · Auto.
- **Orders:** LIMIT with configurable buffer; chase via modify up to N times then cancel; SL-L placed immediately on
  fill; partial booking; ATR/Supertrend/structure trailing; time exit; forced square-off. Token-bucket limiter at
  8 orders/s. No MARKET/IOC/AMO anywhere in the code path (enforced by a test).
- **Kill switch:** cancel all open orders → square off all positions with marketable limits → lock day.
- **Risk engine (every order passes it):** % capital risk per trade, lot-size aware; max daily loss, trades,
  consecutive losses → day lock; max open positions & per-index exposure; margin check; no trading first/last N min,
  event blackouts, stale feed; post-loss cooldown and revenge-size block; limits editable only outside market hours.
- **Static IP:** startup + hourly public-IP check vs config; mismatch blocks live orders (paper continues).
- **Safe mode** on stale feed / repeated API errors / disconnect: no new entries, protective stops kept, reconcile on
  reconnect, Telegram alert.

## 9. UI (React PWA)

Pages: **Dashboard**, **Signals** (expandable *why*), **Option Chain**, **News & Events**, **Market Context**
(external indices panel with freshness), **Trades & Journal**, **Analytics**, **Strategy Lab**, **Replay**,
**Learn** (strategy explainers + "would you take this?" replay quizzes scored against outcomes), **Settings**.
Plus pre-market checklist and EOD report. First-launch and Settings disclaimer per brief §7.

## 10. Install & run (Windows)

- `install.ps1`: installs `uv` → Python 3.11 venv → backend deps; downloads prebuilt frontend; creates desktop
  shortcut and optional Task Scheduler entry (08:25 IST weekdays); runs first-time setup wizard (API key, client
  code, PIN, TOTP secret → keyring; static IP; Telegram bot token).
- `IndexEdge.bat`: starts server, opens `http://localhost:8000`; LAN URL shown for phone.
- `update.ps1`: pulls latest release, migrates DuckDB schema.
- Optional `docker-compose.yml` for a future Linux VPS.

## 11. Testing

pytest: indicators vs reference values; strategy rule tests on fixture candles; risk-rule tests; cost model; order
flow against PaperBroker; forbidden-order-type test; look-ahead leakage test; walk-forward split test; dry-run
Angel One login + data fetch that places no orders. Frontend: type-check + component smoke tests.

## 12. Phases

1. **Foundation** — repo, config, secrets, Angel One login, instrument master, historical download & storage, live
   ticks, basic dashboard with live chart, Windows installer skeleton.
2. **Analysis** — features, option chain, **context engine**, regime classifier, chart overlays.
3. **Strategies + backtester** — strategy research doc, plug-ins, cost model, walk-forward, Strategy Lab UI.
4. **Probability engine** — labelling, ML, calibration, explanation engine.
5. **News & events** — sources, classifier, calendar, alerts, Telegram.
6. **Paper trading** — PaperBroker, trade manager, risk engine, journal, analytics, replay, Learn page.
7. **Live execution** — only after user signs off on paper results.
8. **Polish** — PWA, EOD reports, pre-market checklist, monitoring, README / setup / user guide.

At the end of each phase: run tests, demo, list limitations, wait for go-ahead.

## 13. Out of scope

Multi-user, signal selling, cloud hosting by default, runtime LLMs, MARKET/IOC/AMO orders, stocks other than
index constituents used for breadth.

## 14. Disclaimer (shown on first launch and in Settings)

Signals are probabilistic; past performance does not guarantee future results; F&O trading carries a high risk of
loss; the user is solely responsible for all trades.
