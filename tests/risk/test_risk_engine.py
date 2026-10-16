from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from indexedge.config.settings import RiskSettings
from indexedge.core.clock import IST, SimClock
from indexedge.core.types import Funds, Instrument, InstrumentKind, MarketEvent, Position, Side
from indexedge.journal.db import Journal
from indexedge.journal.models import TradeRecord
from indexedge.risk.engine import RiskDecision, RiskEngine, TradeProposal

DAY = date(2026, 10, 15)  # Thursday
LOT = 75


def at(h: int, mi: int = 0, s: int = 0, d: date = DAY) -> datetime:
    return datetime(d.year, d.month, d.day, h, mi, s, tzinfo=IST)


@pytest.fixture
def journal():
    j = Journal(":memory:")
    yield j
    j.close()


@pytest.fixture
def clock():
    return SimClock(at(10, 30), holidays={date(2026, 10, 20)})


@pytest.fixture
def engine(journal, clock):
    return RiskEngine(RiskSettings(), clock, journal)


def prop(**kw) -> TradeProposal:
    # 10 pts x 75 = ₹750/lot; 1% of ₹2,00,000 = ₹2,000 -> 2 lots = 150
    base = {"underlying": "NIFTY", "side": Side.BUY, "entry": 25000.0, "stop": 24990.0, "lot_size": LOT}
    base.update(kw)
    return TradeProposal(**base)


def pos(lots: int, name: str = "NIFTY", pnl_per_unit: float = 0.0) -> Position:
    inst = Instrument(
        token=f"t{name}{lots}",
        symbol=f"{name}FUT",
        name=name,
        exchange="NFO",
        kind=InstrumentKind.FUTURE,
        lot_size=LOT,
    )
    return Position(instrument=inst, net_qty=lots * LOT, avg_price=100.0, ltp=100.0 + pnl_per_unit)


def add_trade(
    journal, entry: datetime, pnl: float | None, qty: int = LOT, exit_after_min: int = 5, mode: str = "paper"
) -> str:
    t = TradeRecord("s", "orb15", "NIFTY", "NIFTYFUT", "BUY", qty, entry, 25000.0, 24990.0, mode=mode)
    journal.open_trade(t)
    if pnl is not None:
        journal.close_trade(t.id, entry + timedelta(minutes=exit_after_min), 25000.0, "x", pnl=pnl)
    return t.id


def blocked(dec: RiskDecision, text: str) -> bool:
    return not dec.allowed and any(text in r for r in dec.reasons) and dec.qty == 0


# ------------------------------------------------------------------------------------------ sizing
def test_size_position(engine):
    assert engine.size_position(25000, 24990, LOT) == 150
    assert engine.size_position(25000, 25010, LOT) == 150  # short side symmetric
    assert engine.size_position(25000, 24970, LOT) == 0  # 30 pts x 75 = 2250 > 2000
    assert engine.size_position(25000, 24990, LOT, point_value=2.0) == 75
    assert engine.size_position(25000, 25000, LOT) == 0
    assert engine.size_position(25000, 24990, 0) == 0


def test_check_pass_auto_sized(engine):
    d = engine.check(prop(), [], None, 1.0, None)
    assert d.allowed and d.qty == 150 and d.reasons == [] and d.risk_amount == 1500


def test_premium_based_sizing(engine):
    d = engine.check(prop(premium=100.0, premium_stop=80.0))  # 20 x 75 = 1500/lot -> 1 lot
    assert d.allowed and d.qty == 75 and d.risk_amount == 1500
    d2 = engine.check(prop(premium=100.0))  # no premium stop -> whole premium at risk
    assert blocked(d2, "Stop too wide")
    d3 = engine.check(prop(premium=20.0, premium_stop=None))  # 20 x 75 = 1500 -> 1 lot
    assert d3.allowed and d3.qty == 75
    assert blocked(engine.check(prop(premium=50.0, premium_stop=50.0)), "Premium stop equals premium")


def test_invalid_stops_and_explicit_qty(engine):
    assert blocked(engine.check(prop(stop=25010.0)), "wrong side")
    assert blocked(engine.check(prop(side=Side.SELL, stop=24990.0)), "wrong side")
    assert blocked(engine.check(prop(stop=25000.0)), "Stop equals entry")
    assert blocked(engine.check(prop(qty=100)), "not a whole number of lots")
    assert blocked(engine.check(prop(qty=225)), "risks ₹2,250")
    ok = engine.check(prop(qty=75))
    assert ok.allowed and ok.qty == 75


