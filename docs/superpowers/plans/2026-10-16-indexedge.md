# IndexEdge Implementation Plan

> **For agentic workers:** Implement your assigned task(s) only. Read `docs/ARCHITECTURE.md` (spec),
> `docs/SMARTAPI_NOTES.md` (verified broker facts) and the existing code in `indexedge/core`, `indexedge/config`,
> `indexedge/broker/base.py`, `indexedge/backtest/simulate.py` first. Do NOT modify those shared files — if you
> need a change, say so in your report. Do NOT run `git commit`. TDD: write tests in `tests/` first.
> Run tests with `.venv/bin/pytest tests/<your files> -q`.

**Goal:** A finished, installable, single-user Nifty/Bank Nifty trading assistant on Angel One SmartAPI (paper by default).

**Architecture:** One Python process (FastAPI + asyncio). Modules communicate through `core.bus.EventBus`.
Storage DuckDB + Parquet. React PWA served by the backend. See spec §4.

**Tech Stack:** Python 3.11, FastAPI, pandas/numpy, LightGBM, scikit-learn, DuckDB, smartapi-python, feedparser,
vaderSentiment, APScheduler 3.x; React 18 + TypeScript + Vite + Tailwind + lightweight-charts 4.x.

**Spec:** `docs/ARCHITECTURE.md`

## Global Constraints

- Python ≥ 3.11; all timestamps tz-aware IST (`indexedge.core.clock.IST`). Candle DataFrames: tz-aware
  DatetimeIndex (bar open time) + float columns `open, high, low, close, volume` (+ optional `oi`).
- No runtime calls to any LLM / paid AI API.
- Order types: only `OrderType.LIMIT` and `OrderType.SL_LIMIT`. Never MARKET, IOC, AMO. Duration always DAY.
- Order rate ≤ 8 orders/second (token bucket), shared by place/modify/cancel.
- `import SmartApi` only inside functions (it performs network I/O on import).
- Secrets only via `indexedge.config.secrets.get_secret`; never logged.
- No network access in tests. Tests must run offline using synthetic data (`indexedge.data.synthetic`).
- Accuracy numbers are only ever computed from data (never constants). Fewer than
  `settings.strategies.min_trades_any` (50) samples ⇒ "insufficient data".
- Code style: ruff, line length 110, type hints, small focused files, module docstrings.

## Review Focus

1. Look-ahead leakage: a strategy/feature value at bar *i* must be identical whether computed on `df[:i+1]` or on
   the full `df` (truncation test in features and strategies).
2. Stale/missing data: empty DataFrames, NaN warm-up periods, missing context inputs and dead feeds must not crash —
   they must produce "no signal" with a reason.
3. Partial fills / rejections / disconnects during order chase must leave a consistent position + protective stop.
4. Market-hours edges: first/last N minutes, broker cutoff 15:20, holidays, weekends, expiry day.
5. Option strikes ×100 and tick_size in paise in the instrument master; WebSocket prices in paise.

---

## Module interfaces (contract for all tasks)

### T1 `indexedge/features/` — indicators & analytics (pure functions, no I/O)
`indicators.py`:
- `ema(s: pd.Series, n: int) -> pd.Series`, `sma(s, n)`, `rsi(close, n=14)`, `roc(close, n=10)`
- `macd(close, fast=12, slow=26, signal=9) -> DataFrame[macd, signal, hist]`
- `atr(df, n=14) -> Series` (Wilder), `adx(df, n=14) -> DataFrame[adx, plus_di, minus_di]`
- `supertrend(df, n=10, mult=3.0) -> DataFrame[supertrend, direction]` (direction +1 up / -1 down)
- `vwap(df) -> DataFrame[vwap, upper1, lower1, upper2, lower2]` — resets each session (calendar day), bands = ±k·σ
- `bollinger(close, n=20, k=2.0) -> DataFrame[mid, upper, lower, width]`
- `keltner(df, n=20, mult=1.5) -> DataFrame[mid, upper, lower]`; `squeeze(df) -> Series[bool]` (BB inside KC)
- `stoch_rsi(close, n=14, k=3, d=3) -> DataFrame[k, d]`, `obv(df) -> Series`
- `relative_volume(df, n=20) -> Series` (volume / avg volume of the same time-of-day over previous n sessions;
  falls back to rolling mean when history is short)
