"""Pullback / trend-continuation strategies: ``ema_pullback``, ``fib_pullback`` and ``supertrend_pullback``.

All three share the same idea — join an existing intraday trend after a counter-move — but differ in how the
"value zone" is defined (EMA 21 band, Fibonacci 38.2–61.8% of the last impulse leg, or the Supertrend line).
Every decision at bar ``i`` uses only bars ``<= i``; swing points come from the ``swing_high``/``swing_low``
feature columns, which are reported on their confirmation bar (``right`` = 3 bars after the pivot).
"""

from __future__ import annotations

from typing import Any, ClassVar

import numpy as np
import pandas as pd

from indexedge.core.types import Regime, Side, StrategySignal
from indexedge.strategies.base import BarView, Strategy, fmt, hhmm_to_min, r_targets

SWING_RIGHT = 3  # bars between a swing pivot and its confirmation (features.engine uses (3, 3))


def clamp_stop(side: Side, entry: float, stop: float, atr: float, min_atr: float) -> float:
    """Push ``stop`` out so it is at least ``min_atr`` × ATR away from ``entry``."""
    m = entry - side.sign * min_atr * atr
    return min(stop, m) if side is Side.BUY else max(stop, m)


def close_position(o: float, h: float, lo: float, c: float, side: Side) -> float:
    """Close position in the candle range, measured from the side's adverse end (1 = at the best end)."""
    rng = h - lo
    if rng <= 0:
        return 0.0
    return (c - lo) / rng if side is Side.BUY else (h - c) / rng


# ---------------------------------------------------------------------------------------------- ema_pullback
LEARN_EMA_PB = """\
### EMA Pullback (20/50 EMA trend, rejection candle)

**What it is.** The most-taught retail pullback setup: in an up-trend (EMA 20 above EMA 50, price above the
EMA 200) price dips back to the EMA 20 "value zone", prints a **rejection candle** (closes back above the EMA
with the close near the candle high), and we join the trend. Mirror for shorts. IndexEdge uses EMA 21 as the
EMA 20 (they are practically identical on 5-minute bars).

**Why it can work.** In a trend, short-term traders buy dips to a widely watched average; the 50/200 EMAs
make sure the trend is real, and waiting for the rejection candle avoids catching a pullback that is turning
into a reversal.

**When it fails.** Flat or tangled EMAs (range days), the third/fourth pullback of a tired trend, and sharp
news-driven reversals that slice through the EMA 50. The ADX filter (default ≥ 18) removes the flattest days.

**Rules (defaults).**
- Trend: EMA 21 > EMA 50, close > EMA 200 and ADX(14) ≥ 18 (mirror for shorts).
- Pullback: within the last 3 bars the low touched EMA 21 + 0.15×ATR(14) but stayed above EMA 50 − 0.25×ATR.
- Trigger: bullish candle closing above EMA 21 with the close in the top 40% of its range.
- Stop: pullback low − 0.1×ATR (at least 0.3×ATR, skip if > 2.5×ATR).
- Targets: 1R (partial, then stop to breakeven) and 2R. Entries 09:45–14:45, max 2 per day, exit 15:15.

**Risk.** Many small losses on choppy days; respect the daily loss cap.
"""


