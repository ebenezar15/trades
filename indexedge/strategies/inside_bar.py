"""``inside_bar_breakout``: 15-min mother/inside-bar pattern, 5-min close breakout trigger."""
from __future__ import annotations

from typing import Any, ClassVar

import numpy as np
import pandas as pd

from indexedge.core.types import Side, StrategySignal
from indexedge.features.indicators import atr, session_dates
from indexedge.strategies.base import BarView, Strategy, fmt, hhmm_to_min, htf_bars, r_targets

LEARN = """\
### Inside Bar Breakout

**What it is.** A 15-minute "mother" candle is followed by an "inside" candle whose high and low are both
within the mother's range — a pause / volatility contraction. A 5-minute close beyond the mother's high (low)
triggers a long (short).

**Why it can work.** The inside bar shows balance; the side that breaks the mother's range often continues for
at least one more range-length.

**When it fails.** Oversized mother bars (stop too far) and tiny ones (noise) — both are skipped. Midday chop
produces many false breaks.

**Rules (defaults).**
- 15-min mother/inside detection in the same session; the mother's range must be between 0.3× and 1.2× ATR(14,
  15-min).
- Trigger: first 5-min close beyond mother high/low ± 0.03% after the inside bar completes (until the next
  15-min bar completes).
- Stop: the opposite side of the inside bar; if that is closer than 0.2×ATR(14, 5-min), the mother bar's side.
- Target 2R. Entries 09:45–14:00, max 2 trades per day, exit by 15:15.
"""


class InsideBarBreakout(Strategy):
    id = "inside_bar_breakout"
    name = "Inside Bar Breakout"
    description = "Break of a 15-min mother bar after an inside bar, triggered by a 5-min close."
    default_params: ClassVar[dict[str, Any]] = {
        "htf_minutes": 15,
        "buffer_pct": 0.03,
        "max_mother_atr": 1.2,
        "min_mother_atr": 0.3,
        "min_stop_atr5": 0.2,
        "target_r": 2.0,
        "entry_start": "09:45",
        "entry_end": "14:00",
        "max_per_day": 2,
    }
    learn = LEARN

    def _scan(
        self, feat: pd.DataFrame, underlying: str, extra: dict[str, Any] | None
    ) -> list[StrategySignal]:
        p = self.params
        bv = BarView(feat)
        c, a5 = bv["close"], bv["atr14"]
        hm = int(p["htf_minutes"])
        htf, pos = htf_bars(bv, hm)
        if len(htf) < 3:
            return []
        hh, ll = htf["high"].to_numpy(), htf["low"].to_numpy()
        a15 = atr(htf, 14).to_numpy()
        hd = session_dates(htf.index)
        start, end = hhmm_to_min(p["entry_start"]), hhmm_to_min(p["entry_end"])
        buf = p["buffer_pct"] / 100.0
        per_day: dict[Any, int] = {}
        out: list[StrategySignal] = []
        for j in range(1, len(htf)):
            i0 = pos[j]
            if i0 < 0 or pos[j - 1] < 0 or hd[j] != hd[j - 1] or not np.isfinite(a15[j]):
                continue
            if not (hh[j] < hh[j - 1] and ll[j] > ll[j - 1]):
                continue
            m_rng = hh[j - 1] - ll[j - 1]
            if not (p["min_mother_atr"] * a15[j] <= m_rng <= p["max_mother_atr"] * a15[j]):
                continue
            # setup lives until the next HTF bar completes (or the session ends)
            stop_i = pos[j + 1] if j + 1 < len(htf) and pos[j + 1] >= 0 else bv.n - 1
            up, dn = hh[j - 1] * (1 + buf), ll[j - 1] * (1 - buf)
            for i in range(i0 + 1, min(stop_i, bv.n - 1) + 1):
                if bv.dates[i] != hd[j]:
                    break
                d = bv.dates[i]
                if per_day.get(d, 0) >= int(p["max_per_day"]):
                    break
                if c[i] > up:
                    side = Side.BUY
                elif c[i] < dn:
                    side = Side.SELL
                else:
                    continue
                if not (start <= bv.close_min[i] <= end) or not np.isfinite(a5[i]):
                    break
                sg = side.sign
                entry = float(c[i])
                stop = ll[j] if sg > 0 else hh[j]
                which = "inside bar"
                if abs(entry - stop) < p["min_stop_atr5"] * a5[i]:
                    stop, which = (ll[j - 1] if sg > 0 else hh[j - 1]), "mother bar"
                tg = r_targets(side, entry, stop, [p["target_r"]])
                reasons = [
                    (f"{hm}-min inside bar {fmt(ll[j])}–{fmt(hh[j])} inside mother"
                    f" {fmt(ll[j - 1])}–{fmt(hh[j - 1])}"),
                    (f"Mother range {fmt(m_rng)} = {m_rng / a15[j]:.2f}× ATR({hm}m) (allowed"
                    f" {p['min_mother_atr']}–{p['max_mother_atr']}×)"),
                    (f"5-min close {fmt(entry)} broke the mother {'high' if sg > 0 else 'low'}"
                    f" {fmt(hh[j - 1] if sg > 0 else ll[j - 1])}"),
                    f"Stop {fmt(stop)} ({which}), target {fmt(tg[0])} ({p['target_r']}R)",
                ]
                out.append(self._signal(bv, i, underlying, side, entry, stop, tg, reasons))
                per_day[d] = per_day.get(d, 0) + 1
                break
        return out