- `daily_ohlc(df) -> DataFrame` (one row per session date)
- `cpr(daily: DataFrame) -> DataFrame[pivot, bc, tc, width_pct, r1, r2, r3, s1, s2, s3]` indexed by the date the
  levels apply to (i.e. computed from the *previous* session)
- `session_levels(df) -> DataFrame[prev_high, prev_low, prev_close, day_open, or_high, or_low]` aligned to df
  (opening range = first 15 minutes; NaN until the range is complete)
- `heikin_ashi(df) -> DataFrame[open, high, low, close]`
- `swing_points(df, left=3, right=3) -> DataFrame[swing_high, swing_low]` (values confirmed only `right` bars later)
- `divergence(price: Series, osc: Series, lookback=30, swing=3) -> Series[int]` (+1 bullish, -1 bearish, 0)
`volume.py`: `volume_profile(df, bins=50) -> dict(poc, vah, val)`, `anchored_vwap(df, anchor_ts) -> Series`
`options.py`: `bs_price(S,K,T,r,sigma,opt:'CE'|'PE')`, `bs_greeks(...) -> dict(delta,gamma,theta,vega)` (theta per
day, vega per 1 vol pt), `implied_vol(price,S,K,T,r,opt) -> float|nan`, and on a chain DataFrame with columns
`strike, option_type, ltp, oi, oi_change, volume, iv?`: `pcr(chain) -> float` (OI), `max_pain(chain) -> float`,
`oi_walls(chain, n=3) -> dict(resistance:[strikes], support:[strikes])`,
`oi_buildup(price_change, oi_change) -> str` in {long_buildup, short_buildup, short_covering, long_unwinding, neutral},
`enrich_chain(chain, spot, expiry_dt, now, r=0.065) -> DataFrame` (adds iv, delta, gamma, theta, vega, buildup).
`patterns.py`: `detect_patterns(df) -> DataFrame[bull_engulf, bear_engulf, pin_bull, pin_bear, inside_bar, nr7]` (0/1)
`regime.py`: `classify_regime(df) -> Series[str]` values of `core.types.Regime` (ADX/EMA slope/ATR percentile based).
`engine.py`: `build_features(df: DataFrame, daily: DataFrame|None=None) -> DataFrame` — df plus every indicator
column used by strategies (names listed in module constant `FEATURE_COLUMNS: list[str]` for ML; all numeric, causal).

### T2 `indexedge/data/` + `indexedge/broker/` — data and brokers
- `data/synthetic.py`: `generate_intraday(underlying='NIFTY', days=250, interval_min=5, seed=0, start=date(2025,1,1))
  -> DataFrame` realistic 09:15–15:30 IST bars (regime-switching drift/vol, U-shaped volume, gaps, weekends &
  configured holidays skipped); `generate_daily(...)`; `generate_option_chain(spot, expiry, now, seed) -> DataFrame`.
- `data/store.py`: `CandleStore(root: Path)`: `save(symbol, tf, df)` (append+dedupe, parquet by year),
  `load(symbol, tf, start=None, end=None) -> DataFrame`, `resample(df, tf) -> DataFrame` (session-aware, '1m','3m',
  '5m','15m','1h','1d'), `available() -> list[(symbol, tf, first, last, rows)]`.
- `data/instruments.py`: `InstrumentMaster.from_json(rows: list[dict])`, `.load(path)`, `download(url, path)`;
  methods `index(name) -> Instrument`, `futures(name) -> list[Instrument]` (sorted by expiry), `nearest_future(name,
  on: date)`, `expiries(name, on) -> list[date]`, `option(name, expiry, strike, opt) -> Instrument`,
  `chain(name, expiry) -> list[Instrument]`, `atm_strike(name, spot, expiry) -> float`, `lot_size(name) -> int`,
  `strike_step(name, expiry) -> float`, `by_token(token)`. Handles strike/100, tick_size paise, expiry `DDMMMYYYY`.
- `data/aggregator.py`: `CandleAggregator(interval_min, on_close: Callable[[str, pd.Series], Awaitable|None])`
  `.on_tick(tick: Tick)` builds bars from cumulative volume; emits a closed bar when a tick arrives in the next
  bucket or `flush(now)` passes the bucket end.
