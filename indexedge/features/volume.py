"""Volume analytics: volume profile (POC / value area) and anchored VWAP."""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from indexedge.features.indicators import ist_index


def volume_profile(df: pd.DataFrame, bins: int = 50, value_area: float = 0.70) -> dict:
    """Volume-by-price over ``df``. Each bar's volume is spread uniformly over its [low, high] range.

    Returns ``dict(poc, vah, val)`` (bin-centre prices). Bars are equally weighted when there is no volume
    (e.g. spot index data). NaNs for empty input.
    """
    nan = {"poc": float("nan"), "vah": float("nan"), "val": float("nan")}
    if df is None or df.empty:
        return nan
    lo_all = float(df["low"].min())
    hi_all = float(df["high"].max())
    if not np.isfinite(lo_all) or not np.isfinite(hi_all):
        return nan
    vol = df["volume"].astype(float).fillna(0.0).to_numpy() if "volume" in df else np.zeros(len(df))
    if vol.sum() <= 0:
        vol = np.ones(len(df))
    if hi_all <= lo_all:
        return {"poc": hi_all, "vah": hi_all, "val": hi_all}
    edges = np.linspace(lo_all, hi_all, bins + 1)
    width = edges[1] - edges[0]
    hist = np.zeros(bins)
    lows = df["low"].to_numpy(dtype=float)
    highs = df["high"].to_numpy(dtype=float)
    for lo, hi, v in zip(lows, highs, vol):
        if v <= 0 or not (np.isfinite(lo) and np.isfinite(hi)):
            continue
        if hi <= lo:
            k = min(int((lo - lo_all) / width), bins - 1)
            hist[k] += v
            continue
        overlap = np.clip(np.minimum(edges[1:], hi) - np.maximum(edges[:-1], lo), 0, None)
        hist += v * overlap / (hi - lo)
    centres = (edges[:-1] + edges[1:]) / 2.0
    poc_i = int(np.argmax(hist))
    total = hist.sum()
    lo_i = hi_i = poc_i
    acc = hist[poc_i]
    while acc < value_area * total and (lo_i > 0 or hi_i < bins - 1):
        up = hist[hi_i + 1] if hi_i < bins - 1 else -1.0
        dn = hist[lo_i - 1] if lo_i > 0 else -1.0
        if up >= dn:
            hi_i += 1
            acc += up
        else:
            lo_i -= 1
            acc += dn
    return {"poc": float(centres[poc_i]), "vah": float(centres[hi_i]), "val": float(centres[lo_i])}


def anchored_vwap(df: pd.DataFrame, anchor_ts: datetime | pd.Timestamp) -> pd.Series:
    """VWAP of typical price accumulated from the first bar at/after ``anchor_ts`` (NaN before).

    Equal weights while no volume has accumulated.
    """
    if df.empty:
        return pd.Series(dtype=float, index=df.index)
    idx = ist_index(df.index)
    ts = pd.Timestamp(anchor_ts)
    ts = ts.tz_localize(idx.tz) if ts.tzinfo is None else ts.tz_convert(idx.tz)
    mask = pd.Series(np.asarray(idx >= ts), index=df.index)
    tp = (df["high"].astype(float) + df["low"].astype(float) + df["close"].astype(float)) / 3.0
    vol = df["volume"].astype(float).fillna(0.0).clip(lower=0).where(mask, 0.0)
    cumv = vol.cumsum()
    cnt = mask.astype(float).cumsum()
    vw_v = (vol * tp).cumsum() / cumv.where(cumv > 0)
    vw_e = tp.where(mask, 0.0).cumsum() / cnt.where(cnt > 0)
    return vw_v.where(cumv > 0, vw_e).where(mask)