# ------------------------------------------------------------------------------------ session window
@pytest.mark.parametrize(
    "t,ok,text",
    [
        (at(9, 14, 59), False, "Market is closed"),
        (at(9, 15), False, "first 5 min"),
        (at(9, 19, 59), False, "first 5 min"),
        (at(9, 20), True, ""),
        (at(14, 59, 59), True, ""),
        (at(15, 0), False, "last 20 min"),
        (at(15, 19, 59), False, "last 20 min"),
        (at(15, 20), False, "cutoff 15:20"),
        (at(15, 25), False, "cutoff 15:20"),
        (at(15, 30), False, "Market is closed"),
    ],
)
def test_session_window_boundaries(engine, clock, t, ok, text):
    clock.set(t)
    d = engine.check(prop())
    assert d.allowed is ok, d.reasons
    if not ok:
        assert blocked(d, text)


def test_weekend_and_holiday(engine, clock):
    clock.set(at(11, 0, d=date(2026, 10, 17)))  # Saturday
    assert blocked(engine.check(prop()), "weekend or exchange holiday")
    clock.set(at(11, 0, d=date(2026, 10, 20)))  # configured holiday
    assert blocked(engine.check(prop()), "weekend or exchange holiday")
    clock.set(at(11, 0, d=date(2026, 10, 19)))  # Monday
    assert engine.check(prop()).allowed


def test_custom_cutoff(journal, clock):
    eng = RiskEngine(RiskSettings(no_trade_last_min=0), clock, journal, cutoff="15:10")
    clock.set(at(15, 9, 59))
    assert eng.check(prop()).allowed
    clock.set(at(15, 10))
    assert blocked(eng.check(prop()), "cutoff 15:10")


# ------------------------------------------------------------------------------------ blackout / feed
def test_event_blackout(engine):
    ev = MarketEvent(id="e1", ts=at(10, 40), title="RBI policy", category="rbi", impact="high")
    d = engine.check(prop(), blackout=ev)
    assert blocked(d, "Event blackout: RBI policy at 10:40")
    assert engine.check(prop(), blackout=None).allowed


@pytest.mark.parametrize(
    "age,ok", [(0.0, True), (10.0, True), (10.01, False), (None, False), (float("inf"), False)]
)
def test_stale_feed(engine, age, ok):
    d = engine.check(prop(), feed_age_s=age)
    assert d.allowed is ok
    if not ok:
        assert blocked(d, "feed is stale")


# ------------------------------------------------------------------------------------ day-level locks
def test_max_daily_loss_locks(engine, journal, clock):
    add_trade(journal, at(9, 30), -5000.0, qty=150)  # under the ₹6,000 limit
    clock.set(at(10, 30))
    d = engine.check(prop())
    assert d.allowed and d.qty == 75  # budget reduced to ₹1,000 -> 1 lot (750)
    assert any("Risk budget reduced" in n for n in d.notes)
    add_trade(journal, at(10, 0), -1000.0)  # realised -6000 -> lock
    clock.set(at(11, 0))
    assert blocked(engine.check(prop()), "Max daily loss hit")
    assert engine.locked


def test_unrealised_counts_toward_daily_loss(engine):
    # realised 0, open position -₹6,000 (80 per unit x 75)
    d = engine.check(prop(underlying="BANKNIFTY"), [pos(1, pnl_per_unit=-80.0)])
    assert blocked(d, "including open positions")
    assert not engine.locked  # unrealised alone does not lock the day
    assert engine.check(prop(underlying="BANKNIFTY"), [pos(1, pnl_per_unit=-10.0)]).allowed


def test_max_trades_per_day(engine, journal, clock):
    for i in range(4):
        add_trade(journal, at(9, 30 + i * 5), 100.0, exit_after_min=1)
    assert engine.check(prop()).allowed
    add_trade(journal, at(10, 0), 100.0, exit_after_min=1)
    d = engine.check(prop())
    assert blocked(d, "Max trades per day reached (5/5)")
    assert engine.locked


def test_backtest_trades_do_not_count(engine, journal):
    for i in range(6):
        add_trade(journal, at(9, 30 + i), -5000.0, mode="backtest", exit_after_min=1)
    assert engine.check(prop()).allowed


def test_consecutive_losses(engine, journal, clock):
    add_trade(journal, at(9, 30), -100.0)
    add_trade(journal, at(9, 40), -100.0)
    clock.set(at(11, 0))
    assert engine.check(prop()).allowed
    add_trade(journal, at(10, 0), -100.0)
    assert blocked(engine.check(prop()), "3 consecutive losses")


def test_kill_switch_lock_persists_across_restart_and_lapses_next_day(tmp_path, clock):
    path = tmp_path / "j.duckdb"
    j = Journal(path)
    RiskEngine(RiskSettings(), clock, j).lock_day("Kill switch pressed")
    j.close()
    j2 = Journal(path)
    eng = RiskEngine(RiskSettings(), clock, j2)
    assert eng.locked and eng.lock_reason == "Kill switch pressed"
    assert blocked(eng.check(prop()), "Trading is locked for today: Kill switch pressed")
    eng.lock_day("another")  # first reason kept
    assert eng.lock_reason == "Kill switch pressed"
    clock.set(at(10, 30, d=date(2026, 10, 16)))
    assert not eng.locked and eng.check(prop()).allowed
    j2.close()


