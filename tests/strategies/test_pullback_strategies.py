"""Hand-built scenarios for the iteration-2 strategies (ema/fib/supertrend pullback, breakout retest,
Bollinger reversion, MACD trend). Each builds a small feature frame directly so the trigger logic is exact."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd
import pytest

from indexedge.core.clock import IST
from indexedge.core.types import Side
from indexedge.strategies import get_strategy

N = 30   # bars 09:15 .. 11:40


def make_feat(base: dict, bars: dict[int, dict] | None = None, n: int = N, d: date = date(2026, 3, 3)):
    """Feature frame: constant ``base`` columns, with per-bar overrides ``bars[i] = {col: value}``."""
    t0 = datetime.combine(d, time(9, 15), IST)
    idx = pd.DatetimeIndex([t0 + timedelta(minutes=5 * k) for k in range(n)])
    cols = {k: np.full(n, v, dtype=object if isinstance(v, str) else float) for k, v in base.items()}
    for i, row in (bars or {}).items():
        for k, v in row.items():
            cols[k][i] = v
    cols.setdefault("volume", np.full(n, 1000.0))
    return pd.DataFrame(cols, index=idx)


def bar(o, h, lo, c):
    return {"open": o, "high": h, "low": lo, "close": c}


def hm(sig):
    return sig.ts.strftime("%H:%M")


# ------------------------------------------------------------------------------------------- ema_pullback
EMA_BASE = {**bar(1010, 1013, 1008, 1012), "atr14": 10.0, "ema21": 1000.0, "ema50": 990.0, "ema200": 950.0,
            "adx14": 25.0}


def test_ema_pullback_rejection_candle_at_ema21():
    feat = make_feat(EMA_BASE, {10: bar(1008, 1009, 999, 1001), 11: bar(1001, 1009, 1000, 1008)})
    sigs = get_strategy("ema_pullback").scan(feat, "NIFTY")
    assert len(sigs) == 1
    s = sigs[0]
    assert s.side is Side.BUY and hm(s) == "10:10" and s.entry == 1008
    assert s.stop == pytest.approx(998.0)                        # pullback low 999 − 0.1×ATR
    assert s.targets == pytest.approx([1018.0, 1028.0])          # 1R partial, 2R
    assert "999.0" in s.reasons[1] and "EMA 200" in s.reasons[0]


def test_ema_pullback_needs_trend_alignment():
    feat = make_feat({**EMA_BASE, "ema50": 1005.0}, {10: bar(1008, 1009, 999, 1001),
                                                     11: bar(1001, 1009, 1000, 1008)})
    assert get_strategy("ema_pullback").scan(feat, "NIFTY") == []
    feat = make_feat({**EMA_BASE, "adx14": 12.0}, {10: bar(1008, 1009, 999, 1001),
                                                   11: bar(1001, 1009, 1000, 1008)})
    assert get_strategy("ema_pullback").scan(feat, "NIFTY") == []


# ------------------------------------------------------------------------------------------- fib_pullback
FIB_BASE = {**bar(1020, 1022, 1019, 1021), "atr14": 10.0, "swing_high": np.nan, "swing_low": np.nan}


def _fib_bars(pull_low: float):
    return {
        4: {"low": 990.0}, 7: {"swing_low": 990.0},              # swing low pivot bar 4, confirmed bar 7
        10: {"high": 1030.0}, 13: {"swing_high": 1030.0},        # swing high pivot bar 10, confirmed bar 13
        14: bar(1018, 1019, pull_low, 1012),                     # pullback into the zone (bearish)
        15: bar(1012, 1023, max(1011.0, pull_low + 1), 1022),    # reversal close above bar 14's high
    }


def test_fib_pullback_golden_zone_reversal():
    sigs = get_strategy("fib_pullback").scan(make_feat(FIB_BASE, _fib_bars(1010.0)), "NIFTY")
    assert len(sigs) == 1
    s = sigs[0]
    assert s.side is Side.BUY and hm(s) == "10:30" and s.entry == 1022
    assert s.stop == pytest.approx(1030 - 0.786 * 40 - 1.0)      # below 78.6% − 0.1×ATR
    assert s.targets == pytest.approx([1030.0, 990 + 1.272 * 40])
    assert "50.0%" in s.reasons[1]                                # retraced 20 of 40 points


def test_fib_pullback_rejects_too_deep_or_too_shallow():
    deep = get_strategy("fib_pullback").scan(make_feat(FIB_BASE, _fib_bars(1000.0)), "NIFTY")   # 75%
    assert deep == []
    shallow = get_strategy("fib_pullback").scan(make_feat(FIB_BASE, _fib_bars(1017.0)), "NIFTY")  # 32.5%
    assert shallow == []


# ------------------------------------------------------------------------------------ supertrend_pullback
ST_BASE = {**bar(1010, 1013, 1008, 1012), "atr14": 10.0, "supertrend": 990.0, "st_dir": 1.0, "ema50": 980.0}


def test_supertrend_pullback_near_line_then_resume():
    feat = make_feat(ST_BASE, {10: bar(1004, 1005, 994, 996), 11: bar(996, 1002, 995, 1001)})
    sigs = get_strategy("supertrend_pullback").scan(feat, "NIFTY")
    assert len(sigs) == 1
    s = sigs[0]
    assert s.side is Side.BUY and hm(s) == "10:10" and s.entry == 1001
    assert s.stop == pytest.approx(989.0)                         # ST line − 0.1×ATR
    assert s.targets == pytest.approx([1013.0, 1025.0])
    assert "0.40× ATR" in s.reasons[2]                             # pullback low 994 vs line 990


def test_supertrend_pullback_requires_established_trend():
    dirs = {i: {"st_dir": -1.0} for i in range(0, 8)}            # trend flipped up only at bar 8
    feat = make_feat(ST_BASE, {**dirs, 10: bar(1004, 1005, 994, 996), 11: bar(996, 1002, 995, 1001)})
    assert get_strategy("supertrend_pullback").scan(feat, "NIFTY") == []


# ---------------------------------------------------------------------------------------- breakout_retest
BR_BASE = {**bar(994, 997, 993, 995), "atr14": 10.0, "prev_high": 1000.0, "prev_low": 900.0,
           "or_high": 2000.0, "or_low": 800.0}


def test_breakout_retest_of_previous_day_high():
    feat = make_feat(BR_BASE, {6: bar(996, 1004, 995, 1003), 7: bar(1003, 1007, 1002, 1006),
                               8: bar(1001, 1005, 1000.5, 1004)})
    sigs = get_strategy("breakout_retest").scan(feat, "NIFTY")
    assert len(sigs) == 1
    s = sigs[0]
    assert s.side is Side.BUY and hm(s) == "09:55" and s.entry == 1004
    assert s.stop == pytest.approx(997.5)                         # level 1000 − 0.25×ATR
    assert s.targets == pytest.approx([1010.5, 1017.0])
    assert "previous-day high 1,000.0" in s.reasons[0] and "09:45" in s.reasons[0]


def test_breakout_retest_failed_retest_is_skipped():
    # the retest candle closes back below the level -> setup cancelled, no trade
    feat = make_feat(BR_BASE, {6: bar(996, 1004, 995, 1003), 7: bar(1003, 1004, 995, 996),
                               8: bar(996, 1005, 995, 1004)})
    assert get_strategy("breakout_retest").scan(feat, "NIFTY") == []


# ------------------------------------------------------------------------------------ bollinger_reversion
BB_BASE = {**bar(1005, 1007, 1004, 1006), "atr14": 10.0, "bb_upper": 1030.0, "bb_lower": 990.0,
           "bb_mid": 1010.0, "regime": "range"}


def test_bollinger_reversion_reentry_targets_mid_band():
    feat = make_feat(BB_BASE, {10: bar(992, 993, 985, 987), 11: bar(988, 994, 986, 993)})
    sigs = get_strategy("bollinger_reversion").scan(feat, "NIFTY")
    assert len(sigs) == 1
    s = sigs[0]
    assert s.side is Side.BUY and hm(s) == "10:10" and s.entry == 993
    assert s.stop == pytest.approx(984.0)                         # extreme 985 − 0.1×ATR
    assert s.targets == pytest.approx([1010.0])
    assert "range" in s.reasons[0]


def test_bollinger_reversion_only_in_range_regime():
    feat = make_feat({**BB_BASE, "regime": "trend_down"}, {10: bar(992, 993, 985, 987),
                                                          11: bar(988, 994, 986, 993)})
    assert get_strategy("bollinger_reversion").scan(feat, "NIFTY") == []


# ---------------------------------------------------------------------------------------------- macd_trend
MACD_BASE = {**bar(1010, 1013, 1008, 1012), "atr14": 10.0, "ema200": 900.0, "macd": -1.0, "macd_signal": 0.0,
             "macd_hist": -1.0}


def _macd_bars():
    return {7: {"low": 995.0}, 9: {"macd_hist": -0.5}, 10: {"macd": 0.5, "macd_hist": 0.5},
            11: {"macd": 0.6, "macd_hist": 0.6}}


def test_macd_trend_cross_above_ema200():
    sigs = get_strategy("macd_trend").scan(make_feat(MACD_BASE, _macd_bars()), "NIFTY")
    assert len(sigs) == 1
    s = sigs[0]
    assert s.side is Side.BUY and hm(s) == "10:05" and s.entry == 1012
    assert s.stop == pytest.approx(994.0)                         # 10-bar swing low 995 − 0.1×ATR
    assert s.targets == pytest.approx([1048.0])                   # 2R
    assert "-0.50 → 0.50" in s.reasons[2]


def test_macd_trend_filters():
    below = make_feat({**MACD_BASE, "ema200": 1100.0}, _macd_bars())
    assert get_strategy("macd_trend").scan(below, "NIFTY") == []
    zero = get_strategy("macd_trend", {"zero_line_filter": True})
    assert zero.scan(make_feat(MACD_BASE, _macd_bars()), "NIFTY") == []
