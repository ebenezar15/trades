from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from indexedge.core.clock import IST
from indexedge.features import indicators as ind

# Wilder RSI example data and values as published by StockCharts ("RSI" ChartSchool article).
RSI_CLOSES = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28,
              46.28, 46.00, 46.03, 46.41, 46.22, 45.64, 46.21, 46.25, 45.71, 46.45, 45.78, 45.35, 44.03, 44.18,
              44.22, 44.57, 43.42, 42.66, 43.13]
RSI_EXPECTED = [70.53, 66.32, 66.55, 69.41, 66.36, 57.97, 62.93, 63.26, 56.06, 62.38, 54.71, 50.42, 39.99,
                41.46, 41.87, 45.46, 37.30, 33.08, 37.77]


def _frame(rows, start="2026-01-05 09:15", freq="5min"):
    idx = pd.date_range(start, periods=len(rows), freq=freq, tz=IST)
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx, dtype=float)
    df["volume"] = 1.0
    return df


# --------------------------------------------------------------------------- averages


def test_ema_sma_seeded_reference():
    s = pd.Series(np.arange(1.0, 11.0))
    e = ind.ema(s, 3)
    assert e.iloc[:2].isna().all()
    # seed = mean(1,2,3) = 2, alpha 0.5; for a linear series EMA(3) lags by exactly 1
    np.testing.assert_allclose(e.iloc[2:].to_numpy(), s.iloc[2:].to_numpy() - 1.0)
    np.testing.assert_allclose(ind.sma(s, 4).iloc[3:].to_numpy(), np.arange(2.5, 9.0))


def test_ema_hand_values():
    s = pd.Series([22.27, 22.19, 22.08, 22.17, 22.18, 22.13, 22.23, 22.43, 22.24, 22.29, 22.15, 22.39])
    e = ind.ema(s, 10)
    seed = np.mean(s.iloc[:10])
    assert e.iloc[9] == pytest.approx(seed)
    k = 2 / 11
    nxt = seed + k * (22.15 - seed)
    assert e.iloc[10] == pytest.approx(nxt)
    assert e.iloc[11] == pytest.approx(nxt + k * (22.39 - nxt))


def test_ema_ignores_leading_nan():
    s = pd.Series([np.nan, np.nan, 1.0, 2.0, 3.0, 4.0])
    e = ind.ema(s, 2)
    assert np.isnan(e.iloc[2]) and e.iloc[3] == pytest.approx(1.5)


def test_roc():
    s = pd.Series([100.0, 101, 102, 110])
    assert ind.roc(s, 3).iloc[3] == pytest.approx(10.0)


# --------------------------------------------------------------------------- RSI / MACD / stoch


def test_rsi_wilder_stockcharts_reference():
    r = ind.rsi(pd.Series(RSI_CLOSES), 14)
    assert r.iloc[:14].isna().all()
    # the published table rounds intermediate averages to 2 dp, hence the 0.1 tolerance
    np.testing.assert_allclose(r.iloc[14:].to_numpy(), RSI_EXPECTED, atol=0.1)
    assert r.iloc[14] == pytest.approx(70.4641, abs=1e-4)  # exact (unrounded) Wilder value


def test_rsi_extremes():
    assert ind.rsi(pd.Series(np.arange(30.0)), 14).iloc[-1] == 100.0
    assert ind.rsi(pd.Series(np.full(30, 5.0)), 14).iloc[-1] == 50.0
    assert ind.rsi(pd.Series(np.arange(30.0, 0, -1)), 14).iloc[-1] == pytest.approx(0.0)


def test_macd_relations():
    s = pd.Series(np.sin(np.arange(200) / 7.0) * 10 + 100)
    m = ind.macd(s)
    np.testing.assert_allclose(m["macd"], ind.ema(s, 12) - ind.ema(s, 26), equal_nan=True)
    np.testing.assert_allclose(m["hist"], m["macd"] - m["signal"], equal_nan=True)
    assert m["signal"].first_valid_index() == 25 + 8


