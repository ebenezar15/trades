"""``vwap_pullback``: trend-continuation entry on a pullback to session VWAP."""
from __future__ import annotations

from typing import Any, ClassVar

import numpy as np
import pandas as pd

from indexedge.core.types import Regime, Side, StrategySignal
from indexedge.strategies.base import BarView, Strategy, fmt, hhmm_to_min, r_targets

LEARN = """\
### VWAP Pullback (trend continuation)

**What it is.** On a trending day price stays on one side of the session VWAP (volume-weighted average
price). When it pulls back to VWAP and a candle closes firmly back in the trend direction, we join the trend.

**Why it can work.** VWAP is the average price paid today; institutions benchmark against it, so it often
acts as dynamic support in an up-trend (resistance in a down-trend).

**When it fails.** On range days price crosses VWAP repeatedly; the trend gate (≥ 70% of bars on one side and
EMA 9 > EMA 21) is meant to filter those days, but late-day reversals still hit the stop.

**Rules (defaults).**
- Trend gate: close above VWAP for ≥ 70% of today's bars **and** EMA(9) > EMA(21) (mirror for shorts).
- Pullback: within the last 3 bars, low ≤ VWAP + 0.1×ATR(14).
- Trigger: candle closes back above VWAP with a bullish body ≥ 40% of its range.
- Stop: tighter of (pullback low − 0.1×ATR) and (VWAP − 0.5×ATR), but at least 0.3×ATR away.
- Target 2R. Window 09:45–14:30, max 2 trades per day, exit by 15:15.

**Risk.** Needs real futures volume for a meaningful VWAP; on spot-index data (no volume) VWAP degenerates.
"""


class VWAPPullback(Strategy):
    id = "vwap_pullback"
    name = "VWAP Pullback"
    description = "Join an established intraday trend on a pullback to VWAP and a strong re-close."
    regimes: ClassVar[set[Regime] | None] = {Regime.TREND_UP, Regime.TREND_DOWN}
    required_columns = ("open", "high", "low", "close", "atr14", "vwap", "ema9", "ema21")
    default_params: ClassVar[dict[str, Any]] = {
        "trend_frac": 0.70,
        "touch_atr": 0.1,
        "pullback_bars": 3,
        "min_body_frac": 0.40,
        "stop_buffer_atr": 0.1,
        "vwap_stop_atr": 0.5,
        "min_stop_atr": 0.3,
        "target_r": 2.0,
        "entry_start": "09:45",
        "entry_end": "14:30",
        "max_per_day": 2,
    }
    learn = LEARN

    def _scan(self, feat: pd.DataFrame, underlying: str, extra: dict[str, Any] | None) -> list[StrategySignal]:
        p = self.params
        bv = BarView(feat)
        o, h, lo, c, a = bv["open"], bv["high"], bv["low"], bv["close"], bv["atr14"]
        vw, e9, e21 = bv["vwap"], bv["ema9"], bv["ema21"]
        start, end = hhmm_to_min(p["entry_start"]), hhmm_to_min(p["entry_end"])
        nb = int(p["pullback_bars"])
        out: list[StrategySignal] = []
        for _d, s, e in bv.day_slices():
            above = np.cumsum(c[s:e] > vw[s:e]) / np.arange(1, e - s + 1)
            below = np.cumsum(c[s:e] < vw[s:e]) / np.arange(1, e - s + 1)
            count, last_i = 0, -10
            for i in range(s, e):
                if count >= int(p["max_per_day"]):
                    break
                if not (start <= bv.close_min[i] <= end) or i - last_i <= nb:
                    continue
                if not all(np.isfinite(x) and x > 0 for x in (a[i], vw[i], e9[i], e21[i])):
                    continue
                rng = h[i] - lo[i]
                if rng <= 0:
                    continue
                j0 = max(s, i - nb + 1)
                for side in (Side.BUY, Side.SELL):
                    sg = side.sign
                    frac = above[i - s] if side is Side.BUY else below[i - s]
                    if frac < p["trend_frac"] or sg * (e9[i] - e21[i]) <= 0:
                        continue
                    if side is Side.BUY:
                        touched = np.min(lo[j0:i + 1]) <= vw[i] + p["touch_atr"] * a[i]
                    else:
                        touched = np.max(h[j0:i + 1]) >= vw[i] - p["touch_atr"] * a[i]
                    body = sg * (c[i] - o[i])
                    if not touched or sg * (c[i] - vw[i]) <= 0 or body < p["min_body_frac"] * rng:
                        continue
                    entry = float(c[i])
                    swing = np.min(lo[j0:i + 1]) if side is Side.BUY else np.max(h[j0:i + 1])
                    st1 = swing - sg * p["stop_buffer_atr"] * a[i]
                    st2 = vw[i] - sg * p["vwap_stop_atr"] * a[i]
                    stop = max(st1, st2) if side is Side.BUY else min(st1, st2)
                    min_stop = entry - sg * p["min_stop_atr"] * a[i]
                    stop = min(stop, min_stop) if side is Side.BUY else max(stop, min_stop)
                    tg = r_targets(side, entry, stop, [p["target_r"]])
                    trend = "above" if side is Side.BUY else "below"
                    reasons = [
                        (f"Close {trend} VWAP on {frac * 100:.0f}% of today's bars (needs ≥"
                        f" {p['trend_frac'] * 100:.0f}%)"),
                        f"EMA 9 {fmt(e9[i])} {'>' if side is Side.BUY else '<'} EMA 21 {fmt(e21[i])}",
                        f"Pulled back to VWAP {fmt(vw[i])} (extreme {fmt(swing)}) within {nb} bars",
                        f"Close {fmt(entry)} back {trend} VWAP with body {body / rng * 100:.0f}% of the candle",
                        (f"Stop {fmt(stop)}, risk {fmt(abs(entry - stop))} pts, target {fmt(tg[0])}"
                        f" ({p['target_r']}R)"),
                    ]
                    out.append(self._signal(bv, i, underlying, side, entry, stop, tg, reasons))
                    count += 1
                    last_i = i
                    break
        return out