class EmaPullback(Strategy):
    id = "ema_pullback"
    name = "EMA Pullback"
    description = "Trend (EMA 21 > 50, price vs EMA 200, ADX) pullback to EMA 21 with a rejection candle."
    regimes: ClassVar[set[Regime] | None] = {Regime.TREND_UP, Regime.TREND_DOWN}
    required_columns = ("open", "high", "low", "close", "atr14", "ema21", "ema50", "ema200", "adx14")
    default_params: ClassVar[dict[str, Any]] = {
        "adx_min": 18.0,
        "touch_atr": 0.15,
        "ema50_tol_atr": 0.25,
        "pullback_bars": 3,
        "min_close_pos": 0.60,
        "stop_buffer_atr": 0.1,
        "min_stop_atr": 0.3,
        "max_stop_atr": 2.5,
        "targets_r": [1.0, 2.0],
        "entry_start": "09:45",
        "entry_end": "14:45",
        "max_per_day": 2,
    }
    param_grid: ClassVar[dict[str, list[Any]] | None] = {"adx_min": [0.0, 18.0, 22.0]}
    learn = LEARN_EMA_PB

    def _scan(
        self, feat: pd.DataFrame, underlying: str, extra: dict[str, Any] | None
    ) -> list[StrategySignal]:
        p = self.params
        bv = BarView(feat)
        o, h, lo, c, a = bv["open"], bv["high"], bv["low"], bv["close"], bv["atr14"]
        e21, e50, e200, ad = bv["ema21"], bv["ema50"], bv["ema200"], bv["adx14"]
        start, end = hhmm_to_min(p["entry_start"]), hhmm_to_min(p["entry_end"])
        nb = int(p["pullback_bars"])
        out: list[StrategySignal] = []
        for _d, s, e in bv.day_slices():
            count, last_i = 0, -100
            for i in range(s, e):
                if count >= int(p["max_per_day"]):
                    break
                if not (start <= bv.close_min[i] <= end) or i - last_i <= nb:
                    continue
                vals = (o[i], h[i], lo[i], c[i], a[i], e21[i], e50[i], e200[i], ad[i])
                if not all(np.isfinite(vals)) or a[i] <= 0 or ad[i] < p["adx_min"]:
                    continue
                j0 = max(s, i - nb + 1)
                for side in (Side.BUY, Side.SELL):
                    sg = side.sign
                    if sg * (e21[i] - e50[i]) <= 0 or sg * (c[i] - e200[i]) <= 0:
                        continue
                    ext = float(np.min(lo[j0 : i + 1])) if sg > 0 else float(np.max(h[j0 : i + 1]))
                    touched = sg * (ext - e21[i]) <= p["touch_atr"] * a[i]
                    held = sg * (ext - e50[i]) >= -p["ema50_tol_atr"] * a[i]
                    if not (touched and held):
                        continue
                    cpos = close_position(o[i], h[i], lo[i], c[i], side)
                    if sg * (c[i] - o[i]) <= 0 or sg * (c[i] - e21[i]) <= 0 or cpos < p["min_close_pos"]:
                        continue
                    entry = float(c[i])
                    stop = clamp_stop(
                        side, entry, ext - sg * p["stop_buffer_atr"] * a[i], a[i], p["min_stop_atr"]
                    )
                    if abs(entry - stop) > p["max_stop_atr"] * a[i]:
                        continue
                    tg = r_targets(side, entry, stop, list(p["targets_r"]))
                    rel = ">" if sg > 0 else "<"
                    reasons = [
                        (
                            f"Trend: EMA 21 {fmt(e21[i])} {rel} EMA 50 {fmt(e50[i])}, close {rel} EMA 200"
                            f" {fmt(e200[i])}, ADX {ad[i]:.1f} ≥ {p['adx_min']}"
                        ),
                        (
                            f"Pullback {'low' if sg > 0 else 'high'} {fmt(ext)} reached the EMA 21"
                            f" zone within {nb}"
                            f" bars and held EMA 50"
                        ),
                        (
                            f"Rejection candle: close {fmt(entry)} back"
                            f" {'above' if sg > 0 else 'below'} EMA 21,"
                            f" close at {cpos * 100:.0f}% of the candle range"
                        ),
                        (
                            f"Stop {fmt(stop)} beyond the pullback extreme, risk"
                            f" {fmt(abs(entry - stop))} pts;"
                            f" targets {', '.join(fmt(t) for t in tg)}"
                            f" ({', '.join(f'{r}R' for r in p['targets_r'])})"
                        ),
                    ]
                    out.append(self._signal(bv, i, underlying, side, entry, stop, tg, reasons))
                    count += 1
                    last_i = i
                    break
        return out