def test_stoch_rsi_bounds():
    s = pd.Series(np.cumsum(np.random.default_rng(0).normal(0, 1, 300)) + 100)
    sr = ind.stoch_rsi(s).dropna()
    assert len(sr) > 200
    assert sr["k"].between(0, 100).all() and sr["d"].between(0, 100).all()


# --------------------------------------------------------------------------- ATR / ADX / supertrend


def _ref_atr(h, lo, c, n):
    tr = [h[0] - lo[0]] + [max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1])) for i in range(1, len(c))]
    out = [np.nan] * len(c)
    a = sum(tr[:n]) / n
    out[n - 1] = a
    for i in range(n, len(c)):
        a = (a * (n - 1) + tr[i]) / n
        out[i] = a
    return np.array(out)


def _ref_adx(h, lo, c, n):
    """Wilder's original running-sum formulation (independent of the library's mean-based RMA)."""
    m = len(c)
    trs, pdm, mdm = [], [], []
    for i in range(1, m):
        trs.append(max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1])))
        up, dn = h[i] - h[i - 1], lo[i - 1] - lo[i]
        pdm.append(up if up > dn and up > 0 else 0.0)
        mdm.append(dn if dn > up and dn > 0 else 0.0)
    tr_s, p_s, m_s = sum(trs[:n]), sum(pdm[:n]), sum(mdm[:n])
    pdi = np.full(m, np.nan)
    mdi = np.full(m, np.nan)
    dx = np.full(m, np.nan)
    for j in range(n - 1, m - 1):
        if j > n - 1:
            tr_s = tr_s - tr_s / n + trs[j]
            p_s = p_s - p_s / n + pdm[j]
            m_s = m_s - m_s / n + mdm[j]
        i = j + 1
        pdi[i], mdi[i] = 100 * p_s / tr_s, 100 * m_s / tr_s
        dx[i] = 100 * abs(pdi[i] - mdi[i]) / (pdi[i] + mdi[i])
    adx = np.full(m, np.nan)
    first = 2 * n - 1
    adx[first] = np.mean(dx[n:first + 1])
    for i in range(first + 1, m):
        adx[i] = (adx[i - 1] * (n - 1) + dx[i]) / n
    return adx, pdi, mdi


@pytest.fixture
def ohlc():
    rng = np.random.default_rng(3)
    c = 100 + np.cumsum(rng.normal(0, 1, 120))
    o = np.r_[100, c[:-1]]
    h = np.maximum(o, c) + rng.uniform(0, 1, 120)
    lo = np.minimum(o, c) - rng.uniform(0, 1, 120)
    return _frame(np.c_[o, h, lo, c])


def test_atr_matches_hand_loop(ohlc):
    a = ind.atr(ohlc, 14)
    ref = _ref_atr(ohlc.high.values, ohlc.low.values, ohlc.close.values, 14)
    np.testing.assert_allclose(a.to_numpy(), ref, equal_nan=True)
    assert a.first_valid_index() == ohlc.index[13]


def test_atr_tiny_hand_example():
    df = _frame([[10, 12, 9, 11], [11, 15, 10, 14], [14, 14, 8, 9]])
    # TR = 3, 5 (15-10), 6 (14-8); ATR(2): seed (3+5)/2 = 4, then (4*1 + 6)/2 = 5
    np.testing.assert_allclose(ind.atr(df, 2).to_numpy(), [np.nan, 4.0, 5.0], equal_nan=True)


def test_adx_matches_wilder_reference(ohlc):
    got = ind.adx(ohlc, 14)
    adx, pdi, mdi = _ref_adx(ohlc.high.values, ohlc.low.values, ohlc.close.values, 14)
    np.testing.assert_allclose(got["plus_di"], pdi, equal_nan=True, rtol=1e-9)
    np.testing.assert_allclose(got["minus_di"], mdi, equal_nan=True, rtol=1e-9)
    np.testing.assert_allclose(got["adx"], adx, equal_nan=True, rtol=1e-9)
    assert got["adx"].first_valid_index() == ohlc.index[27]


