from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pytest

from indexedge.config.settings import Settings
from indexedge.core.clock import IST, SimClock
from indexedge.core.types import (
    ContextSnapshot,
    InstrumentKind,
    MarketEvent,
    NewsItem,
    Regime,
    Side,
    StrategySignal,
)
from indexedge.data.instruments import InstrumentMaster
from indexedge.data.synthetic import synthetic_master_rows
from indexedge.journal.db import Journal
from indexedge.models.calibration import wilson_ci
from indexedge.risk.engine import RiskEngine
from indexedge.signals.composer import SignalComposer
from indexedge.signals.serialize import SIGNAL_JSON_KEYS, signal_to_json

DAY = date(2026, 10, 16)
TS = datetime(2026, 10, 16, 10, 30, tzinfo=IST)
STATS = {"wins": 60, "n": 100, "badges": ["validated"]}


def costs(kind, qty, entry, exit):
    return 150.0


class FakeStrategy:
    name = "Opening Range Breakout"
    regimes = frozenset({Regime.TREND_UP, Regime.TREND_DOWN})


class FakeCalendar:
    def __init__(self, event=None):
        self.event = event

    def in_blackout(self, at):
        return self.event


class FakeBundle:
    def __init__(self):
        self.usable = True
        self.metrics = {"n": 400}

    def predict_proba(self, X):
        return np.array([0.62])

    def top_features(self, row, k=5):
        return [("rsi_14", 0.31), ("adx", -0.12)]


class FakeRegistry:
    def __init__(self, bundle):
        self.bundle = bundle

    def get(self, sid):
        return self.bundle


@pytest.fixture
def env():
    settings = Settings()
    settings.execution.preferred_instrument = "future"
    clock = SimClock(TS)
    j = Journal(":memory:")
    risk = RiskEngine(settings.risk, clock, j)
    master = InstrumentMaster.from_json(synthetic_master_rows(DAY, spots={"NIFTY": 25000}))
    yield settings, risk, master
    j.close()


def raw(side=Side.BUY, **kw):
    base = {"strategy_id": "orb15", "underlying": "NIFTY", "ts": TS, "side": side, "entry": 25000.0,
            "stop": 24980.0 if side is Side.BUY else 25020.0,
            "targets": [25020.0, 25040.0] if side is Side.BUY else [24980.0, 24960.0],
            "reasons": ["Close above opening range high 24,995"]}
    base.update(kw)
    return StrategySignal(**base)


def ctx(bias=0.42, risk_off=False, stale=None, reasons=None):
    return ContextSnapshot(TS, bias, risk_off, 0.0, {}, reasons or ["S&P 500 fut +0.8% (bullish)"],
                           stale_required=stale or [])


def make(env, calendar=None, registry=None, **kw):
    settings, risk, master = env
    return SignalComposer(settings, registry, risk, calendar, master,
                          strategies={"orb15": FakeStrategy()}, cost_fn=costs, **kw)


def test_active_signal_with_historical_probability_and_ev(env):
    sig = make(env).compose(raw(), None, ctx(), STATS, Regime.TREND_UP)
    assert sig.status == "active" and sig.blocked_reason == ""
    assert sig.probability == pytest.approx(0.6)
    lo, hi = wilson_ci(60, 100)
    assert sig.probability_ci == (pytest.approx(lo, abs=1e-4), pytest.approx(hi, abs=1e-4))
    assert sig.sample_size == 100
    assert sig.instrument.kind is InstrumentKind.FUTURE and sig.instrument.expiry == date(2026, 10, 27)
    # 1% of 2,00,000 = 2,000; 20 pts x 65 = 1,300/lot -> 1 lot
    assert sig.quantity == 65
    assert sig.expected_rr == pytest.approx(2.0)                      # to the final target
    assert sig.ev_per_trade == pytest.approx(0.6 * 40 * 65 - 0.4 * 20 * 65 - 150)
    assert sig.context_reasons[0] == "Global cues bullish (+0.42) — aligned with long"
    assert "validated" in sig.badges