# ---------------------------------------------------------------------------------------------- fib_pullback
LEARN_FIB = """\
### Fibonacci Pullback (38.2–61.8% retracement of the last impulse)

**What it is.** After a clear impulse leg (a confirmed swing low followed by a confirmed swing high), price
often retraces 38.2–61.8% of the leg before the trend resumes. We wait for price to trade into that "golden
zone" and for a **reversal close** (bullish candle closing above the previous bar's high), then buy. Mirror
for a down-leg.

**Why it can work.** Fibonacci levels are drawn by a very large share of retail traders (Zerodha Varsity,
Upstox and most Indian broker portals teach them), so orders cluster there. The rule only uses swings that
are already confirmed, so the levels cannot be redrawn after the fact.

**When it fails.** Deep corrections that are really reversals (price goes through 78.6%), very small legs
(noise), and late-day legs with no time left to reach the target.

**Rules (defaults).**
- Impulse leg: last confirmed swing low → later confirmed swing high on today's chart, size ≥ 1.5×ATR(14).
- Retracement: the deepest low since the swing high lies between the 38.2% and 61.8% levels (78.6% never
  broken — that cancels the leg).
- Trigger: bullish candle that closes above the previous bar's high while still below the swing high.
- Stop: 78.6% level − 0.1×ATR (at least 0.3×ATR).
- Targets: the swing high (prior extreme) and the 1.272 extension of the leg. One trade per leg; entries
  09:45–14:45, max 2 per day, exit 15:15.

**Risk.** Swing confirmation needs 3 bars, so very fast pullbacks are missed by design.
"""


class FibPullback(Strategy):
    id = "fib_pullback"
    name = "Fibonacci Pullback"
    description = "Buy/sell a 38.2–61.8% retracement of a confirmed impulse leg on a reversal close."
    regimes: ClassVar[set[Regime] | None] = {Regime.TREND_UP, Regime.TREND_DOWN}
    required_columns = ("open", "high", "low", "close", "atr14", "swing_high", "swing_low")
    default_params: ClassVar[dict[str, Any]] = {
        "min_leg_atr": 1.5,
        "zone_shallow": 0.382,
        "zone_deep": 0.618,
        "invalid_level": 0.786,
        "extension": 1.272,
        "stop_buffer_atr": 0.1,
        "min_stop_atr": 0.3,
        "max_wait_bars": 24,
        "entry_start": "09:45",
        "entry_end": "14:45",
        "max_per_day": 2,
    }
    param_grid: ClassVar[dict[str, list[Any]] | None] = {"min_leg_atr": [1.0, 1.5, 2.0]}
    learn = LEARN_FIB

    def _scan(
        self, feat: pd.DataFrame, underlying: str, extra: dict[str, Any] | None
    ) -> list[StrategySignal]:
        p = self.params
        bv = BarView(feat)
        o, h, lo, c, a = bv["open"], bv["high"], bv["low"], bv["close"], bv["atr14"]
        sh, sl = bv["swing_high"], bv["swing_low"]
        start, end = hhmm_to_min(p["entry_start"]), hhmm_to_min(p["entry_end"])
        z1, z2, inv, ext_k = p["zone_shallow"], p["zone_deep"], p["invalid_level"], p["extension"]
        out: list[StrategySignal] = []
        for _d, s, e in bv.day_slices():
            hi_v = lo_v = np.nan
            hi_p = lo_p = -1
            used: set[tuple[int, int]] = set()
            count = 0
            for i in range(s, e):
                # register swings confirmed on this bar (pivot = i - SWING_RIGHT, must be inside today)
                piv = i - SWING_RIGHT
                if piv >= s:
                    if np.isfinite(sh[i]):
                        hi_v, hi_p = float(sh[i]), piv
                    if np.isfinite(sl[i]):
                        lo_v, lo_p = float(sl[i]), piv
                if count >= int(p["max_per_day"]) or hi_p < 0 or lo_p < 0:
                    continue
                if not (start <= bv.close_min[i] <= end) or i == s:
                    continue
                if not all(np.isfinite((o[i], h[i], lo[i], c[i], a[i], h[i - 1], lo[i - 1]))) or a[i] <= 0:
                    continue
                side = Side.BUY if hi_p > lo_p else Side.SELL
                key = (lo_p, hi_p)
                if key in used:
                    continue
                sg = side.sign
                leg = hi_v - lo_v
                if leg < p["min_leg_atr"] * a[i]:
                    continue
                origin, extreme = (lo_v, hi_v) if sg > 0 else (hi_v, lo_v)
                last_p = max(hi_p, lo_p)
                if i - last_p > int(p["max_wait_bars"]):
                    continue
                lvl1 = extreme - sg * z1 * leg  # 38.2%
                lvl2 = extreme - sg * z2 * leg  # 61.8%
                lvl_inv = extreme - sg * inv * leg  # 78.6%
                seg = slice(last_p + 1, i + 1)
                deepest = float(np.min(lo[seg])) if sg > 0 else float(np.max(h[seg]))
                if sg * (deepest - lvl_inv) <= 0:  # broke 78.6% -> leg invalid
                    used.add(key)
                    continue
                if sg * (deepest - lvl1) > 0 or sg * (deepest - lvl2) < 0:  # not (or no longer) in the zone
                    continue
                prev_ext = h[i - 1] if sg > 0 else lo[i - 1]
                if sg * (c[i] - o[i]) <= 0 or sg * (c[i] - prev_ext) <= 0 or sg * (extreme - c[i]) <= 0:
                    continue
                entry = float(c[i])
                stop = clamp_stop(
                    side, entry, lvl_inv - sg * p["stop_buffer_atr"] * a[i], a[i], p["min_stop_atr"]
                )
                t2 = origin + sg * ext_k * leg
                tg = [extreme, t2]
                depth = abs(extreme - deepest) / leg
                word = "up" if sg > 0 else "down"
                reasons = [
                    (
                        f"Impulse leg {word} {fmt(origin)} → {fmt(extreme)} ({fmt(leg)} pts ="
                        f" {leg / a[i]:.1f}× ATR, needs ≥ {p['min_leg_atr']})"
                    ),
                    (
                        f"Retraced {depth * 100:.1f}% to {fmt(deepest)} — inside the"
                        f" {z1 * 100:.1f}% ({fmt(lvl1)})"
                        f" to {z2 * 100:.1f}% ({fmt(lvl2)}) zone, 78.6% ({fmt(lvl_inv)}) intact"
                    ),
                    (
                        f"Reversal close {fmt(entry)} {'above' if sg > 0 else 'below'} the previous bar's"
                        f" {'high' if sg > 0 else 'low'} {fmt(prev_ext)}"
                    ),
                    (
                        f"Stop {fmt(stop)} beyond 78.6%, risk {fmt(abs(entry - stop))} pts; targets"
                        f" {fmt(tg[0])}"
                        f" (swing extreme) and {fmt(tg[1])} ({ext_k} extension)"
                    ),
                ]
                out.append(self._signal(bv, i, underlying, side, entry, stop, tg, reasons))
                used.add(key)
                count += 1
        return out


