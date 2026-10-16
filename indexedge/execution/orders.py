"""Order placement with marketable limits, chasing, SL-L stops and a shared rate limiter.

Rules (spec §8, SEBI / Angel One algo framework):

* Only ``LIMIT`` and ``STOPLOSS_LIMIT`` orders are ever built; every request passes
  :func:`indexedge.execution.guard.assert_allowed_order` before it reaches the broker.
* Every place / modify / cancel goes through one shared :class:`~indexedge.broker.rate_limiter.RateLimiter`
  (``settings.execution.max_orders_per_second``, default 8/s).
* **Marketable limit** = LTP ± ``limit_buffer_ticks`` × tick size (BUY rounded up, SELL rounded down to the tick).
* **Chase**: if not filled, every ``chase_interval_s`` the limit is re-priced from the latest LTP via *modify*,
  at most ``chase_max_attempts`` times; then whatever is still pending is cancelled. A partial fill is returned
  as-is (``filled_qty`` < requested) and the caller proceeds with the filled quantity.
* **SL-L**: trigger = stop (rounded to tick), limit = trigger ∓ ``sl_limit_buffer_pct`` % (SELL stop below,
  BUY stop above), so a fast move still fills while a pathological gap leaves the order resting (real SL-L risk).
* Broker errors / disconnects never raise out of this module: a failed placement returns an ``Order`` with
  ``status=REJECTED`` and the error in ``message``; a failed modify / cancel refreshes the order from the order
  book and leaves the decision to the caller (the trade manager enters safe mode and reconciles).

``sleep`` and the limiter are injectable so tests drive time deterministically.
"""
from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable
from typing import Any

from indexedge.broker.base import BrokerBase
from indexedge.broker.rate_limiter import RateLimiter
from indexedge.core import bus as topics
from indexedge.core.types import (
    Fill,
    Instrument,
    Order,
    OrderRequest,
    OrderStatus,
    OrderType,
    Side,
)
from indexedge.execution.guard import assert_allowed_order

log = logging.getLogger(__name__)

Sleep = Callable[[float], Awaitable[None]]


def round_to_tick(price: float, tick: float, mode: str = "nearest") -> float:
    """Round ``price`` to a multiple of ``tick`` (``mode`` = nearest / up / down)."""
    tick = tick if tick and tick > 0 else 0.05
    x = price / tick
    n = math.ceil(x - 1e-9) if mode == "up" else math.floor(x + 1e-9) if mode == "down" else round(x)
    return round(max(n, 1) * tick, 2)


def marketable_price(side: Side, ltp: float, tick: float, buffer_ticks: int) -> float:
    """LTP ± buffer ticks, rounded away from LTP to the tick grid."""
    if side is Side.BUY:
        return round_to_tick(ltp + buffer_ticks * tick, tick, "up")
    return round_to_tick(ltp - buffer_ticks * tick, tick, "down")


def sl_limit_price(side: Side, trigger: float, tick: float, buffer_pct: float) -> float:
    """SL-L limit beyond the trigger: SELL stop below, BUY stop above (always at least one tick away)."""
    off = max(trigger * buffer_pct / 100.0, tick)
    if side is Side.SELL:
        return round_to_tick(trigger - off, tick, "down")
    return round_to_tick(trigger + off, tick, "up")


