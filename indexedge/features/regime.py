"""Market regime classifier (ADX + EMA slope + ATR percentile), causal.

Rules per bar (first match wins):
1. warm-up (ADX/EMA/slope NaN) → ``unknown``
2. ATR% (ATR/close) rolling percentile ≥ ``hv_pct`` → ``high_vol``
3. ADX ≥ ``trend_adx``, EMA(``ema_n``) slope over ``slope_n`` bars ≥ ``min_slope_atr``·ATR, close above EMA and
   +DI > −DI → ``trend_up`` (mirror → ``trend_down``)
4. otherwise → ``range``
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from indexedge.core.types import Regime
from indexedge.features.indicators import adx, atr, ema

REGIME_VALUES = [r.value for r in Regime]


def regime_inputs(df: pd.DataFrame, adx_n: int = 14, ema_n: int = 50, slope_n: int = 5,
                  atr_window: int = 500, atr_min_periods: int = 100) -> pd.DataFrame:
    a = atr(df, adx_n)
    ad = adx(df, adx_n)
    e = ema(df["close"], ema_n)
    slope = (e - e.shift(slope_n)) / a.where(a > 0)
    atr_rel = a / df["close"].astype(float)
    rank = atr_rel.rolling(atr_window, min_periods=atr_min_periods).rank(pct=True)
    return pd.DataFrame({"adx": ad["adx"], "plus_di": ad["plus_di"], "minus_di": ad["minus_di"], "ema": e,
                         "slope_atr": slope, "atr_rank": rank}, index=df.index)


def classify_regime(df: pd.DataFrame, trend_adx: float = 22.0, min_slope_atr: float = 0.1,
                    hv_pct: float = 0.9, **kw) -> pd.Series:
    """Return a Series of :class:`indexedge.core.types.Regime` values (strings)."""
    if df.empty:
        return pd.Series(dtype=object, index=df.index)
    x = regime_inputs(df, **kw)
    c = df["close"].astype(float)
    out = np.full(len(df), Regime.RANGE.value, dtype=object)
    up = (x["adx"] >= trend_adx) & (x["slope_atr"] >= min_slope_atr) & (c > x["ema"]) & \
         (x["plus_di"] > x["minus_di"])
    dn = (x["adx"] >= trend_adx) & (x["slope_atr"] <= -min_slope_atr) & (c < x["ema"]) & \
         (x["minus_di"] > x["plus_di"])
    out[up.to_numpy()] = Regime.TREND_UP.value
    out[dn.to_numpy()] = Regime.TREND_DOWN.value
    out[(x["atr_rank"] >= hv_pct).to_numpy()] = Regime.HIGH_VOL.value
    unknown = x[["adx", "ema", "slope_atr"]].isna().any(axis=1).to_numpy()
    out[unknown] = Regime.UNKNOWN.value
    return pd.Series(out, index=df.index, name="regime")
