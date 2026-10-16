from __future__ import annotations

import pytest
from exec_harness import at

from indexedge.core.types import OrderStatus, OrderType, Side


async def _enter(h, ltp=25050.0):
    await h.tick(h.fut, ltp)
    sig = h.signal()
    res = await h.tm.execute(sig)
    assert res.ok, res.reason
    return sig, res, h.tm.trades[res.trade_id]


async def test_full_lifecycle_entry_stop_t1_breakeven_target(h):
    _sig, res, t = await _enter(h)
    assert res.entry_order.request.order_type is OrderType.LIMIT
    assert res.entry_order.request.price == pytest.approx(25050.2)        # ltp + 4 ticks
    assert t.qty_open == 260 and t.entry_price == pytest.approx(25050.0)  # 4 lots (per-index cap)
    so = t.stop_order
    assert so.request.order_type is OrderType.SL_LIMIT and so.status is OrderStatus.TRIGGER_PENDING
    assert so.request.side is Side.SELL and so.request.quantity == 260
    assert so.request.trigger_price == pytest.approx(25030.0)             # same 20-pt risk on the future
    assert so.request.price < so.request.trigger_price
    assert t.targets == [pytest.approx(25070.0), pytest.approx(25090.0)]
    sigs = h.journal.signals()
    assert sigs.iloc[0]["status"] == "taken"

    await h.tick(h.fut, 25071)                                            # T1
    assert t.t1_done and t.breakeven and t.qty_open == 130
    assert t.stop_order.request.quantity == 130
    assert t.stop_order.request.trigger_price == pytest.approx(25050.0)
    assert so.status is OrderStatus.CANCELLED
    pos = (await h.broker.positions())[0]
    assert pos.net_qty == 130

    await h.tick(h.fut, 25095)                                            # final target
    assert t.closed and t.qty_open == 0
    assert (await h.broker.positions())[0].net_qty == 0
    row = h.journal.trades().iloc[0]
    assert row["status"] == "closed" and row["outcome"] == "target"
    assert row["pnl"] == pytest.approx(130 * 21 + 130 * 45)
    assert row["costs"] == pytest.approx(100.0)
    assert row["mfe"] == pytest.approx(45.0)
    # no resting orders left behind
    assert not [o for o in await h.broker.order_book() if not o.status.is_terminal]
    kinds = [p["event"] for topic, p in h.events if topic == "trade"]
    assert kinds == ["opened", "partial", "closed"]
    assert any(topic == "order" for topic, _ in h.events)
    assert len(h.journal.fills()) == 3


async def test_stop_out_detected_from_order_update(h):
    _, _, t = await _enter(h)
    await h.tick(h.fut, 25029)
    assert t.closed
    row = h.journal.trades().iloc[0]
    assert row["outcome"] == "stop"
    assert row["pnl"] == pytest.approx(-21 * 260)
    assert row["r_multiple"] == pytest.approx(-21 / 20)
    assert (await h.broker.positions())[0].net_qty == 0
    assert h.journal.today_stats(h.clock.today()).consecutive_losses == 1


async def test_breakeven_stop_after_t1(h):
    _, _, t = await _enter(h)
    await h.tick(h.fut, 25072)
    await h.tick(h.fut, 25049)                                          # back through breakeven
    assert t.closed
    row = h.journal.trades().iloc[0]
    assert row["outcome"] == "stop"
    assert row["pnl"] == pytest.approx(130 * 22 + 130 * (-1))


async def test_forced_square_off_before_cutoff(h):
    _, _, t = await _enter(h)
    h.clock.set(at(15, 14, 59))
    await h.tick(h.fut, 25055)
    assert not t.closed
    h.clock.set(at(15, 15))                                              # 15:20 cutoff - 5 min
    await h.tick(h.fut, 25056)
    assert t.closed
    assert h.journal.trades().iloc[0]["outcome"] == "square_off"
    assert (await h.broker.positions())[0].net_qty == 0


async def test_square_off_on_timer_without_ticks(h):
    _, _, t = await _enter(h)
    h.clock.set(at(15, 16))
    await h.tm.on_timer()
    assert t.closed and h.journal.trades().iloc[0]["outcome"] == "square_off"


async def test_time_exit(h):
    await h.tick(h.fut, 25050)
    sig = h.signal(time_exit="11:00")
    res = await h.tm.execute(sig)
    t = h.tm.trades[res.trade_id]
    h.clock.set(at(11, 0))
    await h.tick(h.fut, 25052)
    assert t.closed and h.journal.trades().iloc[0]["outcome"] == "time"