# ---------------------------------------------------------------------------------------- supertrend_pullback
LEARN_ST_PB = """\
### Supertrend Pullback

**What it is.** Supertrend(10, 3) defines the trend and a volatility-adjusted trailing line. Instead of
buying the flip (late, wide stop), we wait until price **pulls back toward the Supertrend line** (within
0.6×ATR of it) while the trend is intact, then buy when a candle closes back up, away from the line. Mirror
for shorts.

**Why it can work.** The line sits where the trend would be proven wrong, so entering near it gives a small,
well-defined risk; the trend filter (Supertrend direction held ≥ 6 bars, price on the right side of EMA 50)
keeps us out of fresh, unconfirmed flips.

**When it fails.** When the pullback becomes a reversal and closes through the line (stop hit), and on
choppy days where Supertrend flips every few bars.

**Rules (defaults).**
- Trend: Supertrend(10,3) direction up for ≥ 6 bars and close > EMA 50.
- Pullback: within the last 3 bars the low came within 0.6×ATR(14) of the Supertrend line.
- Resume: bullish candle closing above the previous close and at least 0.3×ATR above the line.
- Stop: Supertrend line − 0.1×ATR (at least 0.3×ATR). Targets 1R (partial) and 2R.
- Entries 09:45–14:45, max 2 per day, exit 15:15.

**Risk.** Supertrend lags; after a long move the line is far away and the pullback rule rarely triggers.
"""