def test_supertrend_direction_flips():
    up = np.linspace(100, 150, 60)
    dn = np.linspace(150, 90, 60)
    c = np.r_[up, dn]
    df = _frame(np.c_[c, c + 0.5, c - 0.5, c])
    st = ind.supertrend(df, 10, 3.0)
    assert st["direction"].iloc[55] == 1 and st["direction"].iloc[-1] == -1
    valid = st.dropna()
    longs = valid[valid.direction == 1]
    shorts = valid[valid.direction == -1]
    assert (longs.supertrend < df.close.loc[longs.index]).all()
    assert (shorts.supertrend > df.close.loc[shorts.index]).all()
    # long stop never decreases while trend is up
    seg = st["supertrend"].iloc[10:60]
    assert (seg.diff().dropna() >= -1e-9).all()


# --------------------------------------------------------------------------- vwap / bands


def test_vwap_resets_daily_and_hand_values():
    idx = pd.DatetimeIndex([datetime(2026, 1, 5, 9, 15, tzinfo=IST), datetime(2026, 1, 5, 9, 20, tzinfo=IST),
                            datetime(2026, 1, 6, 9, 15, tzinfo=IST)])
    df = pd.DataFrame({"open": [10, 20, 50.0], "high": [10, 20, 50.0], "low": [10, 20, 50.0],
                       "close": [10, 20, 50.0], "volume": [1, 3, 2.0]}, index=idx)
    v = ind.vwap(df)
    assert v["vwap"].iloc[0] == 10
    assert v["vwap"].iloc[1] == pytest.approx((10 + 60) / 4)
    assert v["vwap"].iloc[2] == 50  # reset
    sd = np.sqrt((1 * 100 + 3 * 400) / 4 - 17.5 ** 2)
    assert v["upper1"].iloc[1] == pytest.approx(17.5 + sd)
    assert v["lower2"].iloc[1] == pytest.approx(17.5 - 2 * sd)


def test_vwap_zero_volume_uses_equal_weights():
    df = _frame([[10, 10, 10, 10], [20, 20, 20, 20]])
    df["volume"] = 0.0
    assert ind.vwap(df)["vwap"].iloc[1] == pytest.approx(15.0)


def test_bollinger_keltner_squeeze():
    rng = np.random.default_rng(1)
    calm = 100 + rng.normal(0, 0.05, 60)
    df = _frame(np.c_[calm, calm + 1.0, calm - 1.0, calm])  # wide bars, tiny closes dispersion
    assert ind.squeeze(df).iloc[-1]
    assert not ind.squeeze(df).iloc[:5].any()  # warm-up -> False
    bb = ind.bollinger(df.close, 20, 2.0)
    valid = bb.dropna()
    assert len(valid) == 41 and (valid["upper"] >= valid["mid"]).all()
    flat = ind.bollinger(pd.Series(np.full(25, 50.0)), 20)
    assert flat["width"].iloc[-1] == 0
    kc = ind.keltner(df, 20, 1.5)
    np.testing.assert_allclose((kc["upper"] - kc["mid"]), 1.5 * ind.atr(df, 20), equal_nan=True)
    run = ind.consecutive_true(pd.Series([True, True, False, True, True, True]))
    assert run.tolist() == [1, 2, 0, 1, 2, 3]


def test_linreg_and_squeeze_momentum():
    x = np.arange(50.0) * 2 + 5
    np.testing.assert_allclose(ind._linreg_end(x, 10)[9:], x[9:])
    df = _frame(np.c_[x, x + 1, x - 1, x])
    sm = ind.squeeze_momentum(df, 20)
    assert sm.first_valid_index() == df.index[38]
    # rising market => positive momentum
    up = np.arange(80.0)
    m = ind.squeeze_momentum(_frame(np.c_[up, up + 1, up - 1, up]), 20).dropna()
    assert len(m) and (m > 0).all()