def test_context_against_short_and_neutral(env):
    c = make(env)
    assert c.compose(raw(Side.SELL), None, ctx(0.42), STATS).context_reasons[0] == \
        "Global cues bullish (+0.42) — against short"
    assert c.compose(raw(), None, ctx(0.05), STATS).context_reasons[0] == "Global cues neutral (+0.05)"


def test_gate_context_missing_or_stale(env):
    c = make(env)
    s = c.compose(raw(), None, None, STATS, Regime.TREND_UP)
    assert s.status == "blocked"
    assert s.blocked_reason == "External context unavailable or stale: no context snapshot"
    s = c.compose(raw(), None, ctx(stale=["sp500_fut", "india_vix"]), STATS, Regime.TREND_UP)
    assert s.blocked_reason == "External context unavailable or stale: sp500_fut, india_vix"
    assert s.probability == pytest.approx(0.6)                 # still fully composed for study mode


def test_gate_risk_off(env):
    s = make(env).compose(raw(), None, ctx(-0.7, risk_off=True, reasons=["India VIX 24.1 (risk-off)"]),
                          STATS, Regime.TREND_UP)
    assert s.status == "blocked" and s.blocked_reason == "Risk-off: India VIX 24.1 (risk-off)"


def test_gate_event_blackout(env):
    ev = MarketEvent("rbi", TS, "RBI policy decision", "rbi", "high")
    s = make(env, calendar=FakeCalendar(ev)).compose(raw(), None, ctx(), STATS, Regime.TREND_UP)
    assert s.blocked_reason == "Event blackout: RBI policy decision"


def test_gate_regime(env):
    s = make(env).compose(raw(), None, ctx(), STATS, Regime.RANGE)
    assert s.blocked_reason == "Regime range not suitable for Opening Range Breakout"


def test_gate_not_validated(env):
    settings = env[0]
    settings.strategies.enabled["orb15"] = False
    s = make(env).compose(raw(), None, ctx(), STATS, "trend_up")
    assert s.status == "blocked" and s.blocked_reason == "Strategy not validated for live signals"
    settings.strategies.enabled["orb15"] = True
    assert make(env).compose(raw(), None, ctx(), STATS, "trend_up").status == "active"


def test_gate_order_context_first(env):
    settings = env[0]
    settings.strategies.enabled["orb15"] = False
    ev = MarketEvent("rbi", TS, "RBI", "rbi", "high")
    s = make(env, calendar=FakeCalendar(ev)).compose(raw(), None, ctx(risk_off=True), STATS, Regime.RANGE)
    assert s.blocked_reason.startswith("Risk-off")


def test_insufficient_data_gives_no_probability(env):
    s = make(env).compose(raw(), None, ctx(), {"wins": 30, "n": 49}, Regime.TREND_UP)
    assert s.probability is None and s.probability_ci is None and s.sample_size == 49
    assert "insufficient_data" in s.badges and s.ev_per_trade == 0.0
    s = make(env).compose(raw(), None, ctx(), None, Regime.TREND_UP)
    assert s.probability is None and s.sample_size == 0


def test_model_probability_used_when_usable(env):
    s = make(env, registry=FakeRegistry(FakeBundle())).compose(raw(), {"rsi_14": 61.0}, ctx(), STATS,
                                                               Regime.TREND_UP)
    assert s.probability == pytest.approx(0.62) and s.sample_size == 400
    assert s.top_features == [("rsi_14", 0.31), ("adx", -0.12)]
    lo, hi = s.probability_ci
    assert lo < 0.62 < hi


def test_unusable_model_falls_back_to_history(env):
    b = FakeBundle()
    b.usable = False
    s = make(env, registry=FakeRegistry(b)).compose(raw(), {"rsi_14": 61.0}, ctx(), STATS, Regime.TREND_UP)
    assert s.probability == pytest.approx(0.6) and s.top_features == []


