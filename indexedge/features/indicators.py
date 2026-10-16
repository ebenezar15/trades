"""Technical indicators on IST candle DataFrames (pure, causal functions; pandas/numpy only).

Conventions
-----------
* ``df`` is a candle frame: tz-aware DatetimeIndex (bar *open* time) + ``open, high, low, close, volume``.
* Every function is causal: the value at bar *i* depends only on bars ``<= i``. Indicators that are
  conventionally seeded (EMA, Wilder RMA) are seeded with the simple mean of the first ``n`` valid values,
  matching TradingView / StockCharts, so values are identical whether computed on ``df[:i+1]`` or ``df``.
* Warm-up periods are NaN.
* Sessions are IST calendar days.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd

from indexedge.core.clock import IST, MARKET_OPEN

# ----------------------------------------------------------------------------- helpers


def ist_index(index: pd.Index) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(index)
    if idx.tz is None:
        return idx.tz_localize(IST)
    return idx.tz_convert(IST)


def session_dates(index: pd.Index) -> np.ndarray:
    """IST calendar date of every bar (object array of ``datetime.date``)."""
    return np.asarray(ist_index(index).date)


def infer_interval_minutes(index: pd.Index, default: int = 5) -> int:
    """Most common intraday bar spacing in minutes (``default`` when it cannot be inferred)."""
    idx = ist_index(index)
    if len(idx) < 2:
        return default
    d = session_dates(idx)
    diffs = np.diff(idx.values).astype("timedelta64[s]").astype(float) / 60.0
    same_day = d[1:] == d[:-1]
    diffs = diffs[same_day & (diffs > 0)]
    if diffs.size == 0:
        return default
    vals, counts = np.unique(np.round(diffs).astype(int), return_counts=True)
    return int(vals[np.argmax(counts)])


def _seeded_recursive(values: np.ndarray, n: int, alpha: float) -> np.ndarray:
    """Exponential smoothing seeded by the mean of the first ``n`` valid values.

    NaN inputs produce NaN outputs without resetting the state.
    """
    out = np.full(len(values), np.nan)
    acc = 0.0
    count = 0
    y = np.nan
    seeded = False
    for i, x in enumerate(values):
        if math.isnan(x):
            continue
        if not seeded:
            acc += x
            count += 1
            if count == n:
                y = acc / n
                seeded = True
                out[i] = y
        else:
            y = y + alpha * (x - y)
            out[i] = y
    return out


def wilder(s: pd.Series, n: int) -> pd.Series:
    """Wilder's smoothing (RMA), SMA-seeded."""
    return pd.Series(_seeded_recursive(s.to_numpy(dtype=float), n, 1.0 / n), index=s.index)


def _empty_frame(index: pd.Index, cols: list[str]) -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype=float) for c in cols}, index=index[:0]).reindex(index)


# ----------------------------------------------------------------------------- moving averages


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.astype(float).rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    """EMA with alpha = 2/(n+1), seeded with SMA(n) (TradingView ``ta.ema`` convention)."""
    return pd.Series(_seeded_recursive(s.to_numpy(dtype=float), n, 2.0 / (n + 1)), index=s.index)


def roc(close: pd.Series, n: int = 10) -> pd.Series:
    """Rate of change in percent."""
    c = close.astype(float)
    return (c / c.shift(n) - 1.0) * 100.0