class SupertrendPullback(Strategy):
    id = "supertrend_pullback"
    name = "Supertrend Pullback"
    description = "Supertrend(10,3) trend; enter when price pulls back near the ST line and resumes."
    regimes: ClassVar[set[Regime] | None] = {Regime.TREND_UP, Regime.TREND_DOWN}
    required_columns = ("open", "high", "low", "close", "atr14", "supertrend", "st_dir", "ema50")
    default_params: ClassVar[dict[str, Any]] = {
        "min_trend_bars": 6,
        "near_atr": 0.6,
        "pullback_bars": 3,
        "min_clear_atr": 0.3,
        "ema50_filter": True,
        "stop_buffer_atr": 0.1,
        "min_stop_atr": 0.3,
        "targets_r": [1.0, 2.0],
        "entry_start": "09:45",
        "entry_end": "14:45",
        "max_per_day": 2,
    }
    param_grid: ClassVar[dict[str, list[Any]] | None] = {"near_atr": [0.4, 0.6, 0.8]}
    learn = LEARN_ST_PB

    def _scan(
        self, feat: pd.DataFrame, underlying: str, extra: dict[str, Any] | None
    ) -> list[StrategySignal]:
        p = self.params
        bv = BarView(feat)
        o, h, lo, c, a = bv["open"], bv["high"], bv["low"], bv["close"], bv["atr14"]
        st, sd, e50 = bv["supertrend"], bv["st_dir"], bv["ema50"]
        start, end = hhmm_to_min(p["entry_start"]), hhmm_to_min(p["entry_end"])
        nb, mt = int(p["pullback_bars"]), int(p["min_trend_bars"])
        out: list[StrategySignal] = []
        for _d, s, e in bv.day_slices():
            count, last_i = 0, -100
            for i in range(s, e):
                if count >= int(p["max_per_day"]):
                    break
                if not (start <= bv.close_min[i] <= end) or i - last_i <= nb or i < mt or i == s:
                    continue
                if not all(np.isfinite((o[i], c[i], a[i], st[i], sd[i], e50[i], c[i - 1]))) or a[i] <= 0:
                    continue
                sg = int(sd[i])
                if sg == 0 or not np.all(sd[i - mt + 1 : i + 1] == sg):
                    continue
                side = Side.BUY if sg > 0 else Side.SELL
                if p["ema50_filter"] and sg * (c[i] - e50[i]) <= 0:
                    continue
                j0 = max(s, i - nb + 1)
                ext = float(np.min(lo[j0 : i + 1])) if sg > 0 else float(np.max(h[j0 : i + 1]))
                gap = sg * (ext - st[i])
                if gap > p["near_atr"] * a[i]:
                    continue
                clear = sg * (c[i] - st[i])
                if (
                    sg * (c[i] - o[i]) <= 0
                    or sg * (c[i] - c[i - 1]) <= 0
                    or clear < p["min_clear_atr"] * a[i]
                ):
                    continue
                entry = float(c[i])
                stop = clamp_stop(
                    side, entry, st[i] - sg * p["stop_buffer_atr"] * a[i], a[i], p["min_stop_atr"]
                )
                tg = r_targets(side, entry, stop, list(p["targets_r"]))
                word = "up" if sg > 0 else "down"
                reasons = [
                    f"Supertrend(10,3) {word} for ≥ {mt} bars, line at {fmt(st[i])}",
                    (
                        f"Pullback {'low' if sg > 0 else 'high'} {fmt(ext)} came within"
                        f" {gap / a[i]:.2f}× ATR of the"
                        f" line (limit {p['near_atr']}×)"
                    ),
                    (
                        f"Resumed: close {fmt(entry)} {'above' if sg > 0 else 'below'} previous"
                        f" close {fmt(c[i - 1])},"
                        f" {clear / a[i]:.2f}× ATR clear of the line"
                    ),
                    (
                        f"Stop {fmt(stop)} beyond Supertrend, risk {fmt(abs(entry - stop))} pts; targets"
                        f" {', '.join(fmt(t) for t in tg)}"
                    ),
                ]
                if p["ema50_filter"]:
                    reasons.insert(1, f"Close {'>' if sg > 0 else '<'} EMA 50 {fmt(e50[i])}")
                out.append(self._signal(bv, i, underlying, side, entry, stop, tg, reasons))
                count += 1
                last_i = i
        return out
