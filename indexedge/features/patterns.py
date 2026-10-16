"""Single/two-bar candlestick patterns (0/1 flags, causal: bar i uses bars i and i-1 only, NR7 uses 7)."""
from __future__ import annotations

import pandas as pd

PATTERN_COLUMNS = ["bull_engulf", "bear_engulf", "pin_bull", "pin_bear", "inside_bar", "nr7"]


def detect_patterns(df: pd.DataFrame, wick_body: float = 2.0, wick_range: float = 0.6) -> pd.DataFrame:
    """Flags: bullish/bearish engulfing (body engulfs previous opposite-colour body), pin bars (dominant
    wick ≥ ``wick_body``×body and ≥ ``wick_range``×range), inside bar (strictly inside previous bar),
    NR7 (narrowest range of the last 7 bars)."""
    if df.empty:
        return pd.DataFrame({c: pd.Series(dtype=int) for c in PATTERN_COLUMNS}, index=df.index)
    o, h = df["open"].astype(float), df["high"].astype(float)
    lo, c = df["low"].astype(float), df["close"].astype(float)
    po, pc, ph, pl = o.shift(1), c.shift(1), h.shift(1), lo.shift(1)
    body = (c - o).abs()
    rng = h - lo
    upper = h - pd.concat([o, c], axis=1).max(axis=1)
    lower = pd.concat([o, c], axis=1).min(axis=1) - lo
    prev_body = (pc - po).abs()
    bull = (pc < po) & (c > o) & (c >= po) & (o <= pc) & (body > prev_body)
    bear = (pc > po) & (c < o) & (c <= po) & (o >= pc) & (body > prev_body)
    pin_bull = (rng > 0) & (lower >= wick_body * body) & (lower >= wick_range * rng)
    pin_bear = (rng > 0) & (upper >= wick_body * body) & (upper >= wick_range * rng)
    inside = (h < ph) & (lo > pl)
    nr7 = (rng <= rng.rolling(7, min_periods=7).min()) & rng.rolling(7, min_periods=7).min().notna()
    out = pd.DataFrame({"bull_engulf": bull, "bear_engulf": bear, "pin_bull": pin_bull, "pin_bear": pin_bear,
                        "inside_bar": inside, "nr7": nr7}, index=df.index)
    return out.fillna(False).astype(int)