- `data/history.py`: `HistoryService(broker, store, master)`: `async backfill(underlying, tf, days) -> int` chunked
  per SmartAPI limits; `yfinance_download(ticker, interval, period) -> DataFrame` (offline-safe: returns empty on error).
- `broker/rate_limiter.py`: `RateLimiter(rate: float, burst: int|None=None)` with `async acquire()`.
- `broker/static_ip.py`: `async public_ip(url) -> str|None`, `check_static_ip(expected, url) -> dict(ok, public_ip,
  expected, message)`.
- `broker/paper.py`: `PaperBroker(clock, capital, slippage_ticks=1)` implements `BrokerBase`; `.feed_tick(tick)`
  matches resting LIMIT/SL-L orders against ticks (buy limit fills when ltp ≤ price, at min(price, ltp)+slippage;
  SL-L triggers when ltp crosses trigger then behaves as limit), tracks positions, realised P&L, funds; rejects any
  non-allowed order type.
- `broker/angel_one.py`: `AngelOneBroker(settings, secrets_getter=get_secret, clock)` implements `BrokerBase` with
  smartapi-python: TOTP login via `pyotp`, `refresh()`, SmartWebSocketV2 in a thread → asyncio via
  `loop.call_soon_threadsafe`, paise→rupees, reconnect with backoff, order-update websocket, order params mapping
  (LIMIT→variety NORMAL; SL_LIMIT→variety STOPLOSS ordertype STOPLOSS_LIMIT; duration DAY), shared `RateLimiter(8)`,
  JSONL audit log (`data/audit/YYYY-MM-DD.jsonl`, secrets redacted), `dry_run=True` mode that never sends orders.
  Tested with a fake SmartConnect object (dependency-injected factory).

### T3 `indexedge/context/`, `indexedge/news/`, `indexedge/notify/`
- `context/providers.py`: `QuoteProvider` protocol `async fetch(symbols: list[str]) -> dict[str, Quote]` where
  `Quote(symbol, price, prev_close, change_pct, ts)`. `YFinanceProvider`, `GiftNiftyProvider` (best-effort public
  page scrape, returns nothing on failure), `StaticProvider(dict)` for tests/demo, `SyntheticProvider(seed)`.
- `context/engine.py`: pure `score_context(quotes: dict[str, Quote], cfg: ContextSettings, now, event_risk=0.0)
  -> ContextSnapshot` (bias = tanh(Σ weight·clip(change_pct/σ) / Σ|weight|); VIX ≥ vix_risk_off_level or bias ≤
  risk_off_threshold ⇒ risk_off; required inputs older than max_age_min ⇒ `stale_required`; reasons list in plain
  English, e.g. "S&P 500 fut +0.8% (bullish)"). `ContextEngine(providers, settings, bus, clock)` with
  `async refresh() -> ContextSnapshot`, `latest`, `run()` loop publishing `bus.CONTEXT`.
- `news/sources.py`: `async fetch_feed(source: NewsSource, client) -> list[NewsItem]` (feedparser on fetched bytes).
- `news/classifier.py`: `NewsClassifier(heavyweights)`: `.classify(item) -> NewsItem` sets relevance (keywords:
  nifty, bank nifty, sensex, RBI, repo rate, inflation, FII, Fed, crude, rupee, + heavyweight names/aliases),
  sentiment (VADER + finance lexicon boost, optional FinBERT), impact (rules: RBI policy/rate, budget, Fed,
  CPI/GDP, war/attack, ban, SEBI action, heavyweight results ⇒ high).
- `news/calendar.py`: `EventCalendar(settings, clock)`: loads `config/events.yaml` (scheduled macro events) + adds
  expiry days from an injected list of expiry dates + holidays; `upcoming(days=7)`, `in_blackout(at) ->
  MarketEvent|None`, `event_risk(at) -> float` (0..1 rising as a high-impact event approaches).
- `news/service.py`: `NewsService(settings, bus, clock, classifier, store_cb)` polling loop, dedupe by url hash,
  publishes `bus.NEWS`, alerts on high impact.
- `notify/telegram.py`: `TelegramNotifier(token_getter, chat_id, client=None)`: `async send(text, buttons:
  list[tuple[label, callback_data]]|None=None) -> bool`, `async poll_callbacks(handler)` (getUpdates long-poll;
  handler(callback_data) for one-tap confirm), Markdown escaping. No-op when not configured.
