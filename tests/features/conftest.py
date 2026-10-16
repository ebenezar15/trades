"""Deterministic synthetic 5-minute IST candles for feature tests (independent of indexedge.data)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd
import pytest

from indexedge.core.clock import IST


def make_candles(days: int = 5, seed: int = 0, start: date = date(2026, 1, 5), interval: int = 5,
                 price: float = 24000.0, volume: bool = True, drift: float = 0.0) -> pd.DataFrame:
    """Weekday sessions 09:15..15:30 (last bar opens at 15:30 - interval), GBM closes, U-shaped volume."""
    rng = np.random.default_rng(seed)
    per_day = (6 * 60 + 15) // interval
    stamps = []
    d = start
    while len(stamps) < days * per_day:
        if d.weekday() < 5:
            t0 = datetime.combine(d, time(9, 15), IST)
            stamps += [t0 + timedelta(minutes=interval * k) for k in range(per_day)]
        d += timedelta(days=1)
    n = len(stamps)
    rets = rng.normal(drift, 0.0012, n)
    # overnight gaps on each first bar of the day
    rets[::per_day] += rng.normal(0, 0.004, days)
    close = price * np.exp(np.cumsum(rets))
    open_ = np.empty(n)
    open_[0] = price
    open_[1:] = close[:-1]
    open_[::per_day] = close[::per_day] / np.exp(rng.normal(0, 0.0008, days))
    spread = np.abs(rng.normal(0, 0.0008, n)) * close
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 0.0008, n)) * close
    k = np.tile(np.arange(per_day), days)
    u = 1.0 + 2.0 * ((k - per_day / 2) / (per_day / 2)) ** 2
    vol = (u * rng.lognormal(10, 0.3, n)).round() if volume else np.zeros(n)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": vol},
                        index=pd.DatetimeIndex(stamps))


@pytest.fixture
def candles():
    return make_candles


@pytest.fixture
def df5():
    return make_candles(days=6, seed=1)