# ----------------------------------------------------------------------------- oscillators


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder RSI (first value at bar ``n``)."""
    delta = close.astype(float).diff()
    gain = wilder(delta.clip(lower=0), n)
    loss = wilder((-delta).clip(lower=0), n)
    rs = gain / loss
    out = 100.0 - 100.0 / (1.0 + rs)
    out = out.where(loss != 0, 100.0)
    out = out.where(~((loss == 0) & (gain == 0)), 50.0)
    return out.where(gain.notna())


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig}, index=close.index)


def stoch_rsi(close: pd.Series, n: int = 14, k: int = 3, d: int = 3) -> pd.DataFrame:
    """Stochastic RSI (0..100): RSI(n) normalised over n bars, %K = SMA(k), %D = SMA(%K, d)."""
    r = rsi(close, n)
    lo = r.rolling(n, min_periods=n).min()
    hi = r.rolling(n, min_periods=n).max()
    rng = hi - lo
    raw = ((r - lo) / rng.where(rng > 0)) * 100.0
    raw = raw.where(rng > 0, 50.0).where(rng.notna())
    kk = sma(raw, k)
    return pd.DataFrame({"k": kk, "d": sma(kk, d)}, index=close.index)


# ----------------------------------------------------------------------------- volatility / trend


def true_range(df: pd.DataFrame) -> pd.Series:
    h, lo, c = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
    pc = c.shift(1)
    tr = pd.concat([h - lo, (h - pc).abs(), (lo - pc).abs()], axis=1).max(axis=1)
    return tr.where(pc.notna(), h - lo)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Wilder ATR; TR of the first bar is high-low; first value at bar ``n-1``. Works on any timeframe."""
    return wilder(true_range(df), n)


