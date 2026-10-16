import numpy as np
import pandas as pd
import pytest

from indexedge.core.types import Regime
from indexedge.features.engine import FEATURE_COLUMNS, build_features

REQUIRED = [
    "ema9", "ema21", "ema50", "ema200", "rsi14", "macd", "macd_signal", "macd_hist", "atr14", "adx14", "plus_di",
    "minus_di", "supertrend", "st_dir", "st_10_1_dir", "st_11_2_dir", "st_12_3_dir", "st_12_3", "vwap", "vwap_u1",
    "vwap_l1", "vwap_u2", "vwap_l2", "dist_vwap_atr", "bb_mid", "bb_upper", "bb_lower", "bb_width", "kc_upper",
    "kc_lower", "squeeze", "squeeze_bars", "squeeze_mom", "stochrsi_k", "stochrsi_d", "obv_slope", "relvol",
    "pivot", "bc", "tc", "cpr_width_pct", "r1", "r2", "r3", "s1", "s2", "s3", "prev_high", "prev_low",
    "prev_close", "day_open", "or_high", "or_low", "gap_pct", "bull_engulf", "bear_engulf", "pin_bull",
    "pin_bear", "inside_bar", "nr7", "regime", "regime_trend_up", "regime_trend_down", "regime_range",
    "regime_high_vol", "minute_of_day", "day_of_week", "ha_open", "ha_close", "swing_high", "swing_low",
    "rsi_div",
]


def test_columns_present_and_feature_list_numeric(df5):
    f = build_features(df5)
    missing = [c for c in REQUIRED if c not in f.columns]
    assert not missing
    assert set(FEATURE_COLUMNS) <= set(f.columns)
    assert "regime" not in FEATURE_COLUMNS and len(set(FEATURE_COLUMNS)) == len(FEATURE_COLUMNS)
    for c in FEATURE_COLUMNS:
        assert pd.api.types.is_numeric_dtype(f[c]), c
    assert not np.isinf(f[FEATURE_COLUMNS].to_numpy()).any()
    assert set(f["regime"].unique()) <= {r.value for r in Regime}
    # warmed up at the end of 6 days: core ML features populated
    last = f[FEATURE_COLUMNS].iloc[-1]
    assert last.drop(["dist_ema200_atr"]).notna().mean() > 0.95
    assert list(f.columns[:5]) == ["open", "high", "low", "close", "volume"]
    assert len(f) == len(df5)


def test_values_sane(df5):
    f = build_features(df5)
    day = f.index.date
    first_bar = np.r_[True, day[1:] != day[:-1]]
    np.testing.assert_allclose(f.loc[first_bar, "vwap"],
                               (f.high + f.low + f.close)[first_bar] / 3)  # VWAP reset each day
    assert f["st_dir"].dropna().isin([-1, 1]).all()
    assert f["minute_of_day"].iloc[0] == 9 * 60 + 15 and f["mins_from_open"].iloc[0] == 0
    assert f["day_of_week"].iloc[0] == 0  # 2026-01-05 is a Monday
    days = sorted(set(day))
    d2 = f[day == days[1]]
    prev = df5[day == days[0]]
    assert d2["gap_pct"].iloc[0] == pytest.approx((d2.open.iloc[0] / prev.close.iloc[-1] - 1) * 100)


def test_single_day_works(candles):
    f = build_features(candles(days=1))
    assert len(f) == 75
    assert f["prev_close"].isna().all() and f["ema200"].isna().all()
    assert f["vwap"].notna().all() and f["or_high"].iloc[2:].notna().all()
    assert f["rsi14"].iloc[20:].notna().all()



def test_empty_input_returns_empty_frame(df5):
    e = build_features(df5.iloc[:0])
    assert e.empty and set(FEATURE_COLUMNS) <= set(e.columns) and "regime" in e.columns
    assert build_features(None).empty
    one = build_features(df5.iloc[:1])
    assert len(one) == 1 and one["vwap"].iloc[0] == pytest.approx(
        (df5.high.iloc[0] + df5.low.iloc[0] + df5.close.iloc[0]) / 3)


def test_daily_frame_supplies_prev_levels(candles):
    df = candles(days=1)
    daily = pd.DataFrame({"open": [23900.0], "high": [24100.0], "low": [23800.0], "close": [24000.0]},
                         index=pd.to_datetime(["2026-01-02"]))
    f = build_features(df, daily=daily)
    assert (f["prev_close"] == 24000).all()
    p = (24100 + 23800 + 24000) / 3
    assert f["pivot"].iloc[0] == pytest.approx(p)
    assert f["gap_pct"].iloc[0] == pytest.approx((df.open.iloc[0] / 24000 - 1) * 100)


def test_spot_index_without_volume(candles):
    f = build_features(candles(days=3, volume=False))
    assert f["vwap"].notna().all()
    assert f["relvol"].isna().all()


def test_naive_unsorted_duplicated_index(candles):
    df = candles(days=2)
    messy = pd.concat([df.iloc[::-1], df.iloc[:5]])
    messy.index = messy.index.tz_localize(None)
    f = build_features(messy)
    ref = build_features(df)
    assert len(f) == len(df) and str(f.index.tz) == "Asia/Kolkata"
    pd.testing.assert_frame_equal(f[FEATURE_COLUMNS], ref[FEATURE_COLUMNS])


def test_truncation_no_lookahead(candles):
    """Value at bar i computed on df[:i+1] must equal the value computed on the full frame."""
    df = candles(days=6, seed=7)
    daily = pd.DataFrame({"open": [23900.0], "high": [24100.0], "low": [23800.0], "close": [24000.0]},
                         index=pd.to_datetime(["2026-01-02"]))
    full = build_features(df, daily=daily)
    numeric = [c for c in full.columns if c != "regime"]
    rng = np.random.default_rng(0)
    sample = sorted(set(rng.integers(1, len(df), 22).tolist()) | {2, 74, 75, 77, 220, len(df) - 1})
    for i in sample:
        part = build_features(df.iloc[: i + 1], daily=daily)
        a = part[numeric].iloc[-1].to_numpy(dtype=float)
        b = full[numeric].iloc[i].to_numpy(dtype=float)
        bad = [numeric[j] for j in np.where(~np.isclose(a, b, rtol=1e-9, atol=1e-9, equal_nan=True))[0]]
        assert not bad, f"look-ahead at bar {i}: {bad}"
        assert part["regime"].iloc[-1] == full["regime"].iloc[i]
