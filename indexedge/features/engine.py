"""Feature engine: one call that turns a candle frame into every indicator column strategies and models use.

``build_features(df, daily=None)`` returns ``df`` (IST index, sorted, de-duplicated) plus the columns below.
All columns are causal (truncation-safe). ``FEATURE_COLUMNS`` lists the numeric, scale-free columns intended
as ML inputs (distances are in ATR units, widths in percent); raw price levels are present for strategies but
excluded from ``FEATURE_COLUMNS``. The string column ``regime`` is excluded too (use the one-hot
``regime_*`` columns).

Raw columns for strategies (prices): ema9/21/50/200, supertrend (+ st_10_1, st_11_2, st_12_3 lines), vwap,
vwap_u1/l1/u2/l2, bb_mid/upper/lower, kc_mid/upper/lower, pivot, bc, tc, r1..r3, s1..s3, prev_high,
prev_low, prev_close, day_open, or_high, or_low, ha_open/high/low/close, swing_high, swing_low, obv.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from indexedge.core.clock import IST
from indexedge.core.types import CANDLE_COLUMNS, Regime
from indexedge.features import indicators as ind
from indexedge.features.patterns import PATTERN_COLUMNS, detect_patterns
from indexedge.features.regime import classify_regime

REGIME_ONEHOT = ["regime_trend_up", "regime_trend_down", "regime_range", "regime_high_vol"]

FEATURE_COLUMNS: list[str] = [
    # trend / momentum
    "rsi14", "roc10", "macd_hist_atr", "adx14", "plus_di", "minus_di", "di_diff",
    "st_dir", "st_10_1_dir", "st_11_2_dir", "st_12_3_dir", "st_agree", "dist_st_atr",
    "dist_ema9_atr", "dist_ema21_atr", "dist_ema50_atr", "dist_ema200_atr", "ema9_21_atr", "ema50_slope_atr",
    "stochrsi_k", "stochrsi_d", "rsi_div",
    # volatility
    "atr_pct", "range_atr", "body_atr", "bb_width", "bb_pctb", "squeeze", "squeeze_bars", "squeeze_mom_atr",
    # volume / vwap
    "dist_vwap_atr", "vwap_z", "obv_slope", "relvol",
    # levels
    "cpr_width_pct", "dist_pivot_atr", "dist_tc_atr", "dist_bc_atr", "dist_r1_atr", "dist_s1_atr",
    "dist_prev_high_atr", "dist_prev_low_atr", "dist_prev_close_atr", "dist_day_open_atr",
    "or_width_atr", "dist_or_high_atr", "dist_or_low_atr", "gap_pct", "day_ret_pct",
    # patterns
    *PATTERN_COLUMNS,
    # regime + time
    *REGIME_ONEHOT, "ret1_pct", "minute_of_day", "mins_from_open", "day_of_week",
]


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    x.index = ind.ist_index(x.index)
    x = x[~x.index.duplicated(keep="last")].sort_index()
    for c in CANDLE_COLUMNS:
        if c not in x:
            x[c] = 0.0 if c == "volume" else np.nan
        x[c] = x[c].astype(float)
    return x


def _daily_frame(x: pd.DataFrame, daily: pd.DataFrame | None) -> pd.DataFrame:
    own = ind.daily_ohlc(x)
    if daily is None or daily.empty:
        return own
    ext = ind._to_date_index(daily)[["open", "high", "low", "close"]].astype(float)
    return pd.concat([own[~own.index.isin(ext.index)][["open", "high", "low", "close"]], ext]).sort_index()


def _compute(x: pd.DataFrame, daily: pd.DataFrame | None) -> pd.DataFrame:
    c, h, lo, o = x["close"], x["high"], x["low"], x["open"]
    f: dict[str, pd.Series] = {}
    a = ind.atr(x, 14)
    a_safe = a.where(a > 0)
    f["atr14"] = a

    def dist(level: pd.Series) -> pd.Series:
        return (c - level) / a_safe

    for n in (9, 21, 50, 200):
        f[f"ema{n}"] = ind.ema(c, n)
    f["rsi14"] = ind.rsi(c, 14)
    f["roc10"] = ind.roc(c, 10)
    m = ind.macd(c)
    f["macd"], f["macd_signal"], f["macd_hist"] = m["macd"], m["signal"], m["hist"]
    f["macd_hist_atr"] = m["hist"] / a_safe
    ad = ind.adx(x, 14)
    f["adx14"], f["plus_di"], f["minus_di"] = ad["adx"], ad["plus_di"], ad["minus_di"]
    f["di_diff"] = ad["plus_di"] - ad["minus_di"]

    st = ind.supertrend(x, 10, 3.0)
    f["supertrend"], f["st_dir"] = st["supertrend"], st["direction"]
    dirs = []
    for n, mult, name in ((10, 1.0, "st_10_1"), (11, 2.0, "st_11_2"), (12, 3.0, "st_12_3")):
        s = ind.supertrend(x, n, mult)
        f[name], f[f"{name}_dir"] = s["supertrend"], s["direction"]
        dirs.append(s["direction"])
    tot = dirs[0] + dirs[1] + dirs[2]
    f["st_agree"] = np.sign(tot).where(tot.abs() == 3, 0.0).where(tot.notna())
    f["dist_st_atr"] = dist(st["supertrend"])
    for n in (9, 21, 50, 200):
        f[f"dist_ema{n}_atr"] = dist(f[f"ema{n}"])
    f["ema9_21_atr"] = (f["ema9"] - f["ema21"]) / a_safe
    f["ema50_slope_atr"] = (f["ema50"] - f["ema50"].shift(5)) / a_safe
    sr = ind.stoch_rsi(c)
    f["stochrsi_k"], f["stochrsi_d"] = sr["k"], sr["d"]
    f["rsi_div"] = ind.divergence(c, f["rsi14"], lookback=30, swing=3).astype(float)

    f["atr_pct"] = a / c * 100.0
    f["range_atr"] = (h - lo) / a_safe
    f["body_atr"] = (c - o) / a_safe
    bb = ind.bollinger(c, 20, 2.0)
    f["bb_mid"], f["bb_upper"], f["bb_lower"], f["bb_width"] = bb["mid"], bb["upper"], bb["lower"], bb["width"]
    bw = bb["upper"] - bb["lower"]
    f["bb_pctb"] = (c - bb["lower"]) / bw.where(bw > 0)
    kc = ind.keltner(x, 20, 1.5)
    f["kc_mid"], f["kc_upper"], f["kc_lower"] = kc["mid"], kc["upper"], kc["lower"]
    sq = ind.squeeze(x, 20, 2.0, 1.5)
    f["squeeze"] = sq.astype(float)
    f["squeeze_bars"] = ind.consecutive_true(sq)
    f["squeeze_mom"] = ind.squeeze_momentum(x, 20)
    f["squeeze_mom_atr"] = f["squeeze_mom"] / a_safe

    vw = ind.vwap(x)
    f["vwap"], f["vwap_u1"], f["vwap_l1"] = vw["vwap"], vw["upper1"], vw["lower1"]
    f["vwap_u2"], f["vwap_l2"] = vw["upper2"], vw["lower2"]
    f["dist_vwap_atr"] = dist(vw["vwap"])
    sd = vw["upper1"] - vw["vwap"]
    f["vwap_z"] = (c - vw["vwap"]) / sd.where(sd > 0)
    ob = ind.obv(x)
    f["obv"] = ob
    vsum = x["volume"].rolling(10, min_periods=10).sum()
    f["obv_slope"] = (ob - ob.shift(10)) / vsum.where(vsum > 0)
    f["relvol"] = ind.relative_volume(x, 20)

    # daily levels
    dly = _daily_frame(x, daily)
    bdates = ind.session_dates(x.index)
    pos = ind.prev_session_rows(bdates, dly)
    raw = ind.cpr_levels(dly["high"].to_numpy(), dly["low"].to_numpy(), dly["close"].to_numpy()) \
        if len(dly) else pd.DataFrame(columns=ind.CPR_COLUMNS, dtype=float)
    vals = raw[ind.CPR_COLUMNS].to_numpy()
    lv = np.full((len(x), len(ind.CPR_COLUMNS)), np.nan)
    ok = pos >= 0
    if ok.any():
        lv[ok] = vals[pos[ok]]
    for j, col in enumerate(ind.CPR_COLUMNS):
        f["cpr_width_pct" if col == "width_pct" else col] = pd.Series(lv[:, j], index=x.index)
    sl = ind.session_levels(x, 15, daily=daily)
    for col in sl.columns:
        f[col] = sl[col]
    for col in ("pivot", "tc", "bc", "r1", "s1", "prev_high", "prev_low", "prev_close", "day_open"):
        f[f"dist_{col}_atr"] = dist(f[col])
    f["or_width_atr"] = (sl["or_high"] - sl["or_low"]) / a_safe
    f["dist_or_high_atr"] = dist(sl["or_high"])
    f["dist_or_low_atr"] = dist(sl["or_low"])
    pcl = sl["prev_close"]
    f["gap_pct"] = (sl["day_open"] - pcl) / pcl * 100.0
    f["day_ret_pct"] = (c - sl["day_open"]) / sl["day_open"] * 100.0

    ha = ind.heikin_ashi(x)
    for col in ("open", "high", "low", "close"):
        f[f"ha_{col}"] = ha[col]
    sw = ind.swing_points(x, 3, 3)
    f["swing_high"], f["swing_low"] = sw["swing_high"], sw["swing_low"]

    pats = detect_patterns(x)
    for col in PATTERN_COLUMNS:
        f[col] = pats[col].astype(float)

    reg = classify_regime(x)
    f["regime"] = reg
    for name, val in zip(REGIME_ONEHOT, (Regime.TREND_UP, Regime.TREND_DOWN, Regime.RANGE, Regime.HIGH_VOL)):
        f[name] = (reg == val.value).astype(float)

    f["ret1_pct"] = c.pct_change(fill_method=None) * 100.0
    idx = x.index
    mod = pd.Series(np.asarray(idx.hour * 60 + idx.minute, dtype=float), index=idx)
    f["minute_of_day"] = mod
    f["mins_from_open"] = mod - (9 * 60 + 15)
    f["day_of_week"] = pd.Series(np.asarray(idx.dayofweek, dtype=float), index=idx)

    feats = pd.DataFrame(f, index=x.index)
    feats = feats.replace([np.inf, -np.inf], np.nan)
    return pd.concat([x, feats], axis=1)


def _empty_output(df: pd.DataFrame) -> pd.DataFrame:
    probe_idx = pd.DatetimeIndex([pd.Timestamp("2026-01-05 09:15", tz=IST)])
    probe = pd.DataFrame({c: [1.0] for c in CANDLE_COLUMNS}, index=probe_idx)
    for c in df.columns:
        if c not in probe:
            probe[c] = np.nan
    return _compute(probe, None).iloc[:0]


def build_features(df: pd.DataFrame | None, daily: pd.DataFrame | None = None) -> pd.DataFrame:
    """Return ``df`` plus all indicator / feature columns. Never raises on empty input (empty frame back).

    ``daily`` (optional, one row per session with open/high/low/close) supplies prior-session levels when the
    intraday history does not include the previous day; its rows take precedence for dates it covers. Only
    sessions strictly before each bar's date are used.
    """
    if df is None or len(df) == 0:
        return _empty_output(df if df is not None else pd.DataFrame(columns=CANDLE_COLUMNS))
    return _compute(_prepare(df), daily)
