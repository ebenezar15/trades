"""``oi_pcr``: option open-interest / put-call-ratio bias, traded on a VWAP cross (paper-only, approximate).

Option-chain data is not part of the candle history, so the strategy takes it from ``extra``:

* ``extra["pcr"]`` (float) — current PCR snapshot; applied to the **last session** in ``feat`` only
  (a snapshot says nothing about earlier days). Optional ``extra["pcr_change_30m"]``.
* ``extra["pcr_series"]`` (``pd.Series`` indexed by tz-aware timestamps) — a PCR history; each bar uses the
  latest value with timestamp ``<=`` the bar close (as-of join, causal). Used for backtests/replays.
* ``extra["walls"]`` = ``{"resistance": [strikes], "support": [strikes]}`` and optional ``extra["max_pain"]``.

Without any PCR input ``scan`` returns ``[]`` (reason: "no option-chain / OI data").
"""
from __future__ import annotations

from typing import Any, ClassVar

import numpy as np
import pandas as pd

from indexedge.core.types import Side, StrategySignal
from indexedge.strategies.base import BarView, Strategy, fmt, hhmm_to_min, r_targets

LEARN = """\
### Open Interest / PCR bias (paper-only, approximate backtest)

**What it is.** The put-call ratio (PCR = total put OI ÷ total call OI, ATM ± 10 strikes, current weekly
expiry) summarises option positioning. Read *contrarian*: PCR ≥ 1.30 means heavy put writing/hedging
(oversold → long bias); PCR ≤ 0.70 means the opposite (short bias). A fast PCR rise (≥ +0.15 in 30 min) is a
momentum-style bullish signal. The strikes with the largest call and put OI act as resistance / support
("OI walls").

**Why it can work.** Option writers are often well-capitalised; their positioning marks levels they defend.

**When it fails.** PCR can be read contrarian *or* momentum and the two conflict; OI shifts quickly on event
days; and IndexEdge has no long intraday OI history yet, so this strategy **cannot be properly backtested**.

**Rules (defaults).** A trade only when the OI bias agrees with a 5-min close crossing VWAP in the same
direction. Stop 0.6×ATR(14) beyond VWAP. Target = nearest OI wall in the trade direction (else 2R). Window
09:30–14:30, max 1 trade per day.

**Status.** **Paper-only** until at least one year of IndexEdge's own intraday OI history exists.
"""


class OiPcr(Strategy):
    id = "oi_pcr"
    name = "OI / PCR Bias"
    description = (
        "Contrarian PCR extremes (or fast PCR shifts) confirmed by a VWAP cross; OI walls as"
        " targets."
    )
    instrument_hint = "option"
    backtest_quality = "approximate"
    required_columns = ("open", "high", "low", "close", "atr14", "vwap")
    default_params: ClassVar[dict[str, Any]] = {
        "pcr_bull": 1.30,
        "pcr_bear": 0.70,
        "pcr_shift": 0.15,
        "stop_atr": 0.6,
        "target_r": 2.0,
        "entry_start": "09:30",
        "entry_end": "14:30",
    }
    learn = LEARN

    def _pcr_per_bar(self, bv: BarView, extra: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
        pcr = np.full(bv.n, np.nan)
        dpcr = np.full(bv.n, np.nan)
        series = extra.get("pcr_series")
        if isinstance(series, pd.Series) and len(series):
            ser = series.dropna().sort_index()
            close_ts = bv.index + pd.Timedelta(minutes=bv.step)
            pcr = ser.reindex(ser.index.union(close_ts)).ffill().reindex(close_ts).to_numpy(dtype=float)
            past = ser.reindex(ser.index.union(close_ts - pd.Timedelta(minutes=30))).ffill() \
                .reindex(close_ts - pd.Timedelta(minutes=30)).to_numpy(dtype=float)
            dpcr = pcr - past
        elif extra.get("pcr") is not None:
            last_day = bv.dates[-1]
            mask = bv.dates == last_day
            pcr[mask] = float(extra["pcr"])
            if extra.get("pcr_change_30m") is not None:
                dpcr[mask] = float(extra["pcr_change_30m"])
        return pcr, dpcr

    def _scan(
        self, feat: pd.DataFrame, underlying: str, extra: dict[str, Any] | None
    ) -> list[StrategySignal]:
        if not extra or (extra.get("pcr") is None and extra.get("pcr_series") is None):
            self.last_skip_reason = "no option-chain / OI data (PCR) supplied"
            return []
        p = self.params
        bv = BarView(feat)
        c, a, vw = bv["close"], bv["atr14"], bv["vwap"]
        pcr, dpcr = self._pcr_per_bar(bv, extra)
        walls = extra.get("walls") or {}
        start, end = hhmm_to_min(p["entry_start"]), hhmm_to_min(p["entry_end"])
        out: list[StrategySignal] = []
        for _d, s, e in bv.day_slices():
            for i in range(s + 1, e):
                if not (start <= bv.close_min[i] <= end):
                    continue
                if not all(np.isfinite((pcr[i], vw[i], vw[i - 1], a[i]))):
                    continue
                bias, why = None, ""
                if pcr[i] >= p["pcr_bull"]:
                    bias = Side.BUY
                    why = f"PCR {pcr[i]:.2f} ≥ {p['pcr_bull']} (contrarian: puts crowded → long bias)"
                elif pcr[i] <= p["pcr_bear"]:
                    bias = Side.SELL
                    why = f"PCR {pcr[i]:.2f} ≤ {p['pcr_bear']} (contrarian: calls crowded → short bias)"
                elif np.isfinite(dpcr[i]) and abs(dpcr[i]) >= p["pcr_shift"]:
                    bias = Side.BUY if dpcr[i] > 0 else Side.SELL
                    why = f"PCR moved {dpcr[i]:+.2f} in 30 min (momentum mode)"
                if bias is None:
                    continue
                sg = bias.sign
                if not (sg * (c[i] - vw[i]) > 0 and sg * (c[i - 1] - vw[i - 1]) <= 0):
                    continue
                entry = float(c[i])
                stop = vw[i] - sg * p["stop_atr"] * a[i]
                key = "resistance" if bias is Side.BUY else "support"
                cands = [float(w) for w in walls.get(key, []) if sg * (float(w) - entry) > 0]
                if cands:
                    tgt = min(cands, key=lambda w: abs(w - entry))
                    tdesc = f"nearest OI {key} wall {fmt(tgt, 0)}"
                else:
                    tgt = r_targets(bias, entry, stop, [p["target_r"]])[0]
                    tdesc = f"{p['target_r']}R (no OI wall beyond entry)"
                reasons = [
                    why,
                    f"5-min close {fmt(entry)} crossed {'above' if sg > 0 else 'below'} VWAP {fmt(vw[i])}",
                    f"Stop {fmt(stop)} ({p['stop_atr']}×ATR beyond VWAP); target {tdesc}",
                    "Paper-only: OI history is too short for a reliable backtest",
                ]
                if extra.get("max_pain") is not None:
                    reasons.append(f"Max pain {fmt(float(extra['max_pain']), 0)}")
                out.append(self._signal(bv, i, underlying, bias, entry, stop, [tgt], reasons))
                break
        return out