def test_obv():
    df = _frame([[1, 1, 1, 10], [1, 1, 1, 11], [1, 1, 1, 9], [1, 1, 1, 9]])
    df["volume"] = [5, 2, 3, 4]
    assert ind.obv(df).tolist() == [0, 2, -1, -1]


def test_relative_volume_same_slot(candles):
    df = candles(days=6, seed=2)
    df["volume"] = 100.0
    last_day = df.index.date == df.index[-1].date()
    df.loc[last_day, "volume"] = 200.0
    rv = ind.relative_volume(df, 20)
    assert rv[last_day].round(9).eq(2.0).all()
    # first day falls back to rolling mean of previous bars
    assert np.isnan(rv.iloc[0]) and rv.iloc[1] == pytest.approx(1.0)


def test_relative_volume_zero_volume_is_nan(candles):
    df = candles(days=2, volume=False)
    assert ind.relative_volume(df).isna().all()


# --------------------------------------------------------------------------- daily / session levels


def test_daily_ohlc_and_cpr_hand_values():
    idx = pd.DatetimeIndex([datetime(2026, 1, 8, 9, 15, tzinfo=IST), datetime(2026, 1, 8, 15, 25, tzinfo=IST),
                            datetime(2026, 1, 9, 9, 15, tzinfo=IST)])
    df = pd.DataFrame({"open": [100, 95, 106.0], "high": [110, 100, 107.0], "low": [99, 90, 104.0],
                       "close": [96, 105, 105.0], "volume": [1, 1, 1.0]}, index=idx)
    d = ind.daily_ohlc(df)
    assert list(d.index) == [date(2026, 1, 8), date(2026, 1, 9)]
    assert d.loc[date(2026, 1, 8)].tolist() == [100, 110, 90, 105, 2]
    c = ind.cpr(d)
    # levels from Jan 8 (H110 L90 C105) apply to Jan 9; Jan 9's apply to next weekday (Mon Jan 12)
    assert list(c.index) == [date(2026, 1, 9), date(2026, 1, 12)]
    r = c.loc[date(2026, 1, 9)]
    p = (110 + 90 + 105) / 3
    assert r.pivot == pytest.approx(p)
    assert r.bc == pytest.approx(100.0) and r.tc == pytest.approx(2 * p - 100)
    assert r.width_pct == pytest.approx((2 * p - 200) / p * 100)
    assert r.r1 == pytest.approx(2 * p - 90) and r.s1 == pytest.approx(2 * p - 110)
    assert r.r2 == pytest.approx(p + 20) and r.s2 == pytest.approx(p - 20)
    assert r.r3 == pytest.approx(110 + 2 * (p - 90)) and r.s3 == pytest.approx(90 - 2 * (110 - p))


def test_cpr_bc_tc_ordered():
    c = ind.cpr(pd.DataFrame({"high": [110.0, 1], "low": [90.0, 1], "close": [92.0, 1]},
                             index=pd.to_datetime(["2026-01-05", "2026-01-06"])))
    assert (c.bc <= c.tc).all()


def test_session_levels_opening_range(candles):
    df = candles(days=2)
    sl = ind.session_levels(df)
    day2 = df.index.date == df.index[-1].date()
    d2 = df[day2]
    s2 = sl[day2]
    # 5-min bars: OR (09:15-09:30) known at the close of the 09:25 bar (third bar)
    assert s2["or_high"].iloc[:2].isna().all()
    assert s2["or_high"].iloc[2] == pytest.approx(d2.high.iloc[:3].max())
    assert s2["or_low"].iloc[-1] == pytest.approx(d2.low.iloc[:3].min())
    d1 = df[~day2]
    assert s2["prev_high"].iloc[0] == pytest.approx(d1.high.max())
    assert s2["prev_close"].iloc[0] == pytest.approx(d1.close.iloc[-1])
    assert (s2["day_open"] == d2.open.iloc[0]).all()
    assert sl[~day2]["prev_close"].isna().all()


