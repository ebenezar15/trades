"""Strategy plug-in behaviour: registry, sanity on synthetic data, explanations, edge cases and rule checks."""
from __future__ import annotations

import re
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from strat_helpers import extra_for, make_session

from indexedge.core.types import Side
from indexedge.features.engine import FEATURE_COLUMNS, build_features
from indexedge.strategies import all_strategies, get_strategy
from indexedge.strategies.registry import STRATEGY_IDS
from indexedge.strategies.straddle import leg_values, time_to_expiry

EXPECTED = {"orb15", "vwap_pullback", "ema_supertrend_adx", "triple_supertrend", "cpr_pivot", "cpr_vwap",
            "squeeze_breakout", "rsi_divergence", "oi_pcr", "straddle_920", "expiry_straddle", "gap_fill",
            "inside_bar_breakout"}


def test_registry_has_all_13():
    strats = all_strategies()
    assert set(strats) == EXPECTED == set(STRATEGY_IDS)
    for sid, s in strats.items():
        assert s.id == sid and s.name and s.description and s.learn
        assert s.backtest_quality == ("approximate" if sid in {"oi_pcr", "straddle_920", "expiry_straddle"}
                                      else "full")


def test_params_override_and_unknown_param():
    s = all_strategies({"orb15": {"vol_mult": 2.0, "bogus": 1}})["orb15"]
    assert s.params["vol_mult"] == 2.0 and "bogus" not in s.params
    with pytest.raises(ValueError):
        get_strategy("orb15", {"bogus": 1})
    with pytest.raises(KeyError):
        get_strategy("nope")


@pytest.mark.parametrize("sid", STRATEGY_IDS)
def test_every_strategy_signals_on_250_days(sid, feat250):
    s = get_strategy(sid)
    sigs = s.scan(feat250, "NIFTY", extra_for(sid, feat250.index))
    assert len(sigs) >= 1
    times = [x.ts for x in sigs]
    assert times == sorted(times)
    for sig in sigs[:25]:
        assert sig.strategy_id == sid and sig.underlying == "NIFTY"
        assert sig.reasons and all(isinstance(r, str) and r for r in sig.reasons)
        assert any(re.search(r"\d", r) for r in sig.reasons), "reasons must quote numbers"
        assert sig.features and set(sig.features) <= set(FEATURE_COLUMNS)
        assert all(np.isfinite(v) for v in sig.features.values())
        assert sig.risk_points > 0
        assert pd.Timestamp(sig.ts) in feat250.index
        assert sig.entry == pytest.approx(feat250.loc[pd.Timestamp(sig.ts), "close"])
        if sig.instrument_hint != "straddle":
            sg = sig.side.sign
            assert sg * (sig.entry - sig.stop) > 0
            assert sig.targets and all(sg * (t - sig.entry) > 0 for t in sig.targets)


@pytest.mark.parametrize("sid", STRATEGY_IDS)
def test_learn_markdown_sections(sid):
    learn = get_strategy(sid).learn.lower()
    assert learn.startswith("###")
    for word in ("what it is", "why it can work", "when it fails", "rules"):
        assert word in learn, f"{sid} learn text lacks '{word}'"
    if get_strategy(sid).backtest_quality == "approximate":
        assert "paper-only" in learn and "approximate" in learn


@pytest.mark.parametrize("sid", STRATEGY_IDS)
def test_empty_and_bad_input_never_raise(sid, feat250):
    s = get_strategy(sid)
    assert s.scan(None, "NIFTY") == [] and s.last_skip_reason
    assert s.scan(build_features(pd.DataFrame()), "NIFTY") == []
    assert s.scan(feat250[["open", "high", "low", "close"]], "NIFTY") == []
    assert "missing" in s.last_skip_reason
    # warm-up only: one short morning
    short = feat250.iloc[:4]
    if sid != "straddle_920":                                   # the 09:20 straddle needs no warm-up
        assert s.scan(short, "NIFTY", extra_for(sid, short.index)) == []
    assert s.latest(None, "NIFTY") is None


def test_latest_returns_signal_on_last_bar(feat250):
    s = get_strategy("straddle_920")
    sig = s.scan(feat250, "NIFTY")[3]
    cut = feat250.loc[:pd.Timestamp(sig.ts)]
    assert s.latest(cut, "NIFTY").ts == sig.ts
    assert s.latest(feat250.loc[:pd.Timestamp(sig.ts) + timedelta(minutes=5)], "NIFTY") is None


@pytest.mark.parametrize("sid,start,end", [
    ("orb15", "09:30", "11:30"), ("vwap_pullback", "09:45", "14:30"), ("ema_supertrend_adx", "09:30", "14:45"),
    ("triple_supertrend", "09:30", "14:30"), ("cpr_pivot", "09:30", "14:00"), ("cpr_vwap", "09:30", "13:30"),
    ("squeeze_breakout", "09:45", "14:45"), ("rsi_divergence", "09:45", "14:30"), ("gap_fill", "09:30", "11:00"),
    ("inside_bar_breakout", "09:45", "14:00"), ("straddle_920", "09:20", "09:20"),
    ("expiry_straddle", "10:00", "10:00"),
])
def test_entry_windows_use_bar_close(sid, start, end, feat250):
    sigs = get_strategy(sid).scan(feat250, "NIFTY")
    to_min = lambda s: int(s[:2]) * 60 + int(s[3:])
    for sig in sigs:
        close_min = sig.ts.hour * 60 + sig.ts.minute + 5
        assert to_min(start) <= close_min <= to_min(end), (sid, sig.ts)