def adx(df: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    """Wilder ADX / +DI / -DI. DI first valid at bar ``n``, ADX at bar ``2n-1``."""
    h, lo = df["high"].astype(float), df["low"].astype(float)
    up = h.diff()
    dn = -lo.diff()
    plus_dm = up.where((up > dn) & (up > 0), 0.0).where(up.notna())
    minus_dm = dn.where((dn > up) & (dn > 0), 0.0).where(dn.notna())
    tr = true_range(df).where(h.shift(1).notna())
    tr_s = wilder(tr, n)
    pdi = 100.0 * wilder(plus_dm, n) / tr_s.where(tr_s > 0)
    mdi = 100.0 * wilder(minus_dm, n) / tr_s.where(tr_s > 0)
    tot = pdi + mdi
    dx = (100.0 * (pdi - mdi).abs() / tot.where(tot > 0)).where(~((tot == 0) & pdi.notna()), 0.0)
    return pd.DataFrame({"adx": wilder(dx, n), "plus_di": pdi, "minus_di": mdi}, index=df.index)


def supertrend(df: pd.DataFrame, n: int = 10, mult: float = 3.0) -> pd.DataFrame:
    """Supertrend (TradingView logic, Wilder ATR). ``direction`` +1 = up (line below price), -1 = down."""
    a = atr(df, n).to_numpy()
    h = df["high"].to_numpy(dtype=float)
    lo = df["low"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)
    hl2 = (h + lo) / 2.0
    up_basic = hl2 - mult * a
    dn_basic = hl2 + mult * a
    m = len(df)
    line = np.full(m, np.nan)
    direction = np.full(m, np.nan)
    fu = fd = np.nan
    d = 0
    started = False
    for i in range(m):
        if math.isnan(a[i]):
            continue
        if not started:
            fu, fd = up_basic[i], dn_basic[i]
            d = 1 if c[i] >= hl2[i] else -1
            started = True
        else:
            pfu, pfd = fu, fd
            fu = max(up_basic[i], pfu) if c[i - 1] > pfu else up_basic[i]
            fd = min(dn_basic[i], pfd) if c[i - 1] < pfd else dn_basic[i]
            if d == -1 and c[i] > pfd:
                d = 1
            elif d == 1 and c[i] < pfu:
                d = -1
        line[i] = fu if d == 1 else fd
        direction[i] = d
    return pd.DataFrame({"supertrend": line, "direction": direction}, index=df.index)


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.DataFrame:
    """Bollinger bands (population std). ``width`` = (upper-lower)/mid in percent."""
    c = close.astype(float)
    mid = sma(c, n)
    sd = c.rolling(n, min_periods=n).std(ddof=0)
    upper, lower = mid + k * sd, mid - k * sd
    return pd.DataFrame({"mid": mid, "upper": upper, "lower": lower, "width": (upper - lower) / mid * 100.0},
                        index=close.index)


def keltner(df: pd.DataFrame, n: int = 20, mult: float = 1.5) -> pd.DataFrame:
    """Keltner channel: EMA(n) ± mult × ATR(n)."""
    mid = ema(df["close"], n)
    a = atr(df, n)
    return pd.DataFrame({"mid": mid, "upper": mid + mult * a, "lower": mid - mult * a}, index=df.index)


def squeeze(df: pd.DataFrame, n: int = 20, bb_k: float = 2.0, kc_mult: float = 1.5) -> pd.Series:
    """True while Bollinger(n, bb_k) is fully inside Keltner(n, kc_mult). False during warm-up."""
    bb = bollinger(df["close"], n, bb_k)
    kc = keltner(df, n, kc_mult)
    on = (bb["upper"] < kc["upper"]) & (bb["lower"] > kc["lower"])
    return on.fillna(False).astype(bool)


def consecutive_true(s: pd.Series) -> pd.Series:
    """Length of the current run of True values (0 when False)."""
    b = s.fillna(False).astype(bool)
    grp = (~b).cumsum()
    return b.astype(int).groupby(grp).cumsum().astype(float)


def _linreg_end(values: np.ndarray, n: int) -> np.ndarray:
    """Rolling least-squares line evaluated at the last point of each window (Pine ``linreg(src, n, 0)``)."""
    out = np.full(len(values), np.nan)
    if len(values) < n or n < 2:
        return out
    x = np.arange(n, dtype=float)
    xm = x.mean()
    w = 1.0 / n + (x - xm) * (n - 1 - xm) / ((x - xm) ** 2).sum()
    win = np.lib.stride_tricks.sliding_window_view(values, n)
    out[n - 1:] = win @ w
    return out


def squeeze_momentum(df: pd.DataFrame, n: int = 20) -> pd.Series:
    """LazyBear squeeze momentum histogram: linreg(close - avg(avg(HH_n, LL_n), SMA_n), n)."""
    c = df["close"].astype(float)
    hh = df["high"].astype(float).rolling(n, min_periods=n).max()
    ll = df["low"].astype(float).rolling(n, min_periods=n).min()
    val = c - ((hh + ll) / 2.0 + sma(c, n)) / 2.0
    return pd.Series(_linreg_end(val.to_numpy(dtype=float), n), index=df.index)


# ----------------------------------------------------------------------------- volume


def obv(df: pd.DataFrame) -> pd.Series:
    sign = np.sign(df["close"].astype(float).diff()).fillna(0.0)
    return (sign * df["volume"].astype(float).fillna(0.0)).cumsum()


def vwap(df: pd.DataFrame) -> pd.DataFrame:
    """Session VWAP (resets each IST day) on typical price with ±1σ/±2σ volume-weighted bands.

    If a session has no volume yet (e.g. spot index data) bars are equally weighted.
    """
    cols = ["vwap", "upper1", "lower1", "upper2", "lower2"]
    if df.empty:
        return _empty_frame(df.index, cols)
    tp = (df["high"].astype(float) + df["low"].astype(float) + df["close"].astype(float)) / 3.0
    vol = df["volume"].astype(float).fillna(0.0).clip(lower=0)
    key = session_dates(df.index)
    cumv = vol.groupby(key).cumsum()
    cnt = pd.Series(1.0, index=df.index).groupby(key).cumsum()
    has_vol = cumv > 0
    vw_v = (vol * tp).groupby(key).cumsum() / cumv.where(has_vol)
    m2_v = (vol * tp * tp).groupby(key).cumsum() / cumv.where(has_vol)
    vw_e = tp.groupby(key).cumsum() / cnt
    m2_e = (tp * tp).groupby(key).cumsum() / cnt
    vw = vw_v.where(has_vol, vw_e)
    m2 = m2_v.where(has_vol, m2_e)
    sd = np.sqrt((m2 - vw * vw).clip(lower=0))
    return pd.DataFrame({"vwap": vw, "upper1": vw + sd, "lower1": vw - sd, "upper2": vw + 2 * sd,
                         "lower2": vw - 2 * sd}, index=df.index)


def relative_volume(df: pd.DataFrame, n: int = 20, min_sessions: int = 3) -> pd.Series:
    """Volume / mean volume of the same time-of-day slot over the previous ``n`` sessions.

    Falls back to volume / mean of the previous ``n`` bars when fewer than ``min_sessions`` prior
    sessions exist for the slot. NaN when the baseline is zero (e.g. spot index with no volume).
    """
    if df.empty:
        return pd.Series(dtype=float, index=df.index)
    vol = df["volume"].astype(float)
    idx = ist_index(df.index)
    slot = np.asarray(idx.hour * 60 + idx.minute)
    by_slot = vol.groupby(slot).transform(
        lambda s: s.shift(1).rolling(n, min_periods=min_sessions).mean())
    fallback = vol.shift(1).rolling(n, min_periods=1).mean()
    base = by_slot.fillna(fallback)
    return vol / base.where(base > 0)


# ----------------------------------------------------------------------------- daily / session levels


def _to_date_index(daily: pd.DataFrame) -> pd.DataFrame:
    out = daily.copy()
    idx = out.index
    if isinstance(idx, pd.DatetimeIndex):
        dates = idx.tz_convert(IST).date if idx.tz is not None else idx.date
        out.index = pd.Index(list(dates), name="date")
    else:
        out.index = pd.Index([pd.Timestamp(x).date() if not isinstance(x, date) else x for x in idx],
                             name="date")
    out = out[~out.index.duplicated(keep="last")]
    return out.sort_index()


def daily_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    """One row per IST session date (index of ``datetime.date``): open, high, low, close, volume."""
    cols = ["open", "high", "low", "close", "volume"]
    if df.empty:
        return pd.DataFrame(columns=cols, dtype=float, index=pd.Index([], name="date"))
    key = pd.Index(session_dates(df.index), name="date")
    g = df.groupby(key, sort=True)
    out = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                        "close": g["close"].last(),
                        "volume": g["volume"].sum() if "volume" in df else np.nan})
    return out.astype(float)


