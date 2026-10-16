"""``bollinger_reversion``: range-day mean reversion on Bollinger Bands(20, 2).

Close outside a band, re-entry close inside, target the middle band.
"""

from __future__ import annotations

from typing import Any, ClassVar

import numpy as np
import pandas as pd

from indexedge.core.types import Regime, Side, StrategySignal
from indexedge.strategies.base import BarView, Strategy, fmt, hhmm_to_min
from indexedge.strategies.pullbacks import clamp_stop

LEARN = """\
### Bollinger Band Reversion (range days only)

**What it is.** Bollinger Bands(20, 2) wrap a 20-bar average with ±2 standard deviations. On a **range**
day, a close outside a band followed by a close back **inside** it often marks an exhausted push; we fade it
and target the middle band (the 20-bar average).

**Why it can work.** In a sideways market price oscillates around its average; moves to ±2σ tend to revert.
Waiting for the re-entry close (rather than fading the first touch) avoids standing in front of a breakout.

**When it fails.** Trend days and breakouts — a band "walk" keeps closing outside. That is why IndexEdge only
trades it when its regime classifier says **range** (ADX below ~22, flat EMA 50) on the signal bar.

**Rules (defaults).**
- Regime = range on the signal bar.
- Excursion: one of the previous 3 bars closed below the lower band (above the upper band for shorts).
- Trigger: this bar closes back inside the band with a bullish candle (bearish for shorts).
- Stop: the excursion's extreme ± 0.1×ATR(14) (at least 0.3×ATR).
- Target: the middle band; skip if it is less than 0.8R away. Entries 09:45–14:45, max 2 per day,
  exit 15:15.

**Risk.** Reward is often only ~1R, so the win rate must stay high; a regime change mid-trade is the main
loss source.
"""


class BollingerReversion(Strategy):
    id = "bollinger_reversion"
    name = "Bollinger Reversion"
    description = "Range regime only: close outside BB(20,2), re-entry close inside, target the middle band."
    regimes: ClassVar[set[Regime] | None] = {Regime.RANGE}
    required_columns = ("open", "high", "low", "close", "atr14", "bb_upper", "bb_lower", "bb_mid", "regime")
    default_params: ClassVar[dict[str, Any]] = {
        "lookback_bars": 3,
        "stop_buffer_atr": 0.1,
        "min_stop_atr": 0.3,
        "min_reward_r": 0.8,
        "allowed_regimes": ["range"],
        "entry_start": "09:45",
        "entry_end": "14:45",
        "max_per_day": 2,
    }
    param_grid: ClassVar[dict[str, list[Any]] | None] = {"min_reward_r": [0.5, 0.8, 1.0]}
    learn = LEARN

    def _scan(
        self, feat: pd.DataFrame, underlying: str, extra: dict[str, Any] | None
    ) -> list[StrategySignal]:
        p = self.params
        bv = BarView(feat)
        o, h, lo, c, a = bv["open"], bv["high"], bv["low"], bv["close"], bv["atr14"]
        up, dn, mid = bv["bb_upper"], bv["bb_lower"], bv["bb_mid"]
        reg = feat["regime"].astype(str).to_numpy()
        allowed = set(p["allowed_regimes"])
        start, end = hhmm_to_min(p["entry_start"]), hhmm_to_min(p["entry_end"])
        lb = int(p["lookback_bars"])
        out: list[StrategySignal] = []
        for _d, s, e in bv.day_slices():
            count, last_i = 0, -100
            for i in range(s + 1, e):
                if count >= int(p["max_per_day"]):
                    break
                if not (start <= bv.close_min[i] <= end) or reg[i] not in allowed or i - last_i <= lb:
                    continue
                if not all(np.isfinite((o[i], c[i], a[i], up[i], dn[i], mid[i]))) or a[i] <= 0:
                    continue
                j0 = max(s, i - lb)
                for side in (Side.BUY, Side.SELL):
                    sg = side.sign
                    band = dn if sg > 0 else up
                    outside = [j for j in range(j0, i) if np.isfinite(band[j]) and sg * (c[j] - band[j]) < 0]
                    if not outside or sg * (c[i] - band[i]) <= 0 or sg * (c[i] - o[i]) <= 0:
                        continue
                    k0 = outside[0]
                    extreme = float(np.min(lo[k0 : i + 1])) if sg > 0 else float(np.max(h[k0 : i + 1]))
                    entry = float(c[i])
                    stop = clamp_stop(
                        side, entry, extreme - sg * p["stop_buffer_atr"] * a[i], a[i], p["min_stop_atr"]
                    )
                    risk = abs(entry - stop)
                    reward = sg * (mid[i] - entry)
                    if risk <= 0 or reward < p["min_reward_r"] * risk:
                        continue
                    name = "lower" if sg > 0 else "upper"
                    reasons = [
                        f"Regime: {reg[i]} (mean-reversion allowed)",
                        (
                            f"Closed outside the {name} Bollinger band {bv.index[k0].strftime('%H:%M')}"
                            f" ({fmt(c[k0])} vs band {fmt(band[k0])})"
                        ),
                        f"Re-entry: close {fmt(entry)} back inside the {name} band {fmt(band[i])}",
                        (
                            f"Stop {fmt(stop)} beyond the extreme {fmt(extreme)}, risk {fmt(risk)} pts;"
                            f" target middle band {fmt(mid[i])} ({reward / risk:.1f}R)"
                        ),
                    ]
                    out.append(self._signal(bv, i, underlying, side, entry, stop, [float(mid[i])], reasons))
                    count += 1
                    last_i = i
                    break
        return out
