"""Bar-by-bar exit simulation for a single signal. Shared by the backtester and ML labelling,
so labels and backtest outcomes are guaranteed to agree.

Rules (conservative):
* Entry at ``signal.entry`` on the bar *after* the signal bar (no same-bar fills: avoids look-ahead).
  If the next bar opens beyond the entry in the trade's favour, entry is at that bar's open (gap).
* If a bar touches both stop and target, the stop is assumed to hit first.
* Optional time exit at ``signal.time_exit`` (HH:MM) or after ``max_bars``; exit at that bar's close.
* ``trail_atr``: after first target, stop moves to breakeven.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from indexedge.core.clock import parse_hhmm
from indexedge.core.types import Side, StrategySignal


@dataclass
class TradeOutcome:
    entry_ts: datetime
    entry_price: float
    exit_ts: datetime
    exit_price: float
    outcome: str            # "target" | "stop" | "time" | "eod" | "no_fill"
    points: float           # signed in the trade's favour
    r_multiple: float
    mfe_points: float       # max favourable excursion
    mae_points: float       # max adverse excursion (positive number)
    bars_held: int

    @property
    def win(self) -> bool:
        return self.points > 0


def simulate_exit(
    signal: StrategySignal,
    bars: pd.DataFrame,
    max_bars: int = 75,
    breakeven_after_t1: bool = True,
) -> TradeOutcome | None:
    """Simulate a trade on ``bars`` = candles strictly AFTER the signal bar (same underlying).

    Returns ``None`` if there are no bars to trade on. Only bars on the same calendar day as the
    first bar are used (intraday); a trade still open at the day's last bar exits there ("eod").
    """
    if bars is None or bars.empty:
        return None
    first_day = bars.index[0].date()
    bars = bars[bars.index.date == first_day].iloc[:max_bars]
    side = signal.side
    s = side.sign
    risk = signal.risk_points
    if risk <= 0:
        return None

    first = bars.iloc[0]
    entry = signal.entry
    if s * (first["open"] - entry) > 0:      # gapped through entry in our favour / adverse open
        entry = float(first["open"])
    stop = signal.stop
    targets = list(signal.targets) or [entry + s * 2 * risk]
    target = targets[0]
    time_exit = parse_hhmm(signal.time_exit) if signal.time_exit else None

    mfe = 0.0
    mae = 0.0
    for n, (ts, bar) in enumerate(bars.iterrows(), start=1):
        hi, lo = float(bar["high"]), float(bar["low"])
        fav = (hi - entry) if s > 0 else (entry - lo)
        adv = (entry - lo) if s > 0 else (hi - entry)
        mfe = max(mfe, fav)
        mae = max(mae, adv)
        stop_hit = lo <= stop if s > 0 else hi >= stop
        tgt_hit = hi >= target if s > 0 else lo <= target
        if stop_hit:
            exit_px = stop
            if s * (float(bar["open"]) - stop) < 0:   # gapped beyond stop -> fill at open
                exit_px = float(bar["open"])
            pts = s * (exit_px - entry)
            return TradeOutcome(bars.index[0], entry, ts, exit_px, "stop", pts, pts / risk, mfe, mae, n)
        if tgt_hit:
            if len(targets) > 1 and breakeven_after_t1:
                # scale to next target with stop at breakeven
                targets = targets[1:]
                target = targets[0]
                stop = entry
                continue
            pts = s * (target - entry)
            return TradeOutcome(bars.index[0], entry, ts, target, "target", pts, pts / risk, mfe, mae, n)
        if time_exit is not None and ts.time() >= time_exit:
            px = float(bar["close"])
            pts = s * (px - entry)
            return TradeOutcome(bars.index[0], entry, ts, px, "time", pts, pts / risk, mfe, mae, n)
    ts = bars.index[-1]
    px = float(bars.iloc[-1]["close"])
    pts = s * (px - entry)
    outcome = "eod" if len(bars) < max_bars else "time"
    return TradeOutcome(bars.index[0], entry, ts, px, outcome, pts, pts / risk, mfe, mae, len(bars))


def side_from_str(s: str) -> Side:
    return Side(s.upper())