- Also create `config/events.yaml` with 2026–2027 scheduled events you can verify (RBI MPC, FOMC, Union Budget
  2027-02-01, India CPI ~12th monthly, US CPI) — mark unverified dates `verified: false`.

### T4 `indexedge/journal/` + `indexedge/risk/`
- `journal/db.py`: `Journal(path: Path|":memory:")` DuckDB, tables: `signals, orders, fills, trades, notes,
  news, settings_audit, equity_daily`. Methods: `record_signal(Signal)`, `update_signal_status(id, status,
  reason='')`, `record_order(Order)`, `record_fill(Fill)`, `open_trade(trade: TradeRecord) -> str`,
  `close_trade(id, exit_ts, exit_price, outcome, pnl, costs)`, `trades(start=None, end=None, strategy=None,
  status=None) -> DataFrame`, `signals(...) -> DataFrame`, `add_note(trade_id, text, tags, emotion)`,
  `record_news(NewsItem)`, `news(limit) -> DataFrame`, `today_stats(day) -> DayStats(realised_pnl, trades,
  consecutive_losses, last_loss_ts, last_trade_qty)`, `export(path, fmt='csv'|'xlsx')`.
  `TradeRecord` dataclass: id, signal_id, strategy_id, underlying, instrument_symbol, side, qty, entry_ts,
  entry_price, stop, targets, mode ('paper'|'live'|'backtest'), status ('open'|'closed'), plus exit fields,
  pnl, costs, r_multiple, mfe, mae, features(json), reasons(json), context(json).
- `journal/analysis.py`: `post_trade_analysis(trade_row, bars_during: DataFrame) -> dict` (template text: entry
  quality, MFE/MAE, plan vs actual, slippage, rule violations, outcome quadrant good/bad trade × good/bad result).
- `journal/analytics.py`: `performance(trades_df) -> dict` (equity curve, drawdown, win rate, PF, expectancy, avg
  R, breakdown by strategy / regime / hour / weekday / underlying), `discipline_score(...)`.
- `risk/engine.py`: `RiskEngine(settings: RiskSettings, clock, journal, cutoff='15:20')`:
  `size_position(entry, stop, lot_size, point_value=1.0) -> int` (lots×lot_size, 0 if under one lot),
  `check(proposal: TradeProposal, open_positions: list[Position], funds: Funds|None, feed_age_s: float,
  blackout: MarketEvent|None) -> RiskDecision(allowed: bool, reasons: list[str], qty: int)`;
  `lock_day(reason)`, `locked -> bool`, `can_edit_limits() -> bool`. `TradeProposal(underlying, side, entry,
  stop, lot_size, qty|None, premium|None)`.

### T5 `indexedge/strategies/` + `indexedge/backtest/` (wave 2; uses T1)
- `strategies/base.py`: `class Strategy(ABC)`: attrs `id, name, description, timeframe='5m', regimes:
  set[Regime]|None, instrument_hint, params: dict, learn: str (markdown explainer)`; `scan(feat: DataFrame,
  underlying: str) -> list[StrategySignal]` — causal (truncation-safe), one signal per setup;
  `latest(feat, underlying) -> StrategySignal|None` = signal whose ts == last bar.
- `strategies/registry.py`: `all_strategies(params_overrides=None) -> dict[id, Strategy]`.
- Strategy IDs: `orb15, vwap_pullback, ema_supertrend_adx, cpr_pivot, squeeze_breakout, rsi_divergence, oi_pcr,
  expiry_straddle, gap_fill` + those selected in `docs/STRATEGY_RESEARCH.md`.
- `backtest/costs.py`: `CostModel` with `charges(instrument_kind: 'future'|'option', side, qty, price) -> float`
  (brokerage ₹20/order flat, STT, NSE txn charge, SEBI fee, GST 18% on brokerage+txn+SEBI, stamp duty on buy) —
  rates as config constants, documented with the date verified; `round_trip(kind, qty, entry, exit)`.
- `backtest/engine.py`: `run_backtest(strategy, df, underlying, lot_size, cost_model, point_value=1.0,
  max_bars=75) -> BacktestResult(trades: DataFrame, metrics: dict)` uses `simulate_exit`; one open trade at a time.