def _next_weekday(d: date) -> date:
    d = d + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


CPR_COLUMNS = ["pivot", "bc", "tc", "width_pct", "r1", "r2", "r3", "s1", "s2", "s3"]


def cpr_levels(high, low, close) -> pd.DataFrame:
    """Floor pivots + CPR from session H/L/C arrays (no date shifting). bc <= tc always."""
    h = np.asarray(high, dtype=float)
    lo = np.asarray(low, dtype=float)
    c = np.asarray(close, dtype=float)
    p = (h + lo + c) / 3.0
    bc_raw = (h + lo) / 2.0
    tc_raw = 2.0 * p - bc_raw
    bc = np.minimum(bc_raw, tc_raw)
    tc = np.maximum(bc_raw, tc_raw)
    rng = h - lo
    return pd.DataFrame({
        "pivot": p, "bc": bc, "tc": tc, "width_pct": (tc - bc) / p * 100.0,
        "r1": 2 * p - lo, "r2": p + rng, "r3": h + 2 * (p - lo),
        "s1": 2 * p - h, "s2": p - rng, "s3": lo - 2 * (h - p),
    })


def cpr(daily: pd.DataFrame) -> pd.DataFrame:
    """CPR / floor pivots indexed by the session date the levels *apply to*.

    Row for date D is computed from the previous row of ``daily``; the final row is keyed by the next
    weekday after the last daily date (holidays are not known here).
    """
    if daily is None or daily.empty:
        return pd.DataFrame(columns=CPR_COLUMNS, dtype=float, index=pd.Index([], name="date"))
    d = _to_date_index(daily)
    lv = cpr_levels(d["high"].to_numpy(), d["low"].to_numpy(), d["close"].to_numpy())
    dates = list(d.index)
    lv.index = pd.Index(dates[1:] + [_next_weekday(dates[-1])], name="date")
    return lv


def prev_session_rows(bar_dates: np.ndarray, daily: pd.DataFrame) -> np.ndarray:
    """For each bar date, the integer row position in ``daily`` (date-indexed, sorted) of the last session
    strictly before it, or -1."""
    ddates = np.array(list(daily.index), dtype=object)
    if ddates.size == 0:
        return np.full(len(bar_dates), -1)
    ords = np.array([x.toordinal() for x in ddates])
    bords = np.array([x.toordinal() for x in bar_dates])
    return np.searchsorted(ords, bords, side="left") - 1


