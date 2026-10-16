"""Regression tests for execution safety defects: restart double stops, stray entry fills, naked orphan
positions, kill switch racing in-flight exits, safe-mode kinds and handler detach."""
from __future__ import annotations

import asyncio

import pytest
from exec_harness import flat_costs

from indexedge.core.types import OrderRequest, OrderStatus, OrderType, Side
from indexedge.execution.orders import OrderManager
from indexedge.execution.trades import TradeManager


async def _enter(h, ltp=25050.0):
    await h.tick(h.fut, ltp)
    res = await h.tm.execute(h.signal())
    assert res.ok, res.reason
    return h.tm.trades[res.trade_id]


async def _resting_stops(h):
    return [o for o in await h.broker.order_book()
            if o.request.order_type is OrderType.SL_LIMIT and not o.status.is_terminal]


async def _net(h):
    return sum(p.net_qty for p in await h.broker.positions())


async def _restart(h) -> TradeManager:
    """Fresh TradeManager over the same broker + journal (old one detached, tick feed re-pointed)."""
    h.tm.detach()
    h.broker._tick_subs.clear()
    tm2 = TradeManager(h.om, h.broker, h.journal, h.risk, h.clock, h.bus, h.settings, cost_fn=flat_costs)
    await h.broker.subscribe_ticks([], tm2.on_tick)
    return tm2


# ------------------------------------------------------------------------------------- 1. restart double stop
async def test_restart_adopts_resting_stop_instead_of_placing_a_second_one(make_harness):
    h = await make_harness()
    t = await _enter(h)
    old = t.stop_order
    assert old.status is OrderStatus.TRIGGER_PENDING
    tm2 = await _restart(h)                      # old DAY stop is still resting at the broker
    out = await tm2.reconcile()
    assert out["adopted"] == [t.trade_id] and out["ok"]
    t2 = tm2.trades[t.trade_id]
    assert t2.stop_order is not None and t2.stop_order.broker_id == old.broker_id
    stops = await _resting_stops(h)
    assert [o.broker_id for o in stops] == [old.broker_id]
    assert sum(o.pending_qty for o in stops) == 260
    # idempotent
    await tm2.reconcile()
    assert len(await _resting_stops(h)) == 1
    # a tick through the stop must flatten, never flip to a naked short
    await h.tick(h.fut, 25029)
    assert await _net(h) == 0
    assert t2.closed and h.journal.trade(t.trade_id)["outcome"] == "stop"
    assert not await _resting_stops(h)


async def test_restart_with_duplicate_stops_keeps_one(make_harness):
    h = await make_harness()
    t = await _enter(h)
    dup = await h.om.place_stop(h.fut, Side.SELL, 260, 25030.0, tag=t.tag)
    assert dup.status is OrderStatus.TRIGGER_PENDING
    tm2 = await _restart(h)
    await tm2.reconcile()
    stops = await _resting_stops(h)
    assert len(stops) == 1 and stops[0].pending_qty == 260
    assert tm2.trades[t.trade_id].stop_order.broker_id == stops[0].broker_id
    await h.tick(h.fut, 25029)
    assert await _net(h) == 0


async def test_restart_resizes_adopted_stop_to_position(make_harness):
    h = await make_harness()
    t = await _enter(h)
    h.tm.detach()
    # 130 sold outside the manager while it was down: the old 260 stop would over-sell
    await h.broker.place_order(OrderRequest(h.fut, Side.SELL, 130, OrderType.LIMIT, 25040.0))
    assert await _net(h) == 130
    tm2 = await _restart(h)
    await tm2.reconcile()
    stops = await _resting_stops(h)
    assert len(stops) == 1 and stops[0].pending_qty == 130
    assert tm2.trades[t.trade_id].qty_open == 130
    await h.tick(h.fut, 25029)
    assert await _net(h) == 0


async def test_reconcile_cancels_stale_stop_of_flat_trade(make_harness):
    h = await make_harness()
    t = await _enter(h)
    h.tm.detach()
    await h.broker.place_order(OrderRequest(h.fut, Side.SELL, 260, OrderType.LIMIT, 25040.0))
    assert await _net(h) == 0
    tm2 = await _restart(h)
    out = await tm2.reconcile()
    assert t.trade_id in out["closed"]
    assert not await _resting_stops(h)          # leftover stop on a flat position would open a short


