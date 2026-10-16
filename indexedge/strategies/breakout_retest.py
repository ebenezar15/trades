"""``breakout_retest``: break of the previous-day high/low or 15-min opening range, then a holding retest."""

from __future__ import annotations

from typing import Any, ClassVar

import numpy as np
import pandas as pd

from indexedge.core.types import Regime, Side, StrategySignal
from indexedge.strategies.base import BarView, Strategy, fmt, hhmm_to_min, r_targets
from indexedge.strategies.pullbacks import clamp_stop

LEARN = """\
### Breakout + Retest (previous-day high/low, 15-minute opening range)

**What it is.** Instead of chasing the first candle that breaks a key level, wait for price to **come back
and retest** the broken level and **hold** it (the retest candle dips to the level but closes back in the
breakout direction). Levels: previous day's high/low (PDH/PDL) and the 09:15–09:30 opening range high/low.

**Why it can work.** Old resistance often turns into support ("polarity"): traders who missed the break buy
the retest and shorts that were trapped cover there. Waiting for the retest filters many false breakouts and
gives a tight stop just beyond the level.

**When it fails.** Strong breakouts that never retest (missed trade — acceptable) and failed breakouts whose
retest closes back inside the old range (rule invalidates the setup). Low-volatility days produce many
marginal breaks.

**Rules (defaults).**
- Break: a 5-minute close beyond the level by ≥ 0.1×ATR(14), with the previous close on the other side.
- Retest: within the next 12 bars a candle trades back to within 0.15×ATR of the level, and closes back
  beyond it in the breakout direction (bullish candle for longs). Any close back through the level by more
  than 0.25×ATR first cancels the setup.
- Stop: the lower of the level and the retest low, minus 0.25×ATR (at least 0.3×ATR).
- Targets 1R (partial, stop to breakeven) and 2R. One trade per level per day, max 2 per day,
  entries 09:35–14:45, exit 15:15.

**Risk.** Gap days that open beyond PDH/PDL have no "break" candle and are skipped by design.
"""

LEVELS = {
    "pdh": ("prev_high", Side.BUY, "previous-day high"),
    "pdl": ("prev_low", Side.SELL, "previous-day low"),
    "orh": ("or_high", Side.BUY, "15-min opening-range high"),
    "orl": ("or_low", Side.SELL, "15-min opening-range low"),
}


class BreakoutRetest(Strategy):
    id = "breakout_retest"
    name = "Breakout Retest"
    description = "Trade the retest of a broken previous-day high/low or 15-min opening range when it holds."
    regimes: ClassVar[set[Regime] | None] = {Regime.TREND_UP, Regime.TREND_DOWN, Regime.HIGH_VOL}
    required_columns = ("open", "high", "low", "close", "atr14", "prev_high", "prev_low", "or_high", "or_low")
    default_params: ClassVar[dict[str, Any]] = {
        "levels": ["pdh", "pdl", "orh", "orl"],
        "break_atr": 0.1,
        "retest_atr": 0.15,
        "fail_atr": 0.25,
        "max_wait_bars": 12,
        "stop_buffer_atr": 0.25,
        "min_stop_atr": 0.3,
        "targets_r": [1.0, 2.0],
        "entry_start": "09:35",
        "entry_end": "14:45",
        "max_per_day": 2,
    }
    param_grid: ClassVar[dict[str, list[Any]] | None] = {"max_wait_bars": [6, 12, 18]}
    learn = LEARN

    def _scan(
        self, feat: pd.DataFrame, underlying: str, extra: dict[str, Any] | None
    ) -> list[StrategySignal]:
        p = self.params
        bv = BarView(feat)
        o, h, lo, c, a = bv["open"], bv["high"], bv["low"], bv["close"], bv["atr14"]
        start, end = hhmm_to_min(p["entry_start"]), hhmm_to_min(p["entry_end"])
        names = [k for k in p["levels"] if k in LEVELS]
        out: list[StrategySignal] = []
        for _d, s, e in bv.day_slices():
            broke: dict[str, int] = {}  # level key -> bar index of the break close
            done: set[str] = set()
            count = 0
            for i in range(s + 1, e):
                if not all(np.isfinite((o[i], h[i], lo[i], c[i], a[i], c[i - 1]))) or a[i] <= 0:
                    continue
                for key in names:
                    if key in done:
                        continue
                    col, side, label = LEVELS[key]
                    lv = bv[col][i]
                    if not np.isfinite(lv):
                        continue
                    sg = side.sign
                    if key not in broke:
                        if (
                            sg * (c[i] - lv) >= p["break_atr"] * a[i]
                            and sg * (c[i - 1] - lv) <= 0
                            and np.isfinite(bv[col][i - 1])
                        ):
                            broke[key] = i
                        continue
                    b = broke[key]
                    if i - b > int(p["max_wait_bars"]) or sg * (c[i] - lv) < -p["fail_atr"] * a[i]:
                        done.add(key)
                        continue
                    if count >= int(p["max_per_day"]) or not (start <= bv.close_min[i] <= end):
                        continue
                    ext = lo[i] if sg > 0 else h[i]
                    touched = sg * (ext - lv) <= p["retest_atr"] * a[i]
                    if not touched or sg * (c[i] - lv) <= 0 or sg * (c[i] - o[i]) <= 0:
                        continue
                    entry = float(c[i])
                    base = min(lv, ext) if sg > 0 else max(lv, ext)
                    stop = clamp_stop(
                        side, entry, base - sg * p["stop_buffer_atr"] * a[i], a[i], p["min_stop_atr"]
                    )
                    tg = r_targets(side, entry, stop, list(p["targets_r"]))
                    bt = bv.index[b].strftime("%H:%M")
                    reasons = [
                        (
                            f"Broke the {label} {fmt(lv)} with a close at {fmt(c[b])} ({bt} bar,"
                            f" ≥ {p['break_atr']}× ATR beyond)"
                        ),
                        (
                            f"Retest {i - b} bar(s) later: {'low' if sg > 0 else 'high'} {fmt(ext)}"
                            f" came back within"
                            f" {p['retest_atr']}× ATR of the level"
                        ),
                        (
                            f"Held: close {fmt(entry)} back {'above' if sg > 0 else 'below'} {fmt(lv)} with a"
                            f" {'bullish' if sg > 0 else 'bearish'} candle"
                        ),
                        (
                            f"Stop {fmt(stop)} beyond the level − {p['stop_buffer_atr']}× ATR, risk"
                            f" {fmt(abs(entry - stop))} pts; targets {', '.join(fmt(t) for t in tg)}"
                        ),
                    ]
                    out.append(self._signal(bv, i, underlying, side, entry, stop, tg, reasons))
                    done.add(key)
                    count += 1
                    break
        return out
