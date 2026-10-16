"""Kill switch, reconcile after disconnect, rate limiting."""
from __future__ import annotations

from datetime import timedelta

import pytest
from exec_harness import at

from indexedge.core.types import Instrument, InstrumentKind, OrderRequest, OrderStatus, OrderType, Side


async def _enter(h, ltp=25050.0):
    await h.tick(h.fut, ltp)
    res = await h.tm.execute(h.signal())
    assert res.ok, res.reason
    return h.tm.trades[res.trade_id]


async def test_kill_switch_cancels_squares_off_and_locks(h):
    t = await _enter(h)
    # an extra resting order that must be cancelled too
    extra = await h.om.place(OrderRequest(h.fut, Side.BUY, 65, OrderType.LIMIT, 24000.0))
    assert extra.status is OrderStatus.OPEN
    await h.tick(h.fut, 25040)
    out = await h.tm.kill_switch()
    assert out == {"ok": True, "cancelled": 2, "squared_off": 1}
    assert extra.status is OrderStatus.CANCELLED and t.stop_order is None
    assert t.closed
    row = h.journal.trades().iloc[0]
    assert row["outcome"] == "kill_switch" and row["pnl"] == pytest.approx(-10 * 260)
    assert all(p.net_qty == 0 for p in await h.broker.positions())
    assert h.risk.locked and "Kill switch" in h.risk.lock_reason
    res = await h.tm.execute(h.signal())
    assert not res.ok and "locked" in res.reason
    # square-off used a chased marketable LIMIT, never anything else
    orders = h.journal.orders()
    assert set(orders["order_type"]) <= {"LIMIT", "STOPLOSS_LIMIT"}


async def test_reconcile_replaces_missing_stop_after_disconnect(h):
    t = await _enter(h)
    old = t.stop_order
    # simulate a dropped order-update stream: the stop disappears at the broker unnoticed
    h.broker._order_handlers.remove(h.tm._on_order)
    await h.broker.cancel_order(old)
    h.broker._order_handlers.append(h.tm._on_order)
    assert old.status is OrderStatus.CANCELLED and not t.closed

    out = await h.tm.reconcile()
    assert out["ok"] and out["stops_replaced"] == [t.trade_id] and out["closed"] == []
    new = t.stop_order
    assert new is not old and new.status is OrderStatus.TRIGGER_PENDING
    assert new.request.quantity == 260 and new.request.trigger_price == pytest.approx(25030.0)
    # idempotent
    again = await h.tm.reconcile()
    assert again["stops_replaced"] == [] and t.stop_order is new
    await h.tick(h.fut, 25000)
    assert t.closed


async def test_reconcile_closes_trade_whose_stop_filled_while_disconnected(h):
    t = await _enter(h)
    h.broker._order_handlers.remove(h.tm._on_order)
    await h.tick(h.fut, 25028)                                  # stop fills, update missed
    h.broker._order_handlers.append(h.tm._on_order)
    out = await h.tm.reconcile()
    assert t.closed and t.trade_id in out["closed"]
    row = h.journal.trades().iloc[0]
    assert row["outcome"] == "stop" and row["pnl"] == pytest.approx(-22 * 260)


async def test_reconcile_position_flattened_externally(h):
    t = await _enter(h)
    h.broker._order_handlers.remove(h.tm._on_order)
    await h.broker.cancel_order(t.stop_order)
    await h.broker.place_order(OrderRequest(h.fut, Side.SELL, 260, OrderType.LIMIT, 25040.0))
    h.broker._order_handlers.append(h.tm._on_order)
    out = await h.tm.reconcile()
    assert t.trade_id in out["closed"]
    row = h.journal.trades().iloc[0]
    assert row["outcome"] == "reconciled" and row["exit_price"] == pytest.approx(25050.0)


async def test_reconcile_reports_orphan_positions(h):
    await h.tick(h.fut, 25050)
    await h.broker.place_order(OrderRequest(h.fut, Side.BUY, 65, OrderType.LIMIT, 25051.0))
    out = await h.tm.reconcile()
    assert out["orphans"] == [{"symbol": h.fut.symbol, "qty": 65}]


async def test_reconcile_adopts_open_journal_trade_after_restart(make_harness):
    h = await make_harness()
    t = await _enter(h)
    # restart: a fresh TradeManager over the same broker + journal, no in-memory state
    from indexedge.execution.trades import TradeManager

    h.broker._order_handlers.remove(h.tm._on_order)
    await h.broker.cancel_order(t.stop_order)
    tm2 = TradeManager(h.om, h.broker, h.journal, h.risk, h.clock, h.bus, h.settings)
    out = await tm2.reconcile()
    assert out["adopted"] == [t.trade_id] and out["stops_replaced"] == [t.trade_id]
    assert tm2.trades[t.trade_id].stop_order.status is OrderStatus.TRIGGER_PENDING


async def test_rate_limit_respected_for_burst_of_orders(h):
    await h.tick(h.fut, 25050)
    inst = h.fut
    import asyncio

    start = h.clock.now()
    orders = await asyncio.gather(*[h.om.place_stop(inst, Side.SELL, 65, 25000 - i) for i in range(20)])
    assert all(o.status is OrderStatus.TRIGGER_PENDING for o in orders)
    times = [ts for ts, name in h.calls if name == "place_order"]
    assert len(times) == 20
    for i, ts in enumerate(times):
        window = [x for x in times[i:] if x - ts < timedelta(seconds=1)]
        assert len(window) <= 8
    assert h.clock.now() - start >= timedelta(seconds=2)
    cancelled = await h.om.cancel_all()
    assert len(cancelled) == 20
    ctimes = [ts for ts, name in h.calls if name == "cancel_order"]
    for i, ts in enumerate(ctimes):
        assert len([x for x in ctimes[i:] if x - ts < timedelta(seconds=1)]) <= 8


async def test_kill_switch_squares_off_untracked_positions(make_harness):
    h = await make_harness()
    h.clock.set(at(10, 0))
    for i in range(3):
        inst = Instrument(f"x{i}", f"NIFTYX{i}", "NIFTY", "NFO", InstrumentKind.FUTURE, lot_size=65)
        await h.tick(inst, 100.0 + i)
        await h.broker.place_order(OrderRequest(inst, Side.BUY, 65, OrderType.LIMIT, 101.0 + i))
    out = await h.tm.kill_switch()
    assert out["ok"] and out["squared_off"] == 3 and out["cancelled"] == 0
    assert all(p.net_qty == 0 for p in await h.broker.positions())
