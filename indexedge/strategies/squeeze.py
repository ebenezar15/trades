"""``squeeze_breakout``: TTM-style volatility squeeze (Bollinger inside Keltner) breakout on 15-min bars."""
from __future__ import annotations

from typing import Any, ClassVar

import numpy as np
import pandas as pd

from indexedge.core.types import Side, StrategySignal
from indexedge.features.indicators import atr, consecutive_true, squeeze, squeeze_momentum
from indexedge.strategies.base import BarView, Strategy, fmt, hhmm_to_min, htf_bars, r_targets

LEARN = """\
### Volatility Squeeze Breakout (TTM squeeze)

**What it is.** When Bollinger Bands(20, 2) contract fully *inside* the Keltner Channel(20, 1.5×ATR),
volatility is unusually compressed ("squeeze ON"). The first bar after a squeeze of at least 6 bars
("fire") is traded in the direction of the momentum histogram.

**Why it can work.** Volatility clusters and mean-reverts: quiet periods tend to be followed by expansion. The
momentum histogram (linear regression of price versus its 20-bar mid-range) tells which way the expansion is
leaning.

**When it fails.** False fires near the open, fires into major levels, and squeezes that resolve sideways.

**Rules (defaults).**
- 15-min bars (5-min optional via ``htf_minutes=5``). Squeeze ON for ≥ 6 consecutive bars, then the first OFF bar.
- Long if histogram > 0 and rising; short if < 0 and falling. Ignore fires before 09:45.
- Stop = tighter of the squeeze-period range's opposite side and 1.5×ATR(14). Target 3R. Exit by 15:15.

**Risk / backtest note.** The live rule also exits after 2 consecutive bars of fading histogram; the backtest
uses only stop, 3R target and the time exit.
"""


class SqueezeBreakout(Strategy):
    id = "squeeze_breakout"
    name = "Volatility Squeeze Breakout"
    description = "Trade the first bar after a ≥6-bar BB-inside-KC squeeze in the momentum direction."
    timeframe = "15m"
    default_params: ClassVar[dict[str, Any]] = {
        "htf_minutes": 15,
        "min_squeeze_bars": 6,
        "atr_stop": 1.5,
        "target_r": 3.0,
        "entry_start": "09:45",
        "entry_end": "14:45",
    }
    learn = LEARN

    def _scan(self, feat: pd.DataFrame, underlying: str, extra: dict[str, Any] | None) -> list[StrategySignal]:
        p = self.params
        bv = BarView(feat)
        hm = int(p["htf_minutes"])
        htf, pos = htf_bars(bv, hm)
        if len(htf) < 30:
            self.last_skip_reason = "not enough higher-timeframe bars for the squeeze"
            return []
        sq = squeeze(htf).to_numpy()
        run = consecutive_true(squeeze(htf)).to_numpy()
        mom = squeeze_momentum(htf).to_numpy()
        a = atr(htf, 14).to_numpy()
        hh, ll = htf["high"].to_numpy(), htf["low"].to_numpy()
        start, end = hhmm_to_min(p["entry_start"]), hhmm_to_min(p["entry_end"])
        nmin = int(p["min_squeeze_bars"])
        out: list[StrategySignal] = []
        for j in range(1, len(htf)):
            i = pos[j]
            if i < 0 or sq[j] or run[j - 1] < nmin:
                continue
            if not (start <= bv.close_min[i] <= end):
                continue
            if not all(np.isfinite((mom[j], mom[j - 1], a[j]))):
                continue
            if mom[j] > 0 and mom[j] > mom[j - 1]:
                side = Side.BUY
            elif mom[j] < 0 and mom[j] < mom[j - 1]:
                side = Side.SELL
            else:
                continue
            k = int(run[j - 1])
            box_hi, box_lo = float(np.max(hh[j - k:j])), float(np.min(ll[j - k:j]))
            entry = float(bv["close"][i])
            st_box = box_lo if side is Side.BUY else box_hi
            st_atr = entry - side.sign * p["atr_stop"] * a[j]
            stop = max(st_box, st_atr) if side is Side.BUY else min(st_box, st_atr)
            if side.sign * (entry - stop) <= 0:
                stop = st_atr
            tg = r_targets(side, entry, stop, [p["target_r"]])
            trend = "rising" if side is Side.BUY else "falling"
            reasons = [
                (f"Bollinger(20,2) was inside Keltner(20,1.5) for {k} {hm}-min bars (needs ≥ {nmin});"
                " the squeeze fired on this bar"),
                f"Momentum histogram {mom[j]:+.1f} and {trend} (prev {mom[j - 1]:+.1f})",
                f"Squeeze range {fmt(box_lo)}–{fmt(box_hi)}; stop {fmt(stop)}, ATR({hm}m) {fmt(a[j])}",
                f"Target {fmt(tg[0])} ({p['target_r']}R)",
            ]
            out.append(self._signal(bv, i, underlying, side, entry, stop, tg, reasons))
        return out