# ---------------------------------------------------------------------------- positions / exposure
def test_max_open_positions(engine):
    assert engine.check(prop(), [pos(1, "BANKNIFTY")]).allowed
    d = engine.check(prop(), [pos(1, "BANKNIFTY"), pos(1, "FINNIFTY")])
    assert blocked(d, "Max open positions reached (2/2)")
    flat = pos(1, "FINNIFTY")
    flat.net_qty = 0  # closed positions are ignored
    assert engine.check(prop(), [pos(1, "BANKNIFTY"), flat]).allowed


def test_per_index_lot_exposure(engine):
    d = engine.check(prop(), [pos(3)])
    assert d.allowed and d.qty == 75 and any("lot exposure" in n for n in d.notes)
    assert blocked(engine.check(prop(), [pos(4)]), "exposure limit reached (4/4 lots open)")
    assert blocked(engine.check(prop(qty=150), [pos(3)]), "lot exposure limit")
    assert engine.check(prop(underlying="BANKNIFTY"), [pos(4)]).allowed  # other index unaffected


# --------------------------------------------------------------------------------------- margin
def test_margin_check(engine):
    p = prop(premium=100.0, premium_stop=80.0)  # 75 x ₹100 = ₹7,500
    assert blocked(engine.check(p, funds=Funds(available_cash=7_499.0)), "Insufficient margin")
    assert engine.check(p, funds=Funds(available_cash=7_500.0)).allowed
    fut = prop(margin_per_lot=150_000.0)  # 2 lots -> ₹3,00,000
    assert blocked(engine.check(fut, funds=Funds(available_cash=299_999.0)), "need ₹300,000")
    assert engine.check(fut, funds=Funds(available_cash=300_000.0)).allowed
    assert blocked(engine.check(prop(), funds=Funds(available_cash=1e9)), "Margin requirement unknown")
    assert engine.check(prop(), funds=None).allowed


# ------------------------------------------------------------------------------- tilt protection
def test_post_loss_cooldown_boundary(engine, journal, clock):
    add_trade(journal, at(9, 50), -100.0, exit_after_min=10)  # loss exit at 10:00
    clock.set(at(10, 9, 59))
    assert blocked(engine.check(prop(qty=75)), "Cooling down after a loss")
    clock.set(at(10, 10))
    assert engine.check(prop(qty=75)).allowed


def test_revenge_size_block(engine, journal, clock):
    add_trade(journal, at(9, 30), -100.0, qty=75)
    clock.set(at(11, 0))
    assert blocked(engine.check(prop(qty=150)), "Revenge-size block")
    assert engine.check(prop(qty=75)).allowed
    auto = engine.check(prop())
    assert auto.allowed and auto.qty == 75 and any("revenge" in n for n in auto.notes)
    # a winning trade closes -> block lifted
    add_trade(journal, at(10, 0), 500.0, exit_after_min=5)
    assert engine.check(prop(qty=150)).allowed


def test_revenge_multiplier(journal, clock):
    eng = RiskEngine(RiskSettings(revenge_size_multiplier=2.0), clock, journal)
    add_trade(journal, at(9, 30), -100.0, qty=75)
    clock.set(at(11, 0))
    assert eng.check(prop(qty=150)).allowed
    eng2 = RiskEngine(RiskSettings(revenge_size_multiplier=0.5), clock, journal)
    assert blocked(eng2.check(prop()), "below one lot")


# ------------------------------------------------------------------------------------ edit lock
@pytest.mark.parametrize(
    "t,ok",
    [
        (at(8, 59), True),
        (at(9, 0), False),
        (at(12, 0), False),
        (at(15, 29, 59), False),
        (at(15, 30), True),
        (at(12, 0, d=date(2026, 10, 18)), True),  # Sunday
    ],
)
def test_can_edit_limits(engine, clock, t, ok):
    clock.set(t)
    assert engine.can_edit_limits() is ok


def test_can_edit_limits_when_lock_disabled(journal, clock):
    eng = RiskEngine(RiskSettings(lock_edits_during_market=False), clock, journal)
    assert eng.can_edit_limits()


def test_multiple_reasons_and_status(engine, journal, clock):
    clock.set(at(15, 5))
    d = engine.check(prop(), feed_age_s=60)
    assert not d.allowed and len(d.reasons) >= 2
    add_trade(journal, at(10, 0), -1200.0)
    st = engine.status([pos(1, pnl_per_unit=-8.0)])
    assert st["daily_pnl"] == -1800.0 and st["trades_today"] == 1 and st["max_trades"] == 5
    assert st["daily_loss_used_pct"] == 30.0 and st["locked"] is False