class OrderManager:
    def __init__(
        self,
        broker: BrokerBase,
        limiter: RateLimiter | None,
        settings: Any,
        journal: Any = None,
        *,
        bus: Any = None,
        sleep: Sleep | None = None,
    ) -> None:
        self.broker = broker
        self.exec = getattr(settings, "execution", settings)
        self.limiter = limiter or RateLimiter(self.exec.max_orders_per_second)
        self.journal = journal
        self.bus = bus
        self._sleep: Sleep = sleep or asyncio.sleep
        self._tracked: dict[str, Order] = {}
        self._seen: dict[str, tuple[int, float]] = {}     # broker_id -> (filled_qty, avg_price) journaled
        self._done: dict[str, asyncio.Event] = {}
        broker.on_order_update(self._on_update)

    # ------------------------------------------------------------------------------ bookkeeping
    async def _on_update(self, upd: Order) -> None:
        bid = upd.broker_id
        tracked = self._tracked.get(bid)
        if tracked is not None and tracked is not upd:
            tracked.status, tracked.filled_qty = upd.status, upd.filled_qty
            tracked.avg_price, tracked.message = upd.avg_price, upd.message
            tracked.updated_at = upd.updated_at
            tracked.request = upd.request
        self._journal_order(upd)
        if upd.status.is_terminal and bid in self._done:
            self._done[bid].set()
        if self.bus is not None:
            await self.bus.publish(topics.ORDER, upd)

    def _journal_order(self, order: Order) -> None:
        if self.journal is None or not order.broker_id:
            return
        try:
            self.journal.record_order(order)
            prev_q, prev_avg = self._seen.get(order.broker_id, (0, 0.0))
            dq = order.filled_qty - prev_q
            if dq > 0:
                px = (order.avg_price * order.filled_qty - prev_avg * prev_q) / dq
                req = order.request
                ts = order.updated_at or order.created_at
                self.journal.record_fill(Fill(order.broker_id, req.instrument, req.side, dq, round(px, 2), ts))
                self._seen[order.broker_id] = (order.filled_qty, order.avg_price)
        except Exception:
            log.exception("journal write failed for order %s", order.broker_id)

    def _track(self, order: Order) -> Order:
        if order.broker_id:
            self._tracked[order.broker_id] = order
            self._journal_order(order)
        return order

    async def _refresh(self, order: Order) -> Order:
        """Re-read ``order`` from the broker's order book (best effort)."""
        try:
            for o in await self.broker.order_book():
                if o.broker_id == order.broker_id:
                    if o is not order:
                        order.status, order.filled_qty, order.avg_price = o.status, o.filled_qty, o.avg_price
                        order.message = o.message
                    break
        except Exception as exc:  # noqa: BLE001
            log.warning("order book refresh failed: %s", exc)
        return order

    # ------------------------------------------------------------------------------ primitives
    async def ltp(self, inst: Instrument, fallback: float | None = None) -> float | None:
        try:
            px = float(await self.broker.get_ltp(inst.exchange, inst.token, inst.symbol))
            return px if px > 0 else fallback
        except Exception as exc:  # noqa: BLE001
            log.debug("ltp unavailable for %s: %s", inst.symbol, exc)
            return fallback

    async def place(self, req: OrderRequest) -> Order:
        assert_allowed_order(req)
        await self.limiter.acquire()
        try:
            order = await self.broker.place_order(req)
        except Exception as exc:  # noqa: BLE001 - disconnect / API error -> treated as a rejection
            log.error("place_order failed for %s: %s", req.instrument.symbol, exc)
            return Order(req, broker_id="", status=OrderStatus.REJECTED, message=f"place failed: {exc}")
        return self._track(order)

    async def modify(self, order: Order, price: float, trigger_price: float | None = None) -> Order:
        await self.limiter.acquire()
        try:
            res = await self.broker.modify_order(order, price, trigger_price)
        except Exception as exc:  # noqa: BLE001
            log.warning("modify %s failed: %s", order.broker_id, exc)
            order.message = f"modify failed: {exc}"
            return await self._refresh(order)
        if res is not order:
            order.status, order.filled_qty, order.avg_price = res.status, res.filled_qty, res.avg_price
            order.request, order.message = res.request, res.message
        return order

    async def cancel(self, order: Order) -> Order:
        """Cancel ``order``; returns it with its final status (FILLED if it filled before the cancel)."""
        if not order.broker_id or order.status.is_terminal:
            return order
        await self.limiter.acquire()
        try:
            res = await self.broker.cancel_order(order)
        except Exception as exc:  # noqa: BLE001
            log.warning("cancel %s failed: %s", order.broker_id, exc)
            order.message = f"cancel failed: {exc}"
            return await self._refresh(order)
        if res is not order:
            order.status, order.filled_qty, order.avg_price = res.status, res.filled_qty, res.avg_price
            order.message = res.message
        return order

    async def _wait(self, order: Order, timeout: float) -> None:
        """Wait ``timeout`` seconds or until the order reaches a terminal state, whichever is first."""
        if order.status.is_terminal:
            return
        ev = self._done.setdefault(order.broker_id, asyncio.Event())
        sleeper = asyncio.ensure_future(self._sleep(timeout))
        waiter = asyncio.ensure_future(ev.wait())
        try:
            await asyncio.wait({sleeper, waiter}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in (sleeper, waiter):
                if not t.done():
                    t.cancel()

    # ------------------------------------------------------------------------------ high level
    async def chase(self, side: Side, inst: Instrument, qty: int, ref_price: float | None,
                    tag: str = "") -> Order:
        """Marketable LIMIT, re-priced up to ``chase_max_attempts`` times, then the remainder is cancelled."""
        ltp = await self.ltp(inst, ref_price)
        if ltp is None or ltp <= 0:
            req_px = ref_price if ref_price and ref_price > 0 else inst.tick_size
            req = OrderRequest(inst, side, qty, OrderType.LIMIT, req_px, tag=tag[:19])
            return Order(req, broker_id="", status=OrderStatus.REJECTED, message="no price available")
        tick = inst.tick_size
        buf = int(self.exec.limit_buffer_ticks)
        req = OrderRequest(inst, side, qty, OrderType.LIMIT, marketable_price(side, ltp, tick, buf), tag=tag[:19])
        order = await self.place(req)
        if order.status is OrderStatus.REJECTED:
            return order
        attempts = 0
        try:
            while not order.status.is_terminal:
                await self._wait(order, float(self.exec.chase_interval_s))
                if order.status.is_terminal:
                    break
                if attempts >= int(self.exec.chase_max_attempts):
                    order = await self.cancel(order)
                    break
                attempts += 1
                ltp = await self.ltp(inst, ltp)
                new_px = marketable_price(side, ltp, tick, buf)
                if abs(new_px - order.request.price) >= tick / 2:
                    order = await self.modify(order, new_px)
        finally:
            self._done.pop(order.broker_id, None)
        if not order.status.is_terminal:
            log.error("order %s still %s after chase (cancel failed?)", order.broker_id, order.status.value)
        return order

    async def enter(self, side: Side, instrument: Instrument, qty: int, ref_price: float | None,
                    tag: str = "") -> Order:
        return await self.chase(side, instrument, qty, ref_price, tag)

    async def exit_position(self, side: Side, instrument: Instrument, qty: int, ref_price: float | None,
                            tag: str = "") -> Order:
        return await self.chase(side, instrument, qty, ref_price, tag)

    async def place_stop(self, instrument: Instrument, side: Side, qty: int, trigger: float,
                         tag: str = "") -> Order:
        """SL-L order on ``side`` (SELL protects a long) with trigger = stop."""
        tick = instrument.tick_size
        trig = round_to_tick(trigger, tick)
        price = sl_limit_price(side, trig, tick, float(self.exec.sl_limit_buffer_pct))
        req = OrderRequest(instrument, side, qty, OrderType.SL_LIMIT, price, trigger_price=trig, tag=tag[:19])
        return await self.place(req)

    async def modify_stop(self, order: Order, trigger: float) -> Order:
        inst = order.request.instrument
        trig = round_to_tick(trigger, inst.tick_size)
        price = sl_limit_price(order.request.side, trig, inst.tick_size, float(self.exec.sl_limit_buffer_pct))
        return await self.modify(order, price, trig)

    async def cancel_all(self) -> list[Order]:
        """Cancel every open / trigger-pending order at the broker; returns the orders acted on."""
        try:
            book = await self.broker.order_book()
        except Exception as exc:  # noqa: BLE001
            log.error("order book unavailable for cancel_all: %s", exc)
            return []
        out = []
        for o in book:
            if not o.status.is_terminal:
                tracked = self._tracked.get(o.broker_id, o)
                out.append(await self.cancel(tracked))
        return out

    async def square_off_all(self) -> list[Order]:
        """Close every open broker position with chased marketable limits (concurrently, rate-limited)."""
        try:
            positions = [p for p in await self.broker.positions() if p.net_qty != 0]
        except Exception as exc:  # noqa: BLE001
            log.error("positions unavailable for square-off: %s", exc)
            return []
        jobs = [
            self.chase(Side.SELL if p.net_qty > 0 else Side.BUY, p.instrument, abs(p.net_qty),
                       p.ltp or p.avg_price, tag="squareoff")
            for p in positions
        ]
        return list(await asyncio.gather(*jobs))