async def test_option_trade_uses_delta_half_premium_levels(make_harness):
    h = await make_harness(preferred="option")
    sig = h.signal(side=Side.SELL, entry=25000, stop=25020, targets=[24980, 24960])
    inst = sig.instrument
    assert inst.option_type.value == "PE" and inst.strike == 25000.0
    await h.tick(h.fut, 25050)
    await h.tick(inst, 120.0)
    res = await h.tm.execute(sig)
    assert res.ok, res.reason
    t = h.tm.trades[res.trade_id]
    assert t.side is Side.BUY                         # options are always bought
    assert t.stop == pytest.approx(110.0) and t.targets == [pytest.approx(130.0), pytest.approx(140.0)]
    # premium sizing: 10 premium x 65 = 650/lot, budget 10,000 -> 15 lots, capped at 4 lots
    assert t.qty_open == 260
    assert t.stop_order.request.trigger_price == pytest.approx(110.0)


async def test_entry_partial_fill_proceeds_with_filled_qty(make_harness):
    h = await make_harness(max_fill=65)
    await h.tick(h.fut, 25050)

    async def wide_market():
        await h.tick(h.fut, 25050, bid=25040, ask=25060)

    h.hooks.append(wide_market)
    sig = h.signal()
    res = await h.tm.execute(sig)
    assert res.ok
    assert res.entry_order.status is OrderStatus.CANCELLED and res.entry_order.filled_qty == 65
    t = h.tm.trades[res.trade_id]
    assert t.qty_open == 65 and t.initial_qty == 65
    assert t.stop_order.request.quantity == 65
    assert (await h.broker.positions())[0].net_qty == 65
    assert h.journal.trades().iloc[0]["qty"] == 65


async def test_chase_then_cancel_when_never_filled(h):
    await h.tick(h.fut, 25050, ask=25070)
    state = {"ltp": 25050.0}

    async def drift_up():
        state["ltp"] += 1
        await h.tick(h.fut, state["ltp"], ask=state["ltp"] + 20)

    h.hooks.append(drift_up)
    sig = h.signal()
    res = await h.tm.execute(sig)
    assert not res.ok and "not filled" in res.reason
    o = res.entry_order
    assert o.status is OrderStatus.CANCELLED and o.filled_qty == 0
    names = [n for _, n in h.calls]
    assert names.count("modify_order") == h.settings.execution.chase_max_attempts
    assert names[-1] == "cancel_order"
    assert o.request.price == pytest.approx(state["ltp"] - 1 + 0.2)     # re-priced from latest LTP
    assert h.tm.trades == {}
    assert h.journal.signals().iloc[0]["status"] == "expired"
    assert (await h.broker.positions()) == []


async def test_risk_rejection_recorded_on_signal(make_harness):
    h = await make_harness(start=at(9, 16))
    await h.tick(h.fut, 25050)
    sig = h.signal()
    res = await h.tm.execute(sig)
    assert not res.ok and res.reason.startswith("Risk check:")
    row = h.journal.signals().iloc[0]
    assert row["status"] == "rejected" and "first 5 min" in row["status_reason"]
    assert [n for _, n in h.calls] == []


async def test_broker_disconnect_on_place_is_a_rejection(h):
    await h.tick(h.fut, 25050)

    async def boom(req):
        raise ConnectionError("socket closed")

    h.broker.place_order = boom
    res = await h.tm.execute(h.signal())
    assert not res.ok and "socket closed" in res.reason
    assert h.journal.signals().iloc[0]["status"] == "rejected"
    assert h.tm.trades == {}


async def test_stop_rejection_after_gap_exits_immediately(h):
    await h.tick(h.fut, 25050)
    orig = h.om.place_stop

    async def gap_then_stop(inst, side, qty, trigger, tag=""):
        await h.tick(h.fut, 25020)               # market gaps through the stop before it is placed
        return await orig(inst, side, qty, trigger, tag)

    h.om.place_stop = gap_then_stop
    res = await h.tm.execute(h.signal())
    t = h.tm.trades[res.trade_id]
    assert t.closed and h.journal.trades().iloc[0]["outcome"] == "stop"
    assert (await h.broker.positions())[0].net_qty == 0


async def test_safe_mode_blocks_entries_but_keeps_stops(h):
    _, _, t = await _enter(h)
    await h.tm.enter_safe_mode("feed stale")
    res = await h.tm.execute(h.signal())
    assert not res.ok and res.reason == "Safe mode: feed stale"
    assert t.stop_order.status is OrderStatus.TRIGGER_PENDING
    await h.tick(h.fut, 25029)
    assert t.closed                                    # stop still works in safe mode
    assert any(topic == "alert" for topic, _ in h.events)


async def test_blocked_signal_is_not_executed(h):
    await h.tick(h.fut, 25050)
    sig = h.signal()
    sig.status, sig.blocked_reason = "blocked", "Strategy not validated for live signals"
    res = await h.tm.execute(sig)
    assert not res.ok and res.reason == "Strategy not validated for live signals"
    assert h.calls == []
