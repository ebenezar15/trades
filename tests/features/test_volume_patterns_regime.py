import numpy as np
import pandas as pd
import pytest

from indexedge.core.clock import IST
from indexedge.core.types import Regime
from indexedge.features.patterns import PATTERN_COLUMNS, detect_patterns
from indexedge.features.regime import classify_regime
from indexedge.features.volume import anchored_vwap, volume_profile


def _frame(rows, vol=None):
    idx = pd.date_range("2026-01-05 09:15", periods=len(rows), freq="5min", tz=IST)
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx, dtype=float)
    df["volume"] = 1.0 if vol is None else vol
    return df


def test_volume_profile_poc_and_value_area():
    rows = [[100, 101, 100, 101]] * 20 + [[110, 111, 110, 111]] * 2 + [[105, 106, 105, 106]] * 3
    vol = [100.0] * 20 + [10.0] * 2 + [10.0] * 3
    vp = volume_profile(_frame(rows, vol), bins=22)
    assert 100 <= vp["poc"] <= 101
    assert vp["val"] <= vp["poc"] <= vp["vah"]
    assert vp["vah"] < 105  # 2000 of 2050 volume sits at 100-101
    assert np.isnan(volume_profile(_frame(np.empty((0, 4))))["poc"])


def test_anchored_vwap_hand():
    df = _frame([[10, 10, 10, 10], [20, 20, 20, 20], [30, 30, 30, 30]], vol=[5.0, 1.0, 3.0])
    av = anchored_vwap(df, df.index[1])
    assert np.isnan(av.iloc[0])
    assert av.iloc[1] == 20 and av.iloc[2] == pytest.approx((20 + 90) / 4)


def test_patterns_hand_cases():
    rows = [
        [10, 10.5, 8.8, 9],      # 0 bearish
        [8.9, 11, 8.8, 10.5],    # 1 bullish engulfing
        [10.5, 10.6, 9.0, 10.4], # 2 bullish pin (long lower wick) + inside? (h<11, l>8.8) -> inside too
        [10.4, 12, 10.3, 10.45], # 3 bearish pin (long upper wick)
        [10.45, 10.5, 9.0, 9.1], # 4 bearish engulfing? prev bullish 10.4->10.45, cur 10.45->9.1 yes
    ]
    p = detect_patterns(_frame(rows))
    assert list(p.columns) == PATTERN_COLUMNS
    assert p.bull_engulf.tolist() == [0, 1, 0, 0, 0]
    assert p.bear_engulf.tolist() == [0, 0, 0, 0, 1]
    assert p.pin_bull.iloc[2] == 1 and p.pin_bear.iloc[3] == 1
    assert p.inside_bar.iloc[2] == 1 and p.inside_bar.iloc[1] == 0
    assert p.nr7.sum() == 0  # fewer than 7 bars


def test_nr7():
    rngs = [5, 4, 6, 3, 5, 4, 2, 7]
    rows = [[100, 100 + r / 2, 100 - r / 2, 100] for r in rngs]
    assert detect_patterns(_frame(rows)).nr7.tolist() == [0, 0, 0, 0, 0, 0, 1, 0]


def _trend(n, slope, noise, seed=0):
    rng = np.random.default_rng(seed)
    c = 10000 + slope * np.arange(n) + rng.normal(0, noise, n)
    o = np.r_[c[0], c[:-1]]
    return _frame(np.c_[o, np.maximum(o, c) + 0.5, np.minimum(o, c) - 0.5, c])


def test_regime_trend_up_down_range_unknown():
    # hv_pct > 1 disables the high-vol rule so only the trend/range logic is tested here
    up = classify_regime(_trend(300, 1.0, 0.3), hv_pct=1.01)
    assert up.iloc[:30].eq(Regime.UNKNOWN.value).all()
    assert up.iloc[-50:].eq(Regime.TREND_UP.value).mean() > 0.9
    dn = classify_regime(_trend(300, -1.0, 0.3), hv_pct=1.01)
    assert dn.iloc[-50:].eq(Regime.TREND_DOWN.value).mean() > 0.9
    flat = classify_regime(_trend(300, 0.0, 1.0, seed=4), hv_pct=1.01)
    assert flat.iloc[-100:].eq(Regime.RANGE.value).mean() > 0.6
    assert set(up.unique()) <= {r.value for r in Regime}


def test_regime_high_vol_on_vol_spike():
    rng = np.random.default_rng(5)
    c = 1000 + np.cumsum(np.r_[rng.normal(0, 0.5, 400), rng.normal(0, 8, 40)])
    o = np.r_[c[0], c[:-1]]
    df = _frame(np.c_[o, np.maximum(o, c) + 0.3, np.minimum(o, c) - 0.3, c])
    assert classify_regime(df).iloc[-1] == Regime.HIGH_VOL.value


def test_empty():
    e = _frame(np.empty((0, 4)))
    assert detect_patterns(e).empty and classify_regime(e).empty
