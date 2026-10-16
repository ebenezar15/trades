"""CPR strategies: ``cpr_pivot`` (narrow-day breakout / wide-day reversion) and ``cpr_vwap`` (confluence)."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from indexedge.core.types import Side, StrategySignal
from indexedge.features.indicators import atr
from indexedge.strategies.base import BarView, Strategy, fmt, hhmm_to_min, htf_bars

LEARN_PIVOT = """\
### CPR (Central Pivot Range) + floor pivots

**What it is.** From yesterday's high, low and close: Pivot P = (H+L+C)/3, BC = (H+L)/2, TC = 2P − BC, plus
floor pivots R1/R2 and S1/S2. The *width* of the CPR (TC − BC as % of P) classifies the day.

**Why it can work.** A **narrow** CPR means yesterday was balanced; the market often trends away from such a
range (breakout mode). A **wide** CPR means yesterday travelled far; today often rotates back toward the
pivot (reversion mode). Many traders watch these exact levels, which can make them self-fulfilling.

**When it fails.** CPR width does not predict *direction*, and level-based charts are easy to over-fit in
hindsight. Strong news days ignore pivots altogether.

**Rules (defaults).**
- Narrow if width < 0.15% (Nifty) / 0.20% (Bank Nifty) or below the 20-day 25th percentile.
  Wide if width > 0.50% or above the 20-day 75th percentile. Normal days: no trade.
- Narrow day: first 15-min close above TC (below BC) → long (short). Stop = tighter of BC (TC) and the signal
  candle's extreme. Targets R1 then R2 (S1/S2).
- Wide day: fade the first touch of R1/S1 when a 5-min candle closes back inside with a rejection body (within
  3 bars of the touch). Target the pivot; stop 0.5×ATR(14, 15-min) beyond the level.
- Window 09:30–14:00, exit by 15:15.

**Risk.** Treat as a level map more than a forecast; sample sizes per day-type are small.
"""

LEARN_CPR_VWAP = """\
### CPR + VWAP confluence

**What it is.** A trend-day setup that needs two independent levels to agree. At 09:30, price must already be
above both TC and VWAP (long bias) — or below BC and VWAP (short bias) — on a narrow/normal CPR day.

**Why it can work.** When the opening drive holds above yesterday's value area (CPR) *and* today's average
price (VWAP), dips back to the higher of the two are often bought.

**When it fails.** Wide-CPR days and days where the opening drive fully reverses; one trade per day caps the
damage.

**Rules (defaults).** CPR width ≤ 0.35%. Bias fixed at the 09:30 close. Entry: a 5-min pullback to
max(VWAP, TC) (min(VWAP, BC) for shorts) followed by a close back in the trend direction.
Stop below min(BC, VWAP − 0.5×ATR(14)). Targets R1 / R2 (S1 / S2). Window 09:30–13:30, max 1 trade per day.

