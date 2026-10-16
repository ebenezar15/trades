# IndexEdge

A self-hosted trading assistant for **Nifty 50 and Bank Nifty** on **Angel One SmartAPI**. It runs on your own
Windows PC, opens in any browser (desktop or phone on your home Wi-Fi, installable as an app), and:

- checks **external markets first** (GIFT Nifty, US futures, Asian indices, India VIX, USD/INR, crude, US yields,
  news and the event calendar) and refuses to produce a signal when that context is stale;
- scans **13 widely used Indian index strategies** (ORB, VWAP pullback, EMA+Supertrend+ADX, triple Supertrend,
  CPR, CPR+VWAP, squeeze, RSI divergence, OI/PCR, 9:20 straddle, expiry iron fly, gap fill, inside bar);
- shows every signal with **measured** odds — win rate with a 95% confidence interval and sample size, R:R and
  expected value after Indian F&O costs — and the plain-English reasons behind it;
- can place and manage trades on Angel One (LIMIT / SL-L only, ≤ 8 orders/s, static-IP checked) under hard risk
  limits, with a one-click **kill switch**. **Paper trading is the default.**
- helps you learn: strategy explainers, past-day replay, "would you take this trade?" quizzes, a journal with
  automatic post-trade analysis, and analytics on what works for *you*.

No AI/LLM service is called while the app runs; everything is computed locally.

## About accuracy (please read)

No publicly available intraday index strategy has independently verified 95–98% accuracy. Claims like that
online leave out costs, cherry-pick periods or hide large losses behind many small wins
([research notes](docs/STRATEGY_RESEARCH.md)). IndexEdge therefore never shows a claimed accuracy. It
backtests each strategy on your data, out of sample, after costs, and:

- 🟢 **Validated** strategies (profit factor > 1.3, ≥ 100 trades, positive expectancy, drawdown within limit)
  are the only ones enabled for live signals by default;
- 🏅 **High win-rate** (≥ 80%) is shown, but always next to R:R and expectancy, with a warning when expectancy ≤ 0;
- fewer than 50 historical trades → **"Insufficient data"**, no probability shown.

Until you connect Angel One, the app runs in **demo mode** on synthetic prices so you can explore everything;
demo statistics are labelled "SYNTHETIC — not real performance" everywhere.

## Install (Windows 10/11)

1. Download this repository as a ZIP (green **Code** button → *Download ZIP*) and extract it, e.g. to
   `C:\IndexEdge`.
2. Double-click **`install.bat`**. It installs everything it needs (no admin rights, no Docker), asks for your
   Angel One SmartAPI details (you can skip and add them later in Settings) and puts an **IndexEdge** shortcut
   on your desktop.
3. Double-click **IndexEdge** on the desktop. The app opens at <http://localhost:8000>.

Phone: on the same Wi-Fi open `http://<your-PC-IP>:8000` and choose *Add to Home screen*.
Full guide, including SmartAPI app creation, TOTP, the static IP and Telegram: **[docs/SETUP_WINDOWS.md](docs/SETUP_WINDOWS.md)**.
How to use the app day to day: **[docs/USER_GUIDE.md](docs/USER_GUIDE.md)**.

## Commands

```
IndexEdge.bat                 start (same as: .venv\Scripts\python -m indexedge run)
python -m indexedge setup     re-run the setup wizard
python -m indexedge check     dry-run Angel One login + data fetch (never places orders)
python -m indexedge backtest orb15 NIFTY 250
update.bat                    update to the latest version
```

## For developers

```
uv venv --python 3.11 .venv && uv pip install -e ".[dev]"
.venv/bin/pytest -q                       # ~450 offline tests
cd frontend && npm ci && npm run dev      # UI dev server (proxies /api to :8000); npm run dev:mock for fake data
cd frontend && npm run build              # rebuild frontend/dist (committed so Windows users need no Node)
```

Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · broker facts: [docs/SMARTAPI_NOTES.md](docs/SMARTAPI_NOTES.md) ·
plan & API contract: [docs/superpowers/plans/2026-10-16-indexedge.md](docs/superpowers/plans/2026-10-16-indexedge.md).
An optional `docker-compose.yml` exists for a Linux VPS.

## Disclaimer

Signals are probabilistic; past performance does not guarantee future results. Trading futures and options
carries a high risk of loss — SEBI found over 9 in 10 individual F&O traders lost money. You are solely
responsible for all trades. IndexEdge is personal software, not investment advice, and is for use on your own
account only.
