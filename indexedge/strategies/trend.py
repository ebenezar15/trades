"""Trend-following strategies: ``ema_supertrend_adx`` and ``triple_supertrend``."""
from __future__ import annotations

from typing import Any, ClassVar

import numpy as np
import pandas as pd

from indexedge.core.types import Regime, Side, StrategySignal
from indexedge.strategies.base import BarView, Strategy, fmt, hhmm_to_min, r_targets

LEARN_EMA = """\
### EMA 9/21 + Supertrend(10, 3) + ADX filter

**What it is.** A classic trend-follower: the fast EMA(9) is above the slow EMA(21), price closes above the
Supertrend(10, 3) line, and ADX(14) confirms there is a trend at all (ADX > 22 with +DI > −DI). The first bar
on which *all* conditions hold triggers the entry (mirror for shorts).

**Why it can work.** Each filter removes a different kind of noise: EMAs give direction, Supertrend gives a
volatility-adjusted trailing level, ADX removes flat markets where crossovers whipsaw.

**When it fails.** Sideways sessions (ADX hovering around the threshold) and sharp V-reversals. Late entries
after an extended move have poor reward-to-risk.

**Rules (defaults).** EMA 9 vs 21 state, close beyond Supertrend(10,3), ADX(14) > 22 with DI agreement,
optional EMA(200) bias filter (on). Stop = Supertrend line (at least 0.3×ATR away). Target 2.5R.
Window 09:30–14:45, exit by 15:15.

**Risk / backtest note.** The live rule also exits on a Supertrend flip; the backtest uses only stop, target
and time exit, which is slightly pessimistic on reversals.
"""

LEARN_TRIPLE = """\
### Triple Supertrend

**What it is.** Three Supertrends with increasing sensitivity — (10, 1), (11, 2) and (12, 3). A trade is taken
on the **first** closed bar on which all three agree on direction, with ADX(14) > 20.

**Why it can work.** Agreement of fast and slow Supertrends filters out minor wiggles while still reacting
quickly when a trend starts.

**When it fails.** Choppy days produce frequent agreement/disagreement flips; the ADX filter reduces but does
not remove these.

**Rules (defaults).** Enter on the first bar of agreement (09:30–14:30). Stop = ST(12, 3) line (at least
0.3×ATR away). Target 2R. Time exit 15:00.

**Risk / backtest note.** The live rule exits when any one Supertrend flips; the backtest approximates this with
the 2R target, the stop and the 15:00 time exit.
"""


def _min_stop(side: Side, entry: float, stop: float, atr: float, k: float) -> float:
    m = entry - side.sign * k * atr
    return min(stop, m) if side is Side.BUY else max(stop, m)


