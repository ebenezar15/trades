"""``rsi_divergence``: regular RSI(14) divergence reversal at confirmed swing pivots (no look-ahead)."""
from __future__ import annotations

from typing import Any, ClassVar

import numpy as np
import pandas as pd

from indexedge.core.types import Regime, Side, StrategySignal
from indexedge.strategies.base import BarView, Strategy, fmt, hhmm_to_min, r_targets

LEARN = """\
### RSI Divergence Reversal

**What it is.** Price makes a higher high but RSI(14) makes a lower high (bearish divergence) — or price
makes a
lower low while RSI makes a higher low (bullish). Momentum is fading even though price pushed further.

**Why it can work.** Exhausted moves often stall when fewer participants chase the new extreme; divergence
shows that loss of momentum.

**When it fails.** In strong trends divergences appear repeatedly while price keeps going. Many published
examples also cheat: a swing pivot is only known *after* the following bars print. IndexEdge confirms each
pivot only 3 bars later and only then looks for a trigger.

**Rules (defaults).**
- Swing pivots with 3 bars left and 3 bars right (confirmation lag enforced). Both pivots in the same session,
  5–40 bars apart.
- Bearish: price higher high, RSI lower high, first RSI pivot ≥ 65. Bullish mirror with ≤ 35.
- Trigger: within 5 bars of confirmation, a candle closes beyond the low (high) of the second pivot bar.
- Stop = divergence extreme ± 0.1×ATR(14). Target = VWAP or 2R, whichever is nearer.
- Window 09:45–14:30, exit by 15:15.

**Risk.** Counter-trend: losers can be fast. Best treated as a range-day tool.
"""


class RsiDivergence(Strategy):
    id = "rsi_divergence"
    name = "RSI Divergence Reversal"
    description = "Fade a price extreme that RSI(14) does not confirm, after a confirmed swing pivot."
    regimes: ClassVar[set[Regime] | None] = {Regime.RANGE, Regime.HIGH_VOL}
    required_columns = ("open", "high", "low", "close", "atr14", "rsi14", "vwap")
    default_params: ClassVar[dict[str, Any]] = {
        "swing": 3,
        "min_sep": 5,
        "max_sep": 40,
        "rsi_high": 65.0,
        "rsi_low": 35.0,
        "trigger_bars": 5,
        "stop_atr": 0.1,
        "target_r": 2.0,
        "entry_start": "09:45",
        "entry_end": "14:30",
    }
    learn = LEARN

    def _scan(
        self, feat: pd.DataFrame, underlying: str, extra: dict[str, Any] | None
    ) -> list[StrategySignal]:
        p = self.params
        bv = BarView(feat)
        h, lo, c, a, r, vw = bv["high"], bv["low"], bv["close"], bv["atr14"], bv["rsi14"], bv["vwap"]
        sw = int(p["swing"])
        hs, ls = pd.Series(h), pd.Series(lo)
        # pivot at p (strict vs left, >= vs right), known at p + sw
        is_hi = (hs > hs.shift(1).rolling(sw, min_periods=sw).max()) & \
            (hs >= hs.shift(-sw).rolling(sw, min_periods=sw).max())
        is_lo = (ls < ls.shift(1).rolling(sw, min_periods=sw).min()) & \
            (ls <= ls.shift(-sw).rolling(sw, min_periods=sw).min())
        is_hi, is_lo = is_hi.to_numpy(), is_lo.to_numpy()
        start, end = hhmm_to_min(p["entry_start"]), hhmm_to_min(p["entry_end"])
        out: list[StrategySignal] = []
        for _d, s, e in bv.day_slices():
            last = {Side.SELL: None, Side.BUY: None}
            pending: tuple[Side, int, int, int] | None = None   # side, pivot1, pivot2, deadline
            for i in range(s, e):
                pv = i - sw
                if pv >= s:
                    for side, flag in ((Side.SELL, is_hi), (Side.BUY, is_lo)):
                        if not flag[pv]:
                            continue
                        prev = last[side]
                        last[side] = pv
                        if prev is None or not (p["min_sep"] <= pv - prev <= p["max_sep"]):
                            continue
                        if not (np.isfinite(r[pv]) and np.isfinite(r[prev])):
                            continue
                        if side is Side.SELL:
                            ok = h[pv] > h[prev] and r[pv] < r[prev] and r[prev] >= p["rsi_high"]
                        else:
                            ok = lo[pv] < lo[prev] and r[pv] > r[prev] and r[prev] <= p["rsi_low"]
                        if ok:
                            pending = (side, prev, pv, i + int(p["trigger_bars"]))
                if pending is None:
                    continue
                side, p1, p2, deadline = pending
                if i > deadline:
                    pending = None
                    continue
                sg = side.sign
                lvl = lo[p2] if side is Side.SELL else h[p2]
                if sg * (c[i] - lvl) <= 0 or not (start <= bv.close_min[i] <= end) or not np.isfinite(a[i]):
                    continue
                pending = None
                entry = float(c[i])
                extreme = h[p2] if side is Side.SELL else lo[p2]
                stop = extreme - sg * p["stop_atr"] * a[i]
                t_r = r_targets(side, entry, stop, [p["target_r"]])[0]
                t_v = vw[i]
                use_vwap = np.isfinite(t_v) and sg * (t_v - entry) > 0 and abs(t_v - entry) < abs(t_r - entry)
                tgt = float(t_v) if use_vwap else t_r
                kind = "bearish" if side is Side.SELL else "bullish"
                pw = "higher high" if side is Side.SELL else "lower low"
                rw = "lower high" if side is Side.SELL else "higher low"
                reasons = [
                    (f"Regular {kind} divergence: price {pw} {fmt(lo[p1] if sg > 0 else h[p1])} →"
                    f" {fmt(extreme)} while RSI made a {rw} {r[p1]:.1f} → {r[p2]:.1f}"),
                    f"Pivots {p2 - p1} bars apart, each confirmed {sw} bars after it formed",
                    (f"Close {fmt(entry)} broke the second pivot bar's {'low' if sg < 0 else 'high'}"
                    f" {fmt(lvl)}"),
                    (f"Stop {fmt(stop)} beyond the extreme; target {fmt(tgt)}"
                    f" ({'VWAP' if use_vwap else str(p['target_r']) + 'R'})"),
                ]
                out.append(self._signal(bv, i, underlying, side, entry, stop, [tgt], reasons))
        return out