# ------------------------------------------------------------------------------------- 2. stray entries
async def test_late_fill_of_uncancellable_entry_is_adopted_with_stop(h):
    await h.tick(h.fut, 25050, ask=25100)       # entry limit cannot fill
    orig_cancel = h.broker.cancel_order

    async def flaky_cancel(order):
        if order.request.order_type is OrderType.LIMIT:
            raise ConnectionError("cancel timed out")
        return await orig_cancel(order)

    h.broker.cancel_order = flaky_cancel
    sig = h.signal()
    res = await h.tm.execute(sig)
    assert not res.ok and h.tm.safe_mode and h.tm.safe_mode_kind == "unknown_order"
    entry = res.entry_order
    assert entry.status is OrderStatus.OPEN and h.tm.trades == {}
    h.broker.cancel_order = orig_cancel

    await h.tick(h.fut, 25050, ask=25050)       # the stray entry fills later
    assert await _net(h) == 260
    [t] = h.tm.open_trades
    assert t.qty_open == 260 and t.signal_id == sig.id
    assert t.stop_order is not None and t.stop_order.status is OrderStatus.TRIGGER_PENDING
    assert t.stop_order.request.quantity == 260
    assert t.stop_order.request.trigger_price == pytest.approx(25030.0)
    assert h.journal.trade(t.trade_id) is not None
    assert any(topic == "alert" and "entry" in p["title"].lower() for topic, p in h.events)
    await h.tick(h.fut, 25029)
    assert t.closed and await _net(h) == 0


async def test_late_partial_fill_grows_trade_and_stop(make_harness):
    h = await make_harness(max_fill=65)
    await h.tick(h.fut, 25050)                  # first 65 fill at placement
    orig_cancel = h.broker.cancel_order
    block = {"on": False}

    async def flaky_cancel(order):
        if block["on"] and order.request.order_type is OrderType.LIMIT:
            raise ConnectionError("cancel timed out")
        return await orig_cancel(order)

    h.broker.cancel_order = flaky_cancel

    async def no_more_fills():                  # chase sleeps: market moves away, cancel will fail
        block["on"] = True

    h.hooks.append(no_more_fills)
    res = await h.tm.execute(h.signal())
    h.hooks.clear()
    assert res.ok
    t = h.tm.trades[res.trade_id]
    assert t.qty_open == 65 and res.entry_order.status is OrderStatus.PARTIAL
    block["on"] = False
    await h.tick(h.fut, 25050)                  # another 65 of the stray remainder fills
    assert await _net(h) == 130
    assert t.qty_open == 130
    stops = await _resting_stops(h)
    assert sum(o.pending_qty for o in stops) == 130


async def test_reconcile_protects_naked_orphan_position(h):
    await h.tick(h.fut, 25050)
    await h.broker.place_order(OrderRequest(h.fut, Side.BUY, 65, OrderType.LIMIT, 25051.0))
    out = await h.tm.reconcile()
    assert out["orphans"] == [{"symbol": h.fut.symbol, "qty": 65}]
    stops = await _resting_stops(h)
    assert len(stops) == 1
    s = stops[0]
    assert s.request.side is Side.SELL and s.pending_qty == 65
    assert s.request.trigger_price == pytest.approx(25050 * 0.99, abs=0.05)
    assert any(topic == "alert" and p["level"] == "critical" for topic, p in h.events)
    await h.tm.reconcile()                      # idempotent
    assert len(await _resting_stops(h)) == 1
    await h.tick(h.fut, 24790)
    assert await _net(h) == 0


# ------------------------------------------------------------------------------------- 3. kill switch race
async def test_kill_switch_waits_for_in_flight_exit(h):
    t = await _enter(h)
    state = {"n": 0, "ks": None}

    async def hook():
        state["n"] += 1
        if state["n"] == 1:                     # exit is chasing: hit the kill switch now
            state["ks"] = asyncio.create_task(h.tm.kill_switch())
        elif state["n"] == 3:                   # the slow exit finally fills
            await h.tick(h.fut, 25095, bid=25095)

    h.hooks.append(hook)
    await h.tick(h.fut, 25095, bid=25000)       # final target: exit limit rests (no bid)
    out = await state["ks"]
    h.hooks.clear()
    assert out["ok"]
    assert t.closed and await _net(h) == 0
    sold = sum(o.filled_qty for o in await h.broker.order_book()
               if o.request.side is Side.SELL and o.request.order_type is OrderType.LIMIT)
    assert sold == 260
    assert not [o for o in await h.broker.order_book() if not o.status.is_terminal]
    await h.tick(h.fut, 25029)
    assert await _net(h) == 0
    assert not t.lock.locked()


