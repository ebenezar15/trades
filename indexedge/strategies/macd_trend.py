"""``macd_trend``: MACD(12, 26, 9) signal-line cross on the side of the EMA 200, with histogram expansion."""

from __future__ import annotations

from typing import Any, ClassVar

import numpy as np
import pandas as pd

from indexedge.core.types import Regime, Side, StrategySignal
from indexedge.strategies.base import BarView, Strategy, fmt, hhmm_to_min, r_targets
from indexedge.strategies.pullbacks import clamp_stop

LEARN = """\
### MACD Trend (MACD cross + EMA 200 filter)

**What it is.** One of the most common indicator combos: MACD(12, 26, 9) crossing above its signal line
while price is **above the EMA 200** (only longs in an up-trend; mirror for shorts). We also require the
MACD histogram to be **expanding** (rising for 2 bars) so that the cross has momentum behind it.

**Why it can work.** The EMA 200 keeps you on the side of the bigger trend; the MACD cross times the end of
a pullback inside that trend. Requiring the classic "cross below the zero line" for longs (optional) picks
crosses that start from a dip rather than from an already-stretched move.

**When it fails.** Flat markets around the EMA 200 (many crosses, little follow-through), and late crosses
after an extended move. MACD lags by construction.

**Rules (defaults).**
- Long: MACD crosses above signal on this bar, close > EMA 200, histogram rising for 2 bars
  (zero-line filter off by default). Mirror for shorts.
- Stop: lowest low of the last 10 bars − 0.1×ATR(14) (at least 0.3×ATR, skip if > 3×ATR).
- Target 2R. Entries 09:45–14:45, max 2 per day, exit 15:15.

**Risk.** Win rate is typically below 50%; profitability depends on letting winners reach 2R.
"""


class MacdTrend(Strategy):
    id = "macd_trend"
    name = "MACD Trend"
    description = "MACD(12,26,9) signal cross on the EMA 200 side with histogram expansion; swing stop, 2R."
    regimes: ClassVar[set[Regime] | None] = {Regime.TREND_UP, Regime.TREND_DOWN}
    required_columns = ("open", "high", "low", "close", "atr14", "macd", "macd_signal", "macd_hist", "ema200")
    default_params: ClassVar[dict[str, Any]] = {
        "zero_line_filter": False,
        "swing_bars": 10,
        "stop_buffer_atr": 0.1,
        "min_stop_atr": 0.3,
        "max_stop_atr": 3.0,
        "target_r": 2.0,
        "entry_start": "09:45",
        "entry_end": "14:45",
        "max_per_day": 2,
    }
    param_grid: ClassVar[dict[str, list[Any]] | None] = {"zero_line_filter": [False, True]}
    learn = LEARN

    def _scan(
        self, feat: pd.DataFrame, underlying: str, extra: dict[str, Any] | None
    ) -> list[StrategySignal]:
        p = self.params
        bv = BarView(feat)
        h, lo, c, a = bv["high"], bv["low"], bv["close"], bv["atr14"]
        m, sig, hist, e200 = bv["macd"], bv["macd_signal"], bv["macd_hist"], bv["ema200"]
        start, end = hhmm_to_min(p["entry_start"]), hhmm_to_min(p["entry_end"])
        nb = int(p["swing_bars"])
        out: list[StrategySignal] = []
        for _d, s, e in bv.day_slices():
            count = 0
            for i in range(max(s, 2), e):
                if count >= int(p["max_per_day"]):
                    break
                if not (start <= bv.close_min[i] <= end):
                    continue
                vals = (
                    c[i],
                    a[i],
                    m[i],
                    sig[i],
                    m[i - 1],
                    sig[i - 1],
                    hist[i],
                    hist[i - 1],
                    hist[i - 2],
                    e200[i],
                )
                if not all(np.isfinite(vals)) or a[i] <= 0:
                    continue
                d_now, d_prev = m[i] - sig[i], m[i - 1] - sig[i - 1]
                if d_now > 0 >= d_prev:
                    side = Side.BUY
                elif d_now < 0 <= d_prev:
                    side = Side.SELL
                else:
                    continue
                sg = side.sign
                if sg * (c[i] - e200[i]) <= 0:
                    continue
                if not (sg * (hist[i] - hist[i - 1]) > 0 and sg * (hist[i - 1] - hist[i - 2]) > 0):
                    continue
                if p["zero_line_filter"] and sg * m[i] >= 0:
                    continue
                j0 = max(s, i - nb + 1)
                swing = float(np.min(lo[j0 : i + 1])) if sg > 0 else float(np.max(h[j0 : i + 1]))
                entry = float(c[i])
                stop = clamp_stop(
                    side, entry, swing - sg * p["stop_buffer_atr"] * a[i], a[i], p["min_stop_atr"]
                )
                if abs(entry - stop) > p["max_stop_atr"] * a[i]:
                    continue
                tg = r_targets(side, entry, stop, [p["target_r"]])
                rel = ">" if sg > 0 else "<"
                reasons = [
                    (
                        f"MACD {m[i]:.2f} crossed {'above' if sg > 0 else 'below'} signal {sig[i]:.2f}"
                        f" (was {m[i - 1]:.2f} vs {sig[i - 1]:.2f})"
                    ),
                    f"Close {fmt(entry)} {rel} EMA 200 {fmt(e200[i])}",
                    (f"Histogram expanding: {hist[i - 2]:.2f} → {hist[i - 1]:.2f} → {hist[i]:.2f}"),
                    (
                        f"Stop {fmt(stop)} beyond the {nb}-bar swing {fmt(swing)}, risk"
                        f" {fmt(abs(entry - stop))} pts;"
                        f" target {fmt(tg[0])} ({p['target_r']}R)"
                    ),
                ]
                out.append(self._signal(bv, i, underlying, side, entry, stop, tg, reasons))
                count += 1
        return out