def test_session_levels_15min_bars_and_late_start(candles):
    df15 = candles(days=1, interval=15)
    sl = ind.session_levels(df15)
    assert sl["or_high"].iloc[0] == pytest.approx(df15.high.iloc[0])
    late = candles(days=1).iloc[4:]
    assert ind.session_levels(late)["or_high"].isna().all()


def test_session_levels_external_daily(candles):
    df = candles(days=1)
    daily = pd.DataFrame({"open": [1.0], "high": [200.0], "low": [100.0], "close": [150.0]},
                         index=pd.to_datetime(["2026-01-02"]))
    sl = ind.session_levels(df, daily=daily)
    assert (sl["prev_high"] == 200).all() and (sl["prev_close"] == 150).all()


# --------------------------------------------------------------------------- structure


def test_heikin_ashi_hand():
    df = _frame([[10, 12, 9, 11], [11, 13, 10, 12]])
    ha = ind.heikin_ashi(df)
    assert ha["close"].iloc[0] == pytest.approx(10.5) and ha["open"].iloc[0] == pytest.approx(10.5)
    assert ha["open"].iloc[1] == pytest.approx(10.5)
    assert ha["close"].iloc[1] == pytest.approx(11.5)
    assert ha["high"].iloc[1] == 13 and ha["low"].iloc[1] == 10


def test_swing_points_confirmed_late_and_causal():
    hi = [1, 2, 3, 9, 3, 2, 1, 2, 3]
    df = _frame(np.c_[hi, hi, np.array(hi) - 0.5, hi])
    sp = ind.swing_points(df, 3, 3)
    # pivot at bar 3 reported at bar 6
    assert sp["swing_high"].iloc[6] == 9 and sp["swing_high"].drop(df.index[6]).isna().all()
    for i in range(len(df)):
        part = ind.swing_points(df.iloc[: i + 1], 3, 3)
        np.testing.assert_array_equal(part.to_numpy(), sp.iloc[: i + 1].to_numpy())


def test_divergence_bullish_and_bearish():
    # price: two lows, second lower; oscillator: second low higher -> bullish
    price = pd.Series([10, 9, 8, 5, 8, 9, 10, 9, 8, 4, 8, 9, 10, 11], dtype=float)
    osc = pd.Series([50, 45, 40, 20, 40, 45, 50, 45, 40, 30, 40, 45, 50, 55], dtype=float)
    d = ind.divergence(price, osc, lookback=30, swing=3)
    assert d.iloc[12] == 1 and d.drop(12).eq(0).all()
    d2 = ind.divergence(-price, -osc, lookback=30, swing=3)
    assert d2.iloc[12] == -1
    # out of lookback -> nothing
    assert ind.divergence(price, osc, lookback=3, swing=3).eq(0).all()


def test_resample_and_align_htf(candles):
    df = candles(days=2)
    h15 = ind.resample_ohlc(df, 15)
    assert h15.index[0].strftime("%H:%M") == "09:15" and len(h15) == 50
    first = df.iloc[:3]
    assert h15.iloc[0].high == pytest.approx(first.high.max()) and h15.iloc[0].volume == first.volume.sum()
    aligned = ind.align_htf(h15["close"], df.index, 15)
    assert aligned.iloc[:2].isna().all()
    assert aligned.iloc[2] == pytest.approx(df.close.iloc[2])
    # next session starts NaN (not carried over)
    day2_start = int(np.argmax(df.index.date == df.index[-1].date()))
    assert np.isnan(aligned.iloc[day2_start])
    h60 = ind.resample_ohlc(df, 60)
    assert h60.index[0].strftime("%H:%M") == "09:15"


def test_empty_inputs_do_not_raise():
    empty = _frame(np.empty((0, 4)))
    assert ind.vwap(empty).empty and ind.relative_volume(empty).empty
    assert ind.session_levels(empty).empty and ind.daily_ohlc(empty).empty
    assert ind.cpr(ind.daily_ohlc(empty)).empty
    assert ind.atr(empty).empty and ind.supertrend(empty).empty