class EmaSupertrendAdx(Strategy):
    id = "ema_supertrend_adx"
    name = "EMA + Supertrend + ADX"
    description = "EMA 9/21 trend with a Supertrend(10,3) close and ADX(14) > 22 confirmation."
    regimes: ClassVar[set[Regime] | None] = {Regime.TREND_UP, Regime.TREND_DOWN}
    required_columns = ("open", "high", "low", "close", "atr14", "ema9", "ema21", "ema200", "supertrend",
                        "st_dir", "adx14", "plus_di", "minus_di")
    default_params: ClassVar[dict[str, Any]] = {
        "adx_min": 22.0,
        "ema200_filter": True,
        "target_r": 2.5,
        "min_stop_atr": 0.3,
        "entry_start": "09:30",
        "entry_end": "14:45",
    }
    param_grid: ClassVar[dict[str, list[Any]] | None] = {"adx_min": [20.0, 22.0, 25.0]}
    learn = LEARN_EMA

    def _scan(self, feat: pd.DataFrame, underlying: str, extra: dict[str, Any] | None) -> list[StrategySignal]:
        p = self.params
        bv = BarView(feat)
        c, a = bv["close"], bv["atr14"]
        e9, e21, e200, st, sd = bv["ema9"], bv["ema21"], bv["ema200"], bv["supertrend"], bv["st_dir"]
        ad, pdi, mdi = bv["adx14"], bv["plus_di"], bv["minus_di"]
        start, end = hhmm_to_min(p["entry_start"]), hhmm_to_min(p["entry_end"])

        def cond(i: int, sg: int) -> bool:
            vals = (c[i], a[i], e9[i], e21[i], st[i], sd[i], ad[i], pdi[i], mdi[i])
            if not all(np.isfinite(vals)):
                return False
            if p["ema200_filter"] and not (np.isfinite(e200[i]) and sg * (c[i] - e200[i]) > 0):
                return False
            return (sg * (e9[i] - e21[i]) > 0 and sd[i] == sg and sg * (c[i] - st[i]) > 0
                    and ad[i] > p["adx_min"] and sg * (pdi[i] - mdi[i]) > 0)

        out: list[StrategySignal] = []
        for i in range(1, bv.n):
            if not (start <= bv.close_min[i] <= end):
                continue
            for side in (Side.BUY, Side.SELL):
                sg = side.sign
                if not cond(i, sg) or (bv.dates[i - 1] == bv.dates[i] and cond(i - 1, sg)):
                    continue
                entry = float(c[i])
                stop = _min_stop(side, entry, float(st[i]), a[i], p["min_stop_atr"])
                tg = r_targets(side, entry, stop, [p["target_r"]])
                rel = ">" if sg > 0 else "<"
                reasons = [
                    f"EMA 9 {fmt(e9[i])} {rel} EMA 21 {fmt(e21[i])}",
                    f"Close {fmt(entry)} {'above' if sg > 0 else 'below'} Supertrend(10,3) {fmt(st[i])}",
                    f"ADX {ad[i]:.1f} > {p['adx_min']} with +DI {pdi[i]:.1f} vs −DI {mdi[i]:.1f}",
                ]
                if p["ema200_filter"]:
                    reasons.append(f"Close {rel} EMA 200 {fmt(e200[i])} (bias filter)")
                reasons.append(f"Stop {fmt(stop)} (Supertrend), target {fmt(tg[0])} ({p['target_r']}R)")
                out.append(self._signal(bv, i, underlying, side, entry, stop, tg, reasons))
        return out


class TripleSupertrend(Strategy):
    id = "triple_supertrend"
    name = "Triple Supertrend"
    description = "Enter when Supertrends (10,1), (11,2), (12,3) first agree, with ADX(14) > 20."
    regimes: ClassVar[set[Regime] | None] = {Regime.TREND_UP, Regime.TREND_DOWN}
    time_exit = "15:00"
    required_columns = ("open", "high", "low", "close", "atr14", "st_agree", "st_12_3", "adx14")
    default_params: ClassVar[dict[str, Any]] = {
        "adx_min": 20.0,
        "target_r": 2.0,
        "min_stop_atr": 0.3,
        "entry_start": "09:30",
        "entry_end": "14:30",
    }
    learn = LEARN_TRIPLE

    def _scan(self, feat: pd.DataFrame, underlying: str, extra: dict[str, Any] | None) -> list[StrategySignal]:
        p = self.params
        bv = BarView(feat)
        c, a, ag, st, ad = bv["close"], bv["atr14"], bv["st_agree"], bv["st_12_3"], bv["adx14"]
        start, end = hhmm_to_min(p["entry_start"]), hhmm_to_min(p["entry_end"])
        out: list[StrategySignal] = []
        for i in range(1, bv.n):
            if not (start <= bv.close_min[i] <= end):
                continue
            if not all(np.isfinite((c[i], a[i], ag[i], st[i], ad[i]))) or ag[i] == 0:
                continue
            if ag[i - 1] == ag[i] or ad[i] <= p["adx_min"]:
                continue
            side = Side.BUY if ag[i] > 0 else Side.SELL
            entry = float(c[i])
            if side.sign * (entry - st[i]) <= 0:
                continue
            stop = _min_stop(side, entry, float(st[i]), a[i], p["min_stop_atr"])
            tg = r_targets(side, entry, stop, [p["target_r"]])
            word = "up" if side is Side.BUY else "down"
            reasons = [
                f"All three Supertrends (10,1), (11,2), (12,3) turned {word} together on this bar",
                f"ADX {ad[i]:.1f} > {p['adx_min']}",
                (f"Stop {fmt(stop)} at the ST(12,3) line, risk {fmt(abs(entry - stop))} pts;"
                f" target {fmt(tg[0])} ({p['target_r']}R), time exit {self.time_exit}"),
            ]
            out.append(self._signal(bv, i, underlying, side, entry, stop, tg, reasons))
        return out