**Risk.** Targets are fixed levels, so reward-to-risk varies widely from day to day.
"""


def _first_by_day(bv: BarView) -> dict[Any, int]:
    return {d: s for d, s, _e in bv.day_slices()}


def _day_width_rank(bv: BarView, width: np.ndarray, lookback: int) -> dict[Any, tuple[float, float, float]]:
    """For each session: (width, q25, q75) where the quantiles use the previous ``lookback`` sessions."""
    days = bv.day_slices()
    widths = [width[s] for _d, s, _e in days]
    out: dict[Any, tuple[float, float, float]] = {}
    for k, (d, _s, _e) in enumerate(days):
        prev = np.array([w for w in widths[max(0, k - lookback):k] if np.isfinite(w)])
        q25, q75 = (float(np.quantile(prev, 0.25)), float(np.quantile(prev, 0.75))) if len(prev) >= 5 \
            else (np.nan, np.nan)
        out[d] = (float(widths[k]), q25, q75)
    return out


class CprPivot(Strategy):
    id = "cpr_pivot"
    name = "CPR + Floor Pivots"
    description = "Narrow-CPR breakout beyond TC/BC, or wide-CPR fade of R1/S1 back to the pivot."
    required_columns = ("open", "high", "low", "close", "atr14", "pivot", "tc", "bc", "r1", "r2", "s1", "s2",
                        "cpr_width_pct")
    default_params: dict[str, Any] = {
        "narrow_pct": {"NIFTY": 0.15, "BANKNIFTY": 0.20},
        "wide_pct": 0.50,
        "pct_lookback": 20,
        "htf_minutes": 15,
        "reject_bars": 3,
        "level_stop_atr": 0.5,
        "entry_start": "09:30",
        "entry_end": "14:00",
    }
    learn = LEARN_PIVOT

    def _scan(self, feat: pd.DataFrame, underlying: str, extra: dict[str, Any] | None) -> list[StrategySignal]:
        p = self.params
        bv = BarView(feat)
        o, h, lo, c = bv["open"], bv["high"], bv["low"], bv["close"]
        pv, tc, bc, r1, r2, s1, s2 = (bv[k] for k in ("pivot", "tc", "bc", "r1", "r2", "s1", "s2"))
        start, end = hhmm_to_min(p["entry_start"]), hhmm_to_min(p["entry_end"])
        hm = int(p["htf_minutes"])
        htf, pos = htf_bars(bv, hm)
        a15 = atr(htf, 14).to_numpy() if len(htf) else np.array([])
        # latest completed-HTF ATR visible at each base bar
        a15_base = np.full(bv.n, np.nan)
        for j, i in enumerate(pos):
            if i >= 0:
                a15_base[i] = a15[j]
        a15_base = pd.Series(a15_base).groupby(bv.dates).ffill().to_numpy()
        ranks = _day_width_rank(bv, bv["cpr_width_pct"], int(p["pct_lookback"]))
        narrow_abs = float(self._param("narrow_pct", underlying))
        out: list[StrategySignal] = []
        htf_close_bar = {int(i): j for j, i in enumerate(pos) if i >= 0}
        hl = htf[["high", "low", "close"]].to_numpy() if len(htf) else np.zeros((0, 3))
        for d, s, e in bv.day_slices():
            w, q25, q75 = ranks[d]
            if not np.isfinite(w) or not np.isfinite(pv[s]):
                continue
            narrow = w < narrow_abs or (np.isfinite(q25) and w < q25)
            wide = w > p["wide_pct"] or (np.isfinite(q75) and w > q75)
            if narrow:
                kind = f"narrow CPR ({w:.3f}% < {narrow_abs}%" + (f" or 20-day 25th pct {q25:.3f}%)"
                                                                 if np.isfinite(q25) else ")")
                done: set[Side] = set()
                for i in range(s, e):
                    if i not in htf_close_bar or not (start <= bv.close_min[i] <= end):
                        continue
                    hh, ll, cc = hl[htf_close_bar[i]]
                    for side, lvl, opp in ((Side.BUY, tc[i], bc[i]), (Side.SELL, bc[i], tc[i])):
                        sg = side.sign
                        if side in done or sg * (cc - lvl) <= 0:
                            continue
                        done.add(side)
                        entry = float(c[i])
                        ext = ll if side is Side.BUY else hh
                        stop = max(opp, ext) if side is Side.BUY else min(opp, ext)
                        lv = (r1[i], r2[i]) if side is Side.BUY else (s1[i], s2[i])
                        tg = [float(t) for t in lv if np.isfinite(t) and sg * (t - entry) > 0]
                        if not tg or sg * (entry - stop) <= 0:
                            continue
                        names = ("TC", "R1/R2") if side is Side.BUY else ("BC", "S1/S2")
                        reasons = [
                            f"Today is a {kind}: breakout mode",
                            f"{hm}-min close {fmt(cc)} {'above' if sg > 0 else 'below'} {names[0]} {fmt(lvl)}",
                            f"Stop {fmt(stop)} ({'CPR' if stop == opp else 'signal-candle extreme'}),"
                            f" targets {names[1]} {', '.join(fmt(t) for t in tg)}",
                        ]
                        out.append(self._signal(bv, i, underlying, side, entry, stop, tg, reasons))
            elif wide:
                kind = f"wide CPR ({w:.3f}% > {p['wide_pct']}%" + (f" or 20-day 75th pct {q75:.3f}%)"
                                                                  if np.isfinite(q75) else ")")
                for side, lvl_arr, name in ((Side.SELL, r1, "R1"), (Side.BUY, s1, "S1")):
                    sg = side.sign
                    touch = None
                    for i in range(s, e):
                        lvl = lvl_arr[i]
                        if not np.isfinite(lvl):
                            break
                        if touch is None and ((side is Side.SELL and h[i] >= lvl) or
                                              (side is Side.BUY and lo[i] <= lvl)):
                            touch = i
                        if touch is None:
                            continue
                        if i > touch + int(p["reject_bars"]) - 1:
                            break
                        if not (start <= bv.close_min[i] <= end):
                            continue
                        rejected = sg * (c[i] - lvl) > 0 and sg * (c[i] - o[i]) > 0
                        if not rejected or not np.isfinite(a15_base[i]):
                            continue
                        entry = float(c[i])
                        stop = lvl - sg * p["level_stop_atr"] * a15_base[i]
                        tgt = pv[i]
                        if sg * (tgt - entry) <= 0:
                            break
                        reasons = [
                            f"Today is a {kind}: reversion mode",
                            f"First touch of {name} {fmt(lvl)} rejected: close {fmt(entry)} back"
                            f" {'below' if side is Side.SELL else 'above'} it",
                            f"Stop {fmt(stop)} = {name} ± {p['level_stop_atr']}×ATR(15-min) {fmt(a15_base[i])};"
                            f" target pivot {fmt(tgt)}",
                        ]
                        out.append(self._signal(bv, i, underlying, side, entry, stop, [tgt], reasons))
                        break
        return out


class CprVwap(Strategy):
    id = "cpr_vwap"
    name = "CPR + VWAP Confluence"
    description = "09:30 bias above TC and VWAP (or below BC and VWAP); buy the pullback to the higher level."
    required_columns = ("open", "high", "low", "close", "atr14", "vwap", "tc", "bc", "r1", "r2", "s1", "s2",
                        "cpr_width_pct")
    default_params: dict[str, Any] = {
        "max_width_pct": 0.35,
        "bias_time": "09:30",
        "touch_atr": 0.1,
        "vwap_stop_atr": 0.5,
        "entry_end": "13:30",
    }
    learn = LEARN_CPR_VWAP

    def _scan(self, feat: pd.DataFrame, underlying: str, extra: dict[str, Any] | None) -> list[StrategySignal]:
        p = self.params
        bv = BarView(feat)
        o, h, lo, c, a, vw = bv["open"], bv["high"], bv["low"], bv["close"], bv["atr14"], bv["vwap"]
        tc, bc, r1, r2, s1, s2, wd = (bv[k] for k in ("tc", "bc", "r1", "r2", "s1", "s2", "cpr_width_pct"))
        bias_min, end = hhmm_to_min(p["bias_time"]), hhmm_to_min(p["entry_end"])
        out: list[StrategySignal] = []
        for _d, s, e in bv.day_slices():
            if not np.isfinite(wd[s]) or wd[s] > p["max_width_pct"]:
                continue
            b = next((i for i in range(s, e) if bv.close_min[i] == bias_min), None)
            if b is None or not all(np.isfinite((tc[b], bc[b], vw[b]))):
                continue
            if c[b] > tc[b] and c[b] > vw[b]:
                side = Side.BUY
            elif c[b] < bc[b] and c[b] < vw[b]:
                side = Side.SELL
            else:
                continue
            sg = side.sign
            pulled = False
            for i in range(b + 1, e):
                if bv.close_min[i] > end:
                    break
                if not np.isfinite(a[i]) or not np.isfinite(vw[i]):
                    continue
                lvl = max(vw[i], tc[i]) if side is Side.BUY else min(vw[i], bc[i])
                tol = p["touch_atr"] * a[i]
                if (side is Side.BUY and lo[i] <= lvl + tol) or (side is Side.SELL and h[i] >= lvl - tol):
                    pulled = True
                if not pulled or sg * (c[i] - lvl) <= 0 or sg * (c[i] - o[i]) <= 0:
                    continue
                entry = float(c[i])
                if side is Side.BUY:
                    stop = min(bc[i], vw[i] - p["vwap_stop_atr"] * a[i])
                    lv = (r1[i], r2[i])
                else:
                    stop = max(tc[i], vw[i] + p["vwap_stop_atr"] * a[i])
                    lv = (s1[i], s2[i])
                tg = [float(t) for t in lv if np.isfinite(t) and sg * (t - entry) > 0]
                if not tg:
                    break
                names = ("TC", "BC") if side is Side.BUY else ("BC", "TC")
                rel = "above" if side is Side.BUY else "below"
                reasons = [
                    f"CPR width {wd[s]:.3f}% ≤ {p['max_width_pct']}% (narrow/normal day)",
                    f"At {p['bias_time']} close {fmt(c[b])} was {rel} {names[0]} {fmt(tc[b] if sg > 0 else bc[b])}"
                    f" and VWAP {fmt(vw[b])}",
                    f"Pulled back to {fmt(lvl)} and closed {rel} it at {fmt(entry)}",
                    f"Stop {fmt(stop)}, targets {', '.join(fmt(t) for t in tg)}",
                ]
                out.append(self._signal(bv, i, underlying, side, entry, stop, tg, reasons))
                break
        return out