async def test_kill_switch_times_out_on_stuck_exit_and_still_flattens_once(h):
    t = await _enter(h)
    h.tm.kill_lock_timeout_s = 0.05
    release = asyncio.Event()
    state = {"n": 0, "ks": None}

    async def hook():
        state["n"] += 1
        if state["n"] == 1:
            state["ks"] = asyncio.create_task(h.tm.kill_switch())
            await release.wait()                # the exit path hangs (e.g. broker API stalled)
        elif state["n"] == 2:                   # square-off chase: market comes back
            await h.tick(h.fut, 25095, bid=25095)

    h.hooks.append(hook)
    exit_task = asyncio.create_task(h.tick(h.fut, 25095, bid=25000))
    while state["ks"] is None:
        await asyncio.sleep(0)
    out = await state["ks"]
    release.set()
    await exit_task
    h.hooks.clear()
    assert out["ok"] and t.closed
    assert await _net(h) == 0
    sold = sum(o.filled_qty for o in await h.broker.order_book() if o.request.side is Side.SELL)
    assert sold == 260
    assert not [o for o in await h.broker.order_book() if not o.status.is_terminal]
    assert len(h.journal.trades()) == 1 and h.journal.trades().iloc[0]["outcome"] == "kill_switch"


# ------------------------------------------------------------------------------------- 4. safe mode kinds
async def test_safe_mode_kinds_only_cleared_by_matching_kind(h):
    await h.tm.enter_safe_mode("stop placement failed", kind="stop_failure")
    assert h.tm.safe_mode and h.tm.safe_mode_kind == "stop_failure"
    assert h.tm.exit_safe_mode("stale_feed") is False
    assert h.tm.safe_mode and h.tm.safe_mode_reason == "stop placement failed"
    await h.tm.enter_safe_mode("feed stale", kind="stale_feed")
    assert h.tm.safe_mode_kind == "stale_feed"
    h.tm.exit_safe_mode("stale_feed")             # feed recovered: the stop failure must remain
    assert h.tm.safe_mode and h.tm.safe_mode_kind == "stop_failure"
    assert h.tm.exit_safe_mode("stop_failure") is True
    assert not h.tm.safe_mode and h.tm.safe_mode_kind == "" and h.tm.safe_mode_reason == ""
    await h.tm.enter_safe_mode("operator")
    assert h.tm.safe_mode_kind == "manual"
    h.tm.exit_safe_mode()
    assert not h.tm.safe_mode


async def test_stop_failure_sets_stop_failure_kind(h):
    await h.tick(h.fut, 25050)

    async def fail_stop(inst, side, qty, trigger, tag=""):
        from indexedge.core.types import Order

        req = OrderRequest(inst, side, qty, OrderType.SL_LIMIT, trigger - 1, trigger_price=trigger)
        return Order(req, broker_id="", status=OrderStatus.REJECTED, message="place failed: socket")

    h.om.place_stop = fail_stop
    res = await h.tm.execute(h.signal())
    assert res.ok and h.tm.safe_mode and h.tm.safe_mode_kind == "stop_failure"


# ------------------------------------------------------------------------------------- 5. detach
async def test_detach_unregisters_order_handlers(h):
    handlers = h.broker._order_handlers
    assert h.tm._on_order in handlers and h.om._on_update in handlers
    h.tm.detach()
    h.om.detach()
    h.tm.detach()                                 # idempotent
    assert h.tm._on_order not in handlers and h.om._on_update not in handlers
    om2 = OrderManager(h.broker, h.limiter, h.settings, h.journal, bus=h.bus)
    tm2 = TradeManager(om2, h.broker, h.journal, h.risk, h.clock, h.bus, h.settings)
    assert len(handlers) == 2 and tm2._on_order in handlers
