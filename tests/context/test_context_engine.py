"""Tests for the pure context scorer and the ContextEngine wiring (offline)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from indexedge.config.settings import ContextInput, ContextSettings
from indexedge.context.engine import (
    ContextEngine,
    effective_age_seconds,
    gap_expectation,
    score_context,
    snapshot_to_dict,
)
from indexedge.context.providers import Quote, StaticProvider, SyntheticProvider
from indexedge.core import bus as topics
from indexedge.core.bus import EventBus
from indexedge.core.clock import IST, SimClock

NOW = datetime(2026, 10, 16, 9, 0, tzinfo=IST)   # Friday pre-open


def q(sym: str, price: float, chg: float, age_min: float = 5, now: datetime = NOW) -> Quote:
    prev = price / (1 + chg / 100)
    return Quote.make(sym, price, prev, now - timedelta(minutes=age_min))


def all_quotes(chg: float = 0.0, vix: float = 13.0, vix_chg: float = 0.0, **over: float) -> dict[str, Quote]:
    cfg = ContextSettings()
    out = {}
    for inp in cfg.inputs.values():
        if inp.symbol == "^INDIAVIX":
            out[inp.symbol] = q(inp.symbol, vix, vix_chg)
        else:
            out[inp.symbol] = q(inp.symbol, 100.0, over.get(inp.symbol, chg))
    return out


def test_neutral_inputs_give_zero_bias_and_usable():
    snap = score_context(all_quotes(), ContextSettings(), NOW)
    assert snap.macro_bias == pytest.approx(0.0, abs=1e-9)
    assert not snap.risk_off and snap.usable
    assert snap.reasons[0].startswith("Macro bias")


def test_risk_on_moves_bias_positive():
    quotes = all_quotes(**{"ES=F": 1.0, "NQ=F": 1.3, "^N225": 1.3, "^HSI": 1.5, "GIFTNIFTY": 0.8})
    snap = score_context(quotes, ContextSettings(), NOW)
    assert snap.macro_bias > 0.3
    assert any("S&P 500 fut +1.0% (bullish)" in r for r in snap.reasons)


def test_inverse_weights_turn_rising_vix_usdinr_crude_bearish():
    quotes = all_quotes(**{"INR=X": 0.6, "BZ=F": 4.0, "DX-Y.NYB": 0.8, "^TNX": 4.0}, vix_chg=10.0)
    snap = score_context(quotes, ContextSettings(), NOW)
    assert snap.macro_bias < -0.3
    assert snap.inputs["usdinr"]["contribution"] < 0
    assert snap.inputs["india_vix"]["contribution"] < 0
    assert any("USD/INR" in r and "bearish, inverse" in r for r in snap.reasons)


def test_normalisation_by_typical_move():
    # 0.3% rupee move == 1 sigma; 0.3% S&P move == 0.3 sigma
    cfg = ContextSettings(inputs={
        "usdinr": ContextInput(symbol="INR=X", label="USD/INR", weight=-1.0, max_age_min=60),
        "sp": ContextInput(symbol="ES=F", label="S&P", weight=1.0, max_age_min=60)})
    snap = score_context({"INR=X": q("INR=X", 88, 0.3), "ES=F": q("ES=F", 6000, 0.3)}, cfg, NOW)
    assert snap.inputs["usdinr"]["z"] == pytest.approx(1.0, abs=1e-3)
    assert snap.inputs["sp"]["z"] == pytest.approx(0.3, abs=1e-3)


def test_z_is_clipped():
    cfg = ContextSettings(inputs={"sp": ContextInput(symbol="ES=F", label="S&P", weight=1.0)})
    snap = score_context({"ES=F": q("ES=F", 6000, 20.0)}, cfg, NOW)
    assert snap.inputs["sp"]["z"] == 3.0
    assert snap.macro_bias == pytest.approx(0.99505, abs=1e-4)   # tanh(3)


def test_vix_absolute_level_forces_risk_off():
    snap = score_context(all_quotes(vix=24.0), ContextSettings(), NOW)
    assert snap.risk_off
    assert "risk-off" in snap.reasons[1]


def test_bias_below_threshold_is_risk_off():
    quotes = all_quotes(chg=-3.0, vix_chg=15.0, **{"INR=X": 1.0, "BZ=F": 6.0, "DX-Y.NYB": 1.0, "^TNX": 6.0})
    snap = score_context(quotes, ContextSettings(), NOW)
    assert snap.macro_bias <= -0.5 and snap.risk_off


def test_missing_required_input_marks_unusable():
    quotes = all_quotes()
    del quotes["^INDIAVIX"]
    snap = score_context(quotes, ContextSettings(), NOW)
    assert not snap.usable
    assert snap.stale_required == ["india_vix"]
    assert snap.inputs["india_vix"]["missing"]
    assert any("no data" in r for r in snap.reasons)


def test_missing_optional_input_is_fine():
    quotes = all_quotes()
    del quotes["GIFTNIFTY"]
    snap = score_context(quotes, ContextSettings(), NOW)
    assert snap.usable and snap.inputs["gift_nifty"]["missing"]


def test_stale_required_input_and_stale_optional_ignored_in_bias():
    quotes = all_quotes()
    quotes["ES=F"] = q("ES=F", 6000, 2.0, age_min=25 * 60)        # required, max 24h
    quotes["GIFTNIFTY"] = q("GIFTNIFTY", 25000, 3.0, age_min=45)  # optional, max 30 min
    snap = score_context(quotes, ContextSettings(), NOW)
    assert snap.stale_required == ["sp500_fut"] and not snap.usable
    assert snap.inputs["gift_nifty"]["stale"] and snap.inputs["gift_nifty"]["contribution"] == 0.0
    assert snap.macro_bias == pytest.approx(0.0, abs=1e-9)


def test_weekend_does_not_make_friday_vix_stale_on_monday():
    monday = datetime(2026, 10, 19, 9, 10, tzinfo=IST)
    friday_close = datetime(2026, 10, 16, 15, 30, tzinfo=IST)
    assert effective_age_seconds(friday_close, monday) == pytest.approx(17 * 3600 + 40 * 60)
    quotes = {k: Quote.make(v.symbol, v.price, v.prev_close, monday - timedelta(minutes=5))
              for k, v in all_quotes().items()}
    quotes["^INDIAVIX"] = Quote.make("^INDIAVIX", 13.0, 12.5, friday_close)
    snap = score_context(quotes, ContextSettings(), monday)
    assert "india_vix" not in snap.stale_required


def test_holiday_excluded_from_age():
    hol = datetime(2026, 10, 20).date()
    ts = datetime(2026, 10, 19, 15, 30, tzinfo=IST)
    now = datetime(2026, 10, 21, 9, 15, tzinfo=IST)
    assert effective_age_seconds(ts, now, [hol]) == pytest.approx(17.75 * 3600)


def test_gift_nifty_gap_expectation():
    quotes = all_quotes()
    quotes["GIFTNIFTY"] = Quote.make("GIFTNIFTY", 25250.0, 25300.0, NOW - timedelta(minutes=2))
    assert gap_expectation(quotes, 25000.0) == pytest.approx(1.0)
    assert gap_expectation(quotes, None) is None
    snap = score_context(quotes, ContextSettings(), NOW, prev_nifty_close=25000.0)
    assert snap.inputs["gift_nifty"]["gap_expectation_pct"] == pytest.approx(1.0)
    assert snap.inputs["gift_nifty"]["contribution"] > 0      # gap-up overrides GIFT's own -0.2% change
    assert any("GIFT Nifty implies gap-up (+1.0%)" in r for r in snap.reasons)


def test_no_inputs_at_all_neutral_and_unusable():
    snap = score_context({}, ContextSettings(), NOW, event_risk=0.8)
    assert snap.macro_bias == 0.0 and not snap.usable
    assert set(snap.stale_required) == {"sp500_fut", "india_vix"}
    assert snap.event_risk == 0.8
    assert any("Event risk elevated" in r for r in snap.reasons)
    d = snapshot_to_dict(snap)
    assert d["usable"] is False and "inputs" in d


def test_future_timestamp_counts_as_fresh():
    quotes = all_quotes()
    quotes["ES=F"] = Quote.make("ES=F", 6000, 5990, NOW + timedelta(minutes=3))
    assert score_context(quotes, ContextSettings(), NOW).usable


async def test_engine_refresh_publishes_and_merges_freshest():
    clock = SimClock(NOW)
    bus = EventBus()
    got = []
    bus.subscribe(topics.CONTEXT, got.append)
    old = StaticProvider(all_quotes(chg=-1.0))
    newer = {"ES=F": Quote.make("ES=F", 6100, 6000, NOW)}
    eng = ContextEngine([old, StaticProvider(newer)], ContextSettings(), bus, clock,
                        event_risk_fn=lambda at: 0.4, prev_close_fn=lambda: 25000.0)
    snap = await eng.refresh()
    assert got and got[0] is snap and eng.latest is snap
    assert eng.quotes["ES=F"].price == 6100
    assert snap.event_risk == 0.4


async def test_engine_survives_failing_provider_and_keeps_cache():
    class Boom:
        name = "boom"

        async def fetch(self, symbols):
            raise RuntimeError("network down")

    clock = SimClock(NOW)
    static = StaticProvider(all_quotes())
    eng = ContextEngine([static, Boom()], ContextSettings(), None, clock)
    assert (await eng.refresh()).usable
    static.quotes.clear()                           # provider now returns nothing
    clock.advance(hours=30)
    snap = await eng.refresh()                      # cached quotes age into stale
    assert not snap.usable and "sp500_fut" in snap.stale_required


async def test_synthetic_provider_covers_all_inputs_and_moves():
    cfg = ContextSettings()
    syms = [i.symbol for i in cfg.inputs.values()]
    prov = SyntheticProvider(seed=1, now_fn=lambda: NOW)
    a = await prov.fetch(syms)
    b = await prov.fetch(syms)
    assert set(a) == set(syms)
    assert "GIFTNIFTY" in a and "^INDIAVIX" in a
    assert any(a[s].price != b[s].price for s in syms)
    assert 8 < a["^INDIAVIX"].price < 20 and 20000 < a["GIFTNIFTY"].price < 30000
    assert all(abs(x.change_pct) < 10 for x in b.values())
    eng = ContextEngine([prov], cfg, None, SimClock(NOW))
    assert (await eng.refresh()).usable
