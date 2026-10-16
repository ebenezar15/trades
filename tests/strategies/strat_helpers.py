"""Test helpers for strategy / backtest tests (synthetic PCR path, hand-made sessions)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

from indexedge.core.clock import IST


def pcr_series(index: pd.DatetimeIndex) -> pd.Series:
    """Deterministic PCR path cycling through bullish / bearish / neutral regimes (test-only)."""
    k = np.arange(len(index)) // 40 % 3
    return pd.Series(np.choose(k, [1.4, 0.6, 1.0]).astype(float), index=index)


def extra_for(sid: str, index: pd.DatetimeIndex) -> dict | None:
    return {"pcr_series": pcr_series(index), "walls": {"resistance": [], "support": []}} if sid == "oi_pcr" \
        else None


def make_session(d: date, closes: list[float], highs=None, lows=None, opens=None, volumes=None,
                 step: int = 5) -> pd.DataFrame:
    """One session of bars starting 09:15 from explicit values (missing OHLC derived from closes)."""
    n = len(closes)
    c = np.asarray(closes, float)
    o = np.asarray(opens, float) if opens is not None else np.r_[c[0], c[:-1]]
    h = np.asarray(highs, float) if highs is not None else np.maximum(o, c) + 1
    lo = np.asarray(lows, float) if lows is not None else np.minimum(o, c) - 1
    v = np.asarray(volumes, float) if volumes is not None else np.full(n, 1000.0)
    t0 = datetime.combine(d, time(9, 15), IST)
    idx = pd.DatetimeIndex([t0 + timedelta(minutes=step * k) for k in range(n)])
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": v}, index=idx)