def test_per_day_limits(feat250):
    for sid, limit in (("vwap_pullback", 2), ("cpr_vwap", 1), ("gap_fill", 1), ("inside_bar_breakout", 2),
                       ("oi_pcr", 1), ("straddle_920", 1)):
        sigs = get_strategy(sid).scan(feat250, "NIFTY", extra_for(sid, feat250.index))
        counts = pd.Series([s.ts.date() for s in sigs]).value_counts()
        assert counts.max() <= limit, sid
    orb = get_strategy("orb15").scan(feat250, "NIFTY")
    per = pd.Series([(s.ts.date(), s.side) for s in orb]).value_counts()
    assert per.max() == 1


def test_oi_pcr_requires_oi_context(feat250):
    s = get_strategy("oi_pcr")
    assert s.scan(feat250, "NIFTY") == []
    assert "OI" in s.last_skip_reason
    # snapshot applies to the last session only
    snap = s.scan(feat250, "NIFTY", {"pcr": 1.5, "walls": {"resistance": [1e9], "support": []}})
    assert all(x.ts.date() == feat250.index[-1].date() for x in snap)
    assert all(x.side is Side.BUY for x in snap)
    assert s.instrument_hint == "option"


def test_expiry_straddle_only_on_expiry_days(feat250):
    s = get_strategy("expiry_straddle")
    sigs = s.scan(feat250, "NIFTY")
    assert sigs and all(x.ts.weekday() == 1 for x in sigs)            # default: Tuesdays
    days = sorted(set(feat250.index.date))
    custom = {days[10], days[50]}
    s2 = get_strategy("expiry_straddle", {"expiry_dates": custom})
    got = {x.ts.date() for x in s2.scan(feat250, "NIFTY")}
    assert custom <= got
    assert all(x.instrument_hint == "straddle" and x.targets == [] for x in sigs)
    assert s2.info()["params"]["expiry_dates"] == sorted(d.isoformat() for d in custom)


def test_straddle_premium_model_is_sane():
    from datetime import datetime

    from indexedge.core.clock import IST
    t = time_to_expiry(datetime(2026, 3, 3, 9, 20, tzinfo=IST), date(2026, 3, 10))
    assert 7 / 365 < t < 8 / 365
    ce, pe = leg_values(25000.0, 25000.0, 300.0)
    assert ce == pytest.approx(150) and pe == pytest.approx(150)
    ce, pe = leg_values(26000.0, 25000.0, 10.0)                       # deep ITM call ~ intrinsic
    assert ce == pytest.approx(1000, abs=0.1) and pe < 0.1


def _prev_day(d: date) -> pd.DataFrame:
    """A quiet full session the day before (warms ATR / provides previous close)."""
    n = 75
    closes = 1000 + np.sin(np.arange(n) / 5.0) * 3
    return make_session(d, list(closes))


def test_orb15_rule_on_handmade_day():
    d0, d1 = date(2026, 3, 2), date(2026, 3, 3)
    # opening range 1000-1004 in the first 3 bars, then a breakout close at 1008 on 3x volume
    closes = [1002, 1003, 1001, 1002, 1008, 1010, 1012] + [1012] * 68
    highs = [1004, 1004, 1003, 1003, 1009, 1011, 1013] + [1013] * 68
    lows = [1000, 1001, 1000, 1001, 1002, 1007, 1009] + [1011] * 68
    vols = [3000, 2000, 2000, 1000, 6000] + [1000] * 70
    df = pd.concat([_prev_day(d0), make_session(d1, closes, highs, lows, volumes=vols)])
    sigs = get_strategy("orb15").scan(build_features(df), "NIFTY")
    assert len(sigs) == 1
    s = sigs[0]
    assert s.side is Side.BUY and s.ts.strftime("%H:%M") == "09:35" and s.entry == 1008
    assert s.stop == pytest.approx(1000)                       # opposite end of the range
    assert s.targets == pytest.approx([1016, 1024])            # 1R / 2R
    assert "1,004.0" in s.reasons[0] and "×" in s.reasons[2]


def test_gap_fill_fades_gap_up():
    d0, d1 = date(2026, 3, 2), date(2026, 3, 3)
    prev = _prev_day(d0)
    pc = float(prev["close"].iloc[-1])
    o = pc * 1.01                                               # +1.0% gap (~10 points)
    closes = [o + 1, o + 2, o + 1, o - 3, o - 4] + [o - 4] * 70
    highs = [o + 4, o + 4, o + 3, o + 1, o - 2] + [o - 3.5] * 70
    lows = [o - 2, o - 1, o - 1, o - 3.5, o - 4.5] + [o - 4.5] * 70
    opens = [o, o + 1, o + 2, o + 1, o - 3] + [o - 4] * 70
    df = pd.concat([prev, make_session(d1, closes, highs, lows, opens=opens)])
    sigs = get_strategy("gap_fill").scan(build_features(df), "NIFTY")
    assert len(sigs) == 1
    s = sigs[0]
    assert s.side is Side.SELL and s.ts.strftime("%H:%M") == "09:30"
    assert s.targets[-1] == pytest.approx(pc)                   # full gap fill
    assert s.stop > o + 4                                       # above the day's high
    assert "Fade mode" in s.reasons[1]