def session_levels(df: pd.DataFrame, or_minutes: int = 15, daily: pd.DataFrame | None = None,
                   interval_min: int | None = None) -> pd.DataFrame:
    """Per-bar session levels: prev_high, prev_low, prev_close (previous session), day_open,
    or_high, or_low (opening range of the first ``or_minutes``; NaN until the range bar has closed and
    NaN all day when the session's data does not start at 09:15).

    ``daily`` (optional) supplies prior sessions not present in ``df``; dates present in both use
    ``daily``'s values.
    """
    cols = ["prev_high", "prev_low", "prev_close", "day_open", "or_high", "or_low"]
    if df.empty:
        return _empty_frame(df.index, cols)
    idx = ist_index(df.index)
    bdates = session_dates(idx)
    own = daily_ohlc(df)
    if daily is not None and not daily.empty:
        ext = _to_date_index(daily)[["high", "low", "close"]].astype(float)
        merged = pd.concat([own[["high", "low", "close"]][~own.index.isin(ext.index)], ext]).sort_index()
    else:
        merged = own[["high", "low", "close"]]
    pos = prev_session_rows(bdates, merged)
    vals = merged.to_numpy()
    prev = np.where(pos[:, None] >= 0, vals[np.clip(pos, 0, None)], np.nan) if len(vals) else \
        np.full((len(df), 3), np.nan)
    out = pd.DataFrame(prev, index=df.index, columns=["prev_high", "prev_low", "prev_close"])
    key = pd.Index(bdates)
    out["day_open"] = df["open"].astype(float).groupby(key).transform("first").to_numpy()

    step = interval_min or infer_interval_minutes(idx)
    mins = np.asarray(idx.hour * 60 + idx.minute, dtype=float)
    open_m = MARKET_OPEN.hour * 60 + MARKET_OPEN.minute
    or_end = open_m + or_minutes
    in_or = (mins >= open_m) & (mins < or_end)
    h_or = df["high"].astype(float).where(in_or)
    l_or = df["low"].astype(float).where(in_or)
    run_h = h_or.groupby(key).cummax()
    run_l = l_or.groupby(key).cummin()
    run_h = run_h.groupby(key).ffill()
    run_l = run_l.groupby(key).ffill()
    first_min = pd.Series(mins, index=df.index).groupby(key).transform("min").to_numpy()
    complete = (mins + step >= or_end) & (first_min == open_m)
    out["or_high"] = run_h.where(complete).to_numpy()
    out["or_low"] = run_l.where(complete).to_numpy()
    return out


# ----------------------------------------------------------------------------- transforms / structure


def heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
    o = df["open"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    lo = df["low"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)
    hc = (o + h + lo + c) / 4.0
    ho = np.full(len(df), np.nan)
    for i in range(len(df)):
        ho[i] = (o[i] + c[i]) / 2.0 if i == 0 or math.isnan(ho[i - 1]) else (ho[i - 1] + hc[i - 1]) / 2.0
    return pd.DataFrame({"open": ho, "high": np.fmax(h, np.fmax(ho, hc)), "low": np.fmin(lo, np.fmin(ho, hc)),
                         "close": hc}, index=df.index)


def swing_points(df: pd.DataFrame, left: int = 3, right: int = 3) -> pd.DataFrame:
    """Confirmed swing highs/lows.

    The pivot bar is ``p``; the value (pivot high/low price) is reported on the confirmation bar
    ``p + right`` — i.e. only once it is knowable. The pivot bar is always ``right`` bars before the
    reported row. A swing high is strictly above the ``left`` previous highs and >= the ``right`` next.
    """
    h = df["high"].astype(float)
    lo = df["low"].astype(float)
    lmax = h.shift(1).rolling(left, min_periods=left).max()
    rmax = h.shift(-right).rolling(right, min_periods=right).max() if right > 0 else h * np.nan
    lmin = lo.shift(1).rolling(left, min_periods=left).min()
    rmin = lo.shift(-right).rolling(right, min_periods=right).min() if right > 0 else lo * np.nan
    is_hi = (h > lmax) & (h >= rmax)
    is_lo = (lo < lmin) & (lo <= rmin)
    sh = h.where(is_hi).shift(right)
    sl = lo.where(is_lo).shift(right)
    return pd.DataFrame({"swing_high": sh, "swing_low": sl}, index=df.index)


def divergence(price: pd.Series, osc: pd.Series, lookback: int = 30, swing: int = 3) -> pd.Series:
    """Regular divergence between ``price`` and an oscillator at confirmed price pivots.

    +1 (bullish): price lower low, oscillator higher low; -1 (bearish): price higher high, oscillator lower
    high. Reported on the pivot's confirmation bar (``swing`` bars after the pivot); 0 otherwise. The
    previous pivot must lie within ``lookback`` bars of the new one.
    """
    p = price.astype(float)
    frame = pd.DataFrame({"high": p, "low": p})
    sp = swing_points(frame, swing, swing)
    o = osc.astype(float).to_numpy()
    pv = p.to_numpy()
    out = np.zeros(len(p), dtype=int)
    last_hi: int | None = None
    last_lo: int | None = None
    sh = sp["swing_high"].to_numpy()
    sl = sp["swing_low"].to_numpy()
    ok = ~np.isnan(o)
    for i in range(len(p)):
        piv = i - swing
        if not np.isnan(sh[i]):
            if (last_hi is not None and piv - last_hi <= lookback and ok[piv] and ok[last_hi]
                    and pv[piv] > pv[last_hi] and o[piv] < o[last_hi]):
                out[i] = -1
            last_hi = piv
        if not np.isnan(sl[i]):
            if (last_lo is not None and piv - last_lo <= lookback and ok[piv] and ok[last_lo]
                    and pv[piv] < pv[last_lo] and o[piv] > o[last_lo]):
                out[i] = 1
            last_lo = piv
    return pd.Series(out, index=price.index)


def resample_ohlc(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Session-anchored resample (bins start at 09:15 IST) of an intraday frame to ``minutes`` bars.

    The last bin of a truncated frame may be incomplete — use :func:`align_htf` to map only *completed*
    higher-timeframe values back onto the base bars.
    """
    if df.empty:
        return df.copy()
    idx = ist_index(df.index)
    x = df.copy()
    x.index = idx
    open_m = MARKET_OPEN.hour * 60 + MARKET_OPEN.minute
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    for col in x.columns:
        if col not in agg:
            agg[col] = "sum" if col == "volume" else "last"
    out = x.resample(f"{minutes}min", origin="start_day", offset=f"{open_m % minutes}min").agg(agg)
    return out.dropna(subset=["close"])


def align_htf(htf: pd.DataFrame | pd.Series, base_index: pd.Index, htf_minutes: int,
              base_minutes: int | None = None, same_session: bool = True) -> pd.DataFrame | pd.Series:
    """Map higher-timeframe values onto base bars causally.

    An HTF bar opening at ``t`` becomes visible on the base bar whose close time (open + base interval) is
    >= ``t + htf_minutes``. With ``same_session`` the value is not carried into the next session.
    """
    bidx = ist_index(base_index)
    step = base_minutes or infer_interval_minutes(bidx)
    right = htf.to_frame() if isinstance(htf, pd.Series) else htf.copy()
    hidx = ist_index(right.index)
    right = right.reset_index(drop=True)
    right["_t"] = hidx + pd.Timedelta(minutes=htf_minutes)
    right["_d"] = session_dates(hidx) if len(hidx) else np.array([], dtype=object)
    right = right.sort_values("_t")
    left = pd.DataFrame({"_t": bidx + pd.Timedelta(minutes=step)})
    merged = pd.merge_asof(left, right, on="_t", direction="backward")
    merged.index = base_index
    if same_session and len(merged):
        stale = merged["_d"].to_numpy() != session_dates(bidx)
        value_cols = [c for c in merged.columns if c not in ("_t", "_d")]
        merged.loc[stale, value_cols] = np.nan
    merged = merged.drop(columns=["_t", "_d"])
    if isinstance(htf, pd.Series):
        return merged.iloc[:, 0]
    return merged
