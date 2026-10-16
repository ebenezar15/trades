"""``gap_fill``: opening-gap fade toward the previous close, with automatic gap-and-go mode."""
from __future__ import annotations

from typing import Any, ClassVar

import numpy as np
import pandas as pd

from indexedge.core.types import Side, StrategySignal
from indexedge.strategies.base import BarView, Strategy, fmt, hhmm_to_min, r_targets

LEARN = """\
### Opening Gap Fill / Gap-and-Go

**What it is.** When Nifty opens 0.3–1.5% away from yesterday's close (Bank Nifty 0.4–1.8%), the gap often
partially "fills" as overnight excitement fades. After the first 15-minute candle, a 5-min close back through
that candle's low (gap-up) or high (gap-down) **and** through VWAP is faded toward yesterday's close.

**Why it can work.** Overnight gaps are driven by global cues and thin pre-open orders; once local
participants trade, prices often mean-revert toward the prior value area.

**When it fails.** Large gaps on real news rarely fill (one study found only ~29% of ≥1% gap-ups filled the
same day). If the first 15-minute candle closes strongly *in* the gap direction with a large range, the
strategy switches to **gap-and-go** and trades the continuation instead.

**Rules (defaults).**
- Gap filter as above. Fade: 5-min close through first-candle low/high and VWAP. Targets: half the gap, then
  the previous close (full fill). Stop: the day's extreme so far ± 0.1×ATR(14).
- Gap-and-go (auto): first 15-min candle closes in the gap direction with range > 1.2× the 10-day average
  first-candle range → trade a close beyond its high (low); stop at its other end; target 2R.
- Entries 09:30–11:00, one trade per day, exit by 15:15.

**Risk.** Gap days are volatile; stops are wide. The fade and the go modes contradict each other by design —
the first candle decides which one applies.
"""


class GapFill(Strategy):
    id = "gap_fill"
    name = "Gap Fill / Gap-and-Go"
    description = "Fade a 0.3–1.5% opening gap toward the previous close (or ride it on a strong first candle)."
    required_columns = ("open", "high", "low", "close", "atr14", "vwap", "prev_close")
    default_params: ClassVar[dict[str, Any]] = {
        "min_gap_pct": {"NIFTY": 0.30, "BANKNIFTY": 0.40},
        "max_gap_pct": {"NIFTY": 1.50, "BANKNIFTY": 1.80},
        "first_minutes": 15,
        "go_range_mult": 1.2,
        "go_lookback_days": 10,
        "stop_atr": 0.1,
        "go_target_r": 2.0,
        "entry_end": "11:00",
    }
    learn = LEARN

    def _scan(self, feat: pd.DataFrame, underlying: str, extra: dict[str, Any] | None) -> list[StrategySignal]:
        p = self.params
        bv = BarView(feat)
        o, h, lo, c, a, vw, pc = (bv[k] for k in ("open", "high", "low", "close", "atr14", "vwap", "prev_close"))
        first_end = 9 * 60 + 15 + int(p["first_minutes"])
        end = hhmm_to_min(p["entry_end"])
        gmin, gmax = float(self._param("min_gap_pct", underlying)), float(self._param("max_gap_pct", underlying))
        ranges: list[float] = []                      # previous sessions' first-candle ranges
        out: list[StrategySignal] = []
        for _d, s, e in bv.day_slices():
            if bv.minute[s] != 9 * 60 + 15:
                continue
            fc = np.flatnonzero(bv.minute[s:e] < first_end) + s
            if len(fc) == 0 or bv.close_min[fc[-1]] < first_end:
                continue
            f_hi, f_lo = float(np.max(h[fc])), float(np.min(lo[fc]))
            f_open, f_close = float(o[s]), float(c[fc[-1]])
            f_rng = f_hi - f_lo
            hist = ranges[-int(p["go_lookback_days"]):]
            ranges.append(f_rng)
            prev = pc[s]
            if not np.isfinite(prev) or prev <= 0:
                continue
            gap = (f_open - prev) / prev * 100.0
            if not (gmin <= abs(gap) <= gmax):
                continue
            gsg = 1 if gap > 0 else -1
            avg_rng = float(np.mean(hist)) if len(hist) >= 3 else np.nan
            go = (np.isfinite(avg_rng) and gsg * (f_close - f_open) > 0 and f_rng > p["go_range_mult"] * avg_rng)
            for i in range(fc[-1] + 1, e):
                if bv.close_min[i] > end:
                    break
                if not np.isfinite(a[i]) or not np.isfinite(vw[i]):
                    continue
                if go:
                    side = Side.BUY if gsg > 0 else Side.SELL
                    lvl = f_hi if gsg > 0 else f_lo
                    if side.sign * (c[i] - lvl) <= 0:
                        continue
                    entry = float(c[i])
                    stop = f_lo if gsg > 0 else f_hi
                    tg = r_targets(side, entry, stop, [p["go_target_r"]])
                    reasons = [
                        f"Gap {gap:+.2f}% vs previous close {fmt(prev)} (allowed {gmin}–{gmax}%)",
                        (f"Gap-and-go mode: first {p['first_minutes']}-min candle closed in the gap direction with"
                        f" range {fmt(f_rng)} > {p['go_range_mult']}× the 10-day average {fmt(avg_rng)}"),
                        f"Close {fmt(entry)} beyond the first-candle {'high' if gsg > 0 else 'low'} {fmt(lvl)}",
                        (f"Stop {fmt(stop)} (other end of the first candle), target {fmt(tg[0])}"
                        f" ({p['go_target_r']}R)"),
                    ]
                else:
                    side = Side.SELL if gsg > 0 else Side.BUY
                    sg = side.sign
                    lvl = f_lo if gsg > 0 else f_hi
                    if sg * (c[i] - lvl) <= 0 or sg * (c[i] - vw[i]) <= 0:
                        continue
                    entry = float(c[i])
                    ext = np.max(h[s:i + 1]) if gsg > 0 else np.min(lo[s:i + 1])
                    stop = float(ext - sg * p["stop_atr"] * a[i])
                    half = prev + (f_open - prev) / 2.0
                    tg = [t for t in (half, prev) if sg * (t - entry) > 0]
                    if not tg:
                        break
                    reasons = [
                        f"Gap {gap:+.2f}% vs previous close {fmt(prev)} (allowed {gmin}–{gmax}%)",
                        (f"Fade mode: close {fmt(entry)} back through the first-candle"
                        f" {'low' if gsg > 0 else 'high'} {fmt(lvl)} and VWAP {fmt(vw[i])}"),
                        f"Targets {', '.join(fmt(t) for t in tg)} (half gap / full fill)",
                        f"Stop {fmt(stop)} beyond the day's {'high' if gsg > 0 else 'low'} {fmt(ext)}",
                    ]
                out.append(self._signal(bv, i, underlying, side, entry, stop, tg, reasons))
                break
        return out
