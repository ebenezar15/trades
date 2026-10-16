"""``orb15``: Opening Range Breakout (15-minute range; ``range_minutes=5`` gives the first-candle variant)."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from indexedge.core.types import Regime, Side, StrategySignal
from indexedge.strategies.base import BarView, Strategy, fmt, hhmm_to_min

LEARN = """\
### Opening Range Breakout (ORB-15)

**What it is.** The high and low of the first 15 minutes (09:15–09:30) form the *opening range*. A 5-minute
candle that **closes** beyond that range, on above-average volume, is traded in the breakout direction.

**Why it can work.** The first minutes absorb overnight news and order imbalances. A decisive close outside the
range, with volume, often marks the side that "won" the open and the start of a trend day.

**When it fails.** Range days and choppy, low-volume sessions produce false breakouts that snap back into the
range. Very wide opening ranges (gap days) put the stop far away, so the day is skipped.

**Rules (defaults).**
- Range = 09:15–09:30. Entry on a 5-min **close** beyond range high/low ± 0.05% buffer, until 11:30.
- Volume of the breakout candle ≥ 1.5× the average of the previous 20 bars (skipped when volume is unavailable).
- Stop at the opposite end of the range, but never more than 3×ATR(14) away.
- Skip the day if the range is wider than 0.8% (Nifty) / 1.0% (Bank Nifty).
- Targets 1R (book half, move stop to breakeven) and 2R. One trade per direction per day; exit by 15:15.

**Risk.** Breakouts fail often (win rates near 50% are typical); the edge, if any, comes from the 2R target.
The backtest exits the full position (it does not book the 50% partial) — it trails to breakeven after 1R.
"""


class ORB15(Strategy):
    id = "orb15"
    name = "Opening Range Breakout"
    description = "Trade a 5-min close beyond the 09:15–09:30 range with volume confirmation."
    regimes = {Regime.TREND_UP, Regime.TREND_DOWN, Regime.HIGH_VOL}
    default_params: dict[str, Any] = {
        "range_minutes": 15,
        "buffer_pct": 0.05,
        "vol_mult": 1.5,
        "vol_avg_bars": 20,
        "atr_stop_cap": 3.0,
        "max_range_pct": {"NIFTY": 0.8, "BANKNIFTY": 1.0},
        "entry_end": "11:30",
        "targets_r": [1.0, 2.0],
    }
    param_grid = {"vol_mult": [1.2, 1.5, 2.0]}
    learn = LEARN

    def _scan(self, feat: pd.DataFrame, underlying: str, extra: dict[str, Any] | None) -> list[StrategySignal]:
        p = self.params
        bv = BarView(feat)
        h, lo, c, v, a = bv["high"], bv["low"], bv["close"], bv["volume"], bv["atr14"]
        n_avg = int(p["vol_avg_bars"])
        vol_avg = pd.Series(v).shift(1).rolling(n_avg, min_periods=n_avg).mean().to_numpy()
        rng_end = 9 * 60 + 15 + int(p["range_minutes"])
        end_min = hhmm_to_min(p["entry_end"])
        buf = float(p["buffer_pct"]) / 100.0
        max_rng = float(self._param("max_range_pct", underlying))
        out: list[StrategySignal] = []
        for _d, s, e in bv.day_slices():
            if bv.minute[s] != 9 * 60 + 15:
                continue                      # session must start at the open
            in_rng = np.flatnonzero(bv.minute[s:e] < rng_end) + s
            if len(in_rng) == 0 or bv.close_min[in_rng[-1]] < rng_end:
                continue
            rh, rl = float(np.max(h[in_rng])), float(np.min(lo[in_rng]))
            width_pct = (rh - rl) / rl * 100.0
            if width_pct > max_rng:
                continue
            done: set[Side] = set()
            for i in range(in_rng[-1] + 1, e):
                if bv.close_min[i] > end_min or len(done) == 2:
                    break
                if not np.isfinite(a[i]) or a[i] <= 0:
                    continue
                for side, level in ((Side.BUY, rh * (1 + buf)), (Side.SELL, rl * (1 - buf))):
                    if side in done or side.sign * (c[i] - level) <= 0:
                        continue
                    has_vol = bool(np.isfinite(vol_avg[i]) and vol_avg[i] > 0)
                    ratio = v[i] / vol_avg[i] if has_vol else float("nan")
                    if has_vol and ratio < float(p["vol_mult"]):
                        continue
                    entry = float(c[i])
                    opposite = rl if side is Side.BUY else rh
                    cap = entry - side.sign * float(p["atr_stop_cap"]) * a[i]
                    stop = max(opposite, cap) if side is Side.BUY else min(opposite, cap)
                    risk = abs(entry - stop)
                    targets = [entry + side.sign * r * risk for r in p["targets_r"]]
                    word = "high" if side is Side.BUY else "low"
                    lvl = rh if side is Side.BUY else rl
                    reasons = [
                        f"Close {fmt(entry)} broke the {p['range_minutes']}-min opening-range {word} {fmt(lvl)}"
                        f" (buffer {p['buffer_pct']}%)",
                        f"Opening range {fmt(rl)}–{fmt(rh)} is {width_pct:.2f}% wide (limit {max_rng}%)",
                        (f"Volume {ratio:.1f}× the {n_avg}-bar average (needs ≥ {p['vol_mult']}×)" if has_vol
                         else "Volume unavailable, volume filter skipped"),
                        f"Stop {fmt(stop)} ({'range end' if stop == opposite else 'capped at 3×ATR'}),"
                        f" risk {fmt(risk)} pts; targets {', '.join(fmt(t) for t in targets)}",
                    ]
                    out.append(self._signal(bv, i, underlying, side, entry, stop, targets, reasons))
                    done.add(side)
        return out
