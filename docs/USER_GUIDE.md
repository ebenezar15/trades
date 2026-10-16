# IndexEdge user guide

## A trading day

| Time (IST) | What happens |
|---|---|
| 08:25 | IndexEdge starts (if you enabled auto-start). |
| 08:30 | Logs in to Angel One with TOTP, downloads the day's instrument master (lot sizes and expiries are never hard-coded), checks the static IP. |
| 08:50 | **Pre-market checklist** (app + Telegram): global bias, GIFT Nifty gap expectation, CPR/pivot levels, today's events, which strategies suit today's regime. |
| 09:15–15:30 | Live 5-minute candles. On every closed bar each strategy is evaluated; signals pass through the gates below. |
| 15:15 | Forced square-off of open intraday trades (5 min before Angel One's 15:20 F&O cutoff). |
| 15:45 | **End-of-day report**: P&L, trades, signals taken/skipped, discipline score. |
| Saturday 10:00 | Strategies revalidated and probability models retrained on the latest history. |

## How a signal is built

1. **Strategy trigger** — a rule fires on a closed bar (never on an unfinished one).
2. **External context gate** — required inputs (S&P 500 futures, India VIX by default) must be fresh; if global
   cues are risk-off (bias ≤ −0.5 or VIX ≥ 22) the signal is blocked.
3. **Event blackout** — no new entries 15 min before/after high-impact events (RBI policy, Fed, CPI, Budget, …).
4. **Regime filter** — each strategy declares the regimes it works in (trend up/down, range, high volatility).
5. **Validation** — only strategies validated on real data (or that you enabled yourself) are "active";
   others still appear as *blocked* so you can study them.
6. **Probability** — calibrated model probability when a trained model is reliable, otherwise the strategy's
   historical win rate with its 95% confidence interval. Fewer than 50 samples → *Insufficient data*.
7. **Risk engine** (before any order) — position size from your risk-per-trade, daily loss / trade-count /
   consecutive-loss locks, max positions and lots, margin, first/last-minutes filter, cooldown after a loss,
   no revenge sizing, stale-feed block.

Each signal card shows entry, stop, targets, probability gauge with CI and sample size, R:R, expected value in ₹
after costs, badges, and a **Why?** panel with every condition, the context and news reasons and the top model
features.

## Execution modes (per strategy, Strategy Lab)

- **Signal only** — alert with the full plan; you trade manually if you wish.
- **Confirm** — the app prepares the order; tap Confirm (app or Telegram) within 60 s.
- **Auto** — executes and manages within the risk limits.

Orders are always LIMIT (marketable, chased up to 5 times) with an immediate SL-L stop; at target 1 half is
booked and the stop moves to breakeven; time exit and forced square-off apply. The **Kill switch** cancels all
orders, squares off everything and locks trading for the rest of the day.

## Pages

- **Dashboard** — live chart (EMA 9/21, VWAP, Supertrend, CPR/pivots, previous-day and opening-range levels),
  regime, VIX, PCR, context meter, active signals, positions, risk meters, kill switch.
- **Signals** — current and past signals with filters and the Why panel.
- **Option Chain** — OI, ΔOI, IV, Greeks (computed locally with Black-Scholes), build-up colours, PCR, max pain,
  OI walls.
- **Market Context** — every external input with its value, change, age and stale flag.
- **News & Events** — local sentiment/impact-tagged headlines, upcoming events and blackout windows.
- **Trades & Journal** — every trade with automatic post-trade analysis (entry quality, MFE/MAE, plan vs actual,
  slippage, rule breaks, good/bad trade × good/bad result). Add notes, tags and emotions. Export CSV/Excel.
- **Analytics** — equity curve, drawdown, win rate, profit factor, expectancy, breakdowns by strategy / hour /
  weekday / regime, weekly and monthly summaries, discipline score.
- **Strategy Lab** — report cards (walk-forward, out-of-sample, after costs), Monte Carlo drawdowns, enable,
  execution mode, parameters, run backtests.
- **Replay** — replay any stored day bar by bar and see signals as they would have appeared, with outcomes.
- **Learn** — explainer for every strategy and a "Would you take this trade?" quiz scored against what happened.
- **Pre-market / EOD report / Health / Settings**.

## Settings that matter

- **Risk limits** can only be changed outside market hours (prevents emotional changes mid-session).
- **Paper ↔ Live** needs the typed confirmation `I UNDERSTAND THE RISK`, a connected broker, a passing static-IP
  check and no open trades.
- **Data sources** — news RSS feeds and external context inputs (weights, required flag, max age) are editable.

## Good habits

- Treat probabilities as long-run frequencies, not promises: a 60% setup loses 4 times in 10.
- Judge yourself on process (discipline score, rule violations), not on single results.
- Re-run Strategy Lab validation after major market changes (lot sizes, expiry rules, volatility regimes).