def test_negative_ev_badge(env):
    s = make(env).compose(raw(), None, ctx(), {"wins": 20, "n": 100}, Regime.TREND_UP)
    assert s.ev_per_trade < 0 and "negative_expectancy_warning" in s.badges


def test_option_instrument_atm_ce_pe_nearest_expiry(env):
    settings = env[0]
    settings.execution.preferred_instrument = "option"
    c = make(env)
    long_ = c.compose(raw(entry=25012.0, stop=24992.0), None, ctx(), STATS, Regime.TREND_UP)
    inst = long_.instrument
    assert inst.kind is InstrumentKind.OPTION and inst.option_type.value == "CE"
    assert inst.strike == 25000.0 and inst.expiry == date(2026, 10, 20)
    # delta 0.5: 20 pts -> 10 premium x 65 = 650/lot -> 3 lots for 2,000
    assert long_.quantity == 195
    short = c.compose(raw(Side.SELL), None, ctx(), STATS, Regime.TREND_UP)
    assert short.instrument.option_type.value == "PE"


def test_option_rolls_on_expiry_day(env):
    settings = env[0]
    settings.execution.preferred_instrument = "option"
    ts = datetime(2026, 10, 20, 10, 0, tzinfo=IST)
    s = make(env).compose(raw(ts=ts), None, ctx(), STATS, Regime.TREND_UP)
    assert s.instrument.expiry == date(2026, 10, 27)


def test_option_moneyness_itm(env):
    settings = env[0]
    settings.execution.preferred_instrument = "option"
    settings.execution.option_moneyness = "ITM1"
    s = make(env).compose(raw(), None, ctx(), STATS, Regime.TREND_UP)
    assert s.instrument.strike == 24950.0
    p = make(env).compose(raw(Side.SELL), None, ctx(), STATS, Regime.TREND_UP)
    assert p.instrument.strike == 25050.0


def test_no_master_means_no_instrument(env):
    settings, risk, _ = env
    s = SignalComposer(settings, None, risk, None, None, cost_fn=costs).compose(raw(), None, ctx(), STATS)
    assert s.instrument is None and s.quantity == 100   # lot size 1 fallback: 2,000 / 20


def test_news_reasons(env):
    news = [NewsItem("n1", TS, "x", "RBI hikes repo rate", "u", sentiment=-0.6, relevance=0.9, impact="high"),
            NewsItem("n2", TS, "x", "Cricket score", "u", relevance=0.1)]
    s = make(env).compose(raw(), None, ctx(), STATS, Regime.TREND_UP, news=news)
    assert s.news_reasons == ["High-impact news (negative): RBI hikes repo rate"]


def test_signal_to_json_matches_contract(env):
    s = make(env, registry=FakeRegistry(FakeBundle())).compose(raw(), {"rsi_14": 1.0}, ctx(), STATS,
                                                               Regime.TREND_UP)
    js = signal_to_json(s, "Opening Range Breakout")
    assert tuple(js) == SIGNAL_JSON_KEYS
    assert js["side"] == "BUY" and js["regime"] == "trend_up" and js["status"] == "active"
    assert js["ts"] == "2026-10-16T10:30:00+05:30"
    assert js["top_features"] == [["rsi_14", 0.31], ["adx", -0.12]]
    assert isinstance(js["probability_ci"], list) and len(js["probability_ci"]) == 2
    assert js["instrument"]["symbol"] == s.instrument.symbol and js["instrument"]["kind"] == "future"
    assert js["strategy_name"] == "Opening Range Breakout"
    import json

    json.dumps(js)   # fully JSON serialisable
    blocked = signal_to_json(make(env).compose(raw(), None, None, {"n": 3, "wins": 1}))
    assert blocked["probability"] is None and blocked["probability_ci"] is None
    assert blocked["status"] == "blocked" and blocked["strategy_name"] == "orb15"