- `backtest/metrics.py`: `wilson_ci(wins, n, z=1.96)`, `compute_metrics(trades) -> dict(n, win_rate, win_ci,
  avg_r, expectancy_r, expectancy_rs, profit_factor, max_dd_r, sharpe, by_year)`, `monte_carlo(r_list, n=2000) ->
  dict(dd_p50, dd_p95, ...)`.
- `backtest/walkforward.py`: `walk_forward(strategy, df, ..., train_days, test_days) -> dict` (OOS metrics,
  stability across folds, overfitting warnings).
- `backtest/report.py`: `report_card(metrics, settings: StrategySettings) -> dict(badges: list[str], status:
  'validated'|'high_win_rate'|'insufficient'|'rejected', warnings)`. Badges: "validated", "high_win_rate",
  "insufficient_data", "rejected", "negative_expectancy_warning".
- `backtest/replay.py`: `ReplaySession(df, strategies)` stepping bar by bar, returning signals visible at each bar.

### T6 `indexedge/models/` (wave 2; uses T1 + simulate)
- `labels.py`: `label_signals(signals, df, max_bars) -> DataFrame` (features at signal bar + outcome, r, win).
- `train.py`: `train_model(dataset, feature_cols, n_splits=5, embargo_bars=…) -> ModelBundle` LightGBM (fallback
  logistic regression when n < 300), purged TimeSeriesSplit, isotonic calibration on OOS predictions;
  `ModelBundle.predict_proba(X) -> ndarray`, `.metrics` (auc, brier, reliability bins, n), `.save/.load(path)`,
  `.top_features(x_row, k=5)` (LightGBM `pred_contrib` = TreeSHAP, no shap dependency).
- `registry.py`: `ModelRegistry(dir)`: `get(strategy_id) -> ModelBundle|None`, `put(...)`.
- `drift.py`: `DriftMonitor(window)`: `update(prob, outcome)`, `status() -> dict(ok, live_brier, expected_band)`.
- Leakage test: shuffle-future test & truncation test.

### T7 `indexedge/signals/` + `indexedge/execution/` (wave 2; uses T2, T4)
- `signals/composer.py`: `SignalComposer(settings, registry, risk, calendar, master|None)`:
  `compose(raw: StrategySignal, feat_row, context: ContextSnapshot|None, stats: dict|None, regime) -> Signal`
  applying: context gate (None/unusable ⇒ blocked "external context stale"), risk_off gate, news blackout gate,
  regime gate, calibrated probability (or historical win rate with Wilson CI when no model; None if n < 50),
  EV after costs, badges from report card, sizing, instrument selection (ATM option or future via master).
- `execution/orders.py`: `OrderManager(broker, limiter, settings, journal)`: `async enter(req_side, instrument,
  qty, ref_price) -> Order` (marketable LIMIT = ltp ± buffer ticks, chase via modify up to N then cancel;
  handles partial fill), `async place_stop(instrument, side, qty, trigger) -> Order` (SL-L), `async
  cancel_all()`, `async square_off_all()` (marketable LIMIT, chased).
- `execution/trades.py`: `TradeManager(order_mgr, broker, journal, risk, clock, bus, settings)`: `async
  execute(signal)`, `on_tick` management (targets/partials, trailing to breakeven after T1, time exit, forced
  square-off before cutoff), `async kill_switch()`, `async reconcile()`.
- `execution/confirm.py`: pending-confirmation queue with expiry (default 60 s).

### T8 `frontend/` (React PWA, wave 1) — consumes the REST/WS API below.

### T9 `indexedge/api/` + `indexedge/app.py` + scheduler + installers + docs (lead, wave 3)

## REST / WebSocket API contract (served at `http://<host>:8000`)
All JSON. Timestamps ISO-8601 with +05:30.
- `GET /api/status` → `{mode, demo_mode, market_open, broker:{connected,name,logged_in}, static_ip:{ok,public_ip,
  expected,message}, feed:{last_tick_age_s, ws}, risk:{locked, lock_reason, daily_pnl, trades_today, max_trades,
  daily_loss_used_pct}, disclaimer_accepted, server_time}`
- `GET /api/candles?underlying=NIFTY&tf=5m&limit=500` → `{candles:[{time(unix s),open,high,low,close,volume}],
  overlays:{ema9:[{time,value}],ema21,vwap,supertrend:[{time,value,direction}]}, levels:{pivot,bc,tc,r1,s1,
  prev_high,prev_low,or_high,or_low}}`
- `GET /api/overview` → `{indices:[{underlying, ltp, change_pct, regime, vix?, pcr?, max_pain?}]}`
- `GET /api/context` → ContextSnapshot JSON `{ts, macro_bias, risk_off, event_risk, usable, inputs:{name:{label,
  value,change_pct,age_s,stale,weight,required}}, reasons, stale_required}`
- `GET /api/signals?status=&limit=` → `{signals:[SignalJSON]}`; SignalJSON = `{id, strategy_id, strategy_name,
  underlying, side, ts, entry, stop, targets, probability, probability_ci, sample_size, expected_rr, ev_per_trade,
  quantity, instrument, badges, reasons, context_reasons, news_reasons, top_features:[[name,val]], regime, status,
  blocked_reason}`
- `POST /api/signals/{id}/confirm`, `POST /api/signals/{id}/skip`
- `GET /api/positions` → `{positions:[{symbol, qty, avg_price, ltp, pnl}], open_trades:[...]}`
- `POST /api/kill-switch` → `{ok, cancelled, squared_off}`
- `GET /api/option-chain?underlying=NIFTY&expiry=` → `{spot, expiry, expiries, pcr, max_pain, walls, rows:[{strike,
  ce:{ltp,oi,oi_change,iv,delta,gamma,theta,vega,buildup}, pe:{...}}]}`
- `GET /api/news?limit=` → `{items:[{id,ts,source,title,url,sentiment,relevance,impact}]}`;
  `GET /api/events?days=14` → `{events:[{id,ts,title,category,impact}], blackout:{active,event}}`
- `GET /api/trades?start=&end=&strategy=` → `{trades:[...]}`; `GET /api/trades/{id}` → trade + analysis;
  `POST /api/trades/{id}/notes {text,tags,emotion}`; `GET /api/export?fmt=csv|xlsx` (file)
- `GET /api/analytics` → performance dict (equity:[{time,value}], drawdown, by_strategy, by_hour, by_weekday, ...)
- `GET /api/strategies` → `{strategies:[{id,name,description,timeframe,regimes,enabled,execution_mode,params,
  report:{status,badges,metrics,warnings}|null,learn}]}`; `POST /api/strategies/{id}` `{enabled?, execution_mode?,
  params?}`; `POST /api/backtest {strategy_id, underlying, days}` → `{metrics, report, trades, equity,
  monte_carlo, walk_forward}`; `GET /api/models/{strategy_id}` → `{metrics, reliability:[{p_pred,p_obs,n}]}`
- `GET /api/replay/days?underlying=` → `{days:[YYYY-MM-DD]}`; `GET /api/replay?underlying=&day=` → `{candles,
  signals:[SignalJSON-lite with bar index], outcomes}`; `GET /api/learn/quiz?underlying=` → `{day, upto_index,
  candles, question, choices, answer_id}`
- `GET /api/premarket` → `{date, context, gap_expectation, key_levels, events_today, enabled_strategies, regime}`;
  `GET /api/eod?day=` → report dict
- `GET /api/settings` → Settings JSON (no secrets) + `{secrets: {NAME: bool}, can_edit_limits}`;
  `PUT /api/settings` (partial); `POST /api/secrets {name, value}`; `POST /api/mode {mode:'paper'|'live',
  confirm_text}` (live requires confirm_text == "I UNDERSTAND THE RISK"); `POST /api/disclaimer/accept`;
  `POST /api/login` (trigger broker login today)
- `GET /api/health` → `{ws_status, last_tick_age_s, latency_ms, broker_errors, order_rate, jobs:[...], log_tail}`
- `WS /ws` → messages `{type: 'tick'|'candle'|'signal'|'context'|'news'|'order'|'trade'|'health'|'alert', data}`

## Tasks
- [ ] T1 features · [ ] T2 data+brokers · [ ] T3 context/news/notify · [ ] T4 journal+risk · [ ] T8 frontend (wave 1)
- [ ] T5 strategies+backtest · [ ] T6 models · [ ] T7 signals+execution (wave 2)
- [ ] T9 API, app wiring, scheduler, Windows installer, docs, integration tests (wave 3)
