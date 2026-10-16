"""Paper (simulated) broker: matches resting LIMIT / SL-L orders against fed ticks.

Fill rules
----------
* BUY LIMIT fills when the reference price (ask if present, else LTP) <= limit, at
  ``min(limit, ref + slippage)``; SELL LIMIT mirrors this (bid / LTP >= limit, at ``max(limit, ref - slip)``).
  A fill is never worse than the limit price.
* SL-L: BUY triggers when LTP >= trigger, SELL when LTP <= trigger; once triggered it behaves as a LIMIT at
  ``price`` (so a gap through the limit leaves it resting unfilled - exactly the real SL-L risk).
* An SL-L whose trigger is already breached at placement is rejected (as the exchange does).
* ``max_fill_qty_per_tick`` simulates partial fills.
* Marketable orders fill immediately against the last known tick.

Funds: ``available = capital + realised + min(unrealised, 0) - position margin - margin blocked by open
orders``. Margin model (configurable): futures 12% of notional, option buy = premium, option sell = 15% of
strike notional. Only the position-opening part of an order needs margin. Charges are not modelled here
(see ``backtest.costs``).
"""
from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from indexedge.broker.base import BrokerBase, BrokerError, TickHandler
from indexedge.core.clock import Clock
from indexedge.core.types import (
    Fill,
    Funds,
    Instrument,
    InstrumentKind,
    Order,
    OrderRequest,
    OrderStatus,
    OrderType,
    Position,
    Side,
    Tick,
)
from indexedge.data.store import empty_candles

log = logging.getLogger(__name__)

ALLOWED_ORDER_TYPES = (OrderType.LIMIT, OrderType.SL_LIMIT)
CandleSource = Callable[[str, str, str, datetime, datetime], pd.DataFrame]


@dataclass
class MarginModel:
    future_pct: float = 0.12
    option_sell_pct: float = 0.15

    def required(self, inst: Instrument, side: Side, qty: int, price: float) -> float:
        qty = abs(qty)
        if inst.kind is InstrumentKind.OPTION:
            if side is Side.BUY:
                return price * qty
            return self.option_sell_pct * (inst.strike or price) * qty
        return self.future_pct * price * qty


class PaperBroker(BrokerBase):
    name = "paper"

    def __init__(
        self,
        clock: Clock | None = None,
        capital: float = 200_000.0,
        slippage_ticks: int = 1,
        *,
        max_fill_qty_per_tick: int | None = None,
        margin: MarginModel | None = None,
        candle_source: CandleSource | None = None,
    ) -> None:
        self.clock = clock or Clock()
        self.capital = float(capital)
        self.slippage_ticks = slippage_ticks
        self.max_fill_qty_per_tick = max_fill_qty_per_tick
        self.margin = margin or MarginModel()
        self.candle_source = candle_source
        self._connected = False
        self._seq = 0
        self._orders: dict[str, Order] = {}
        self._fills: list[Fill] = []
        self._positions: dict[str, Position] = {}
        self._ticks: dict[str, Tick] = {}
        self._tick_subs: list[tuple[set[str], TickHandler]] = []

    # ------------------------------------------------------------------ session
    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------ market data
    async def subscribe_ticks(self, tokens: list[tuple[str, str]], handler: TickHandler) -> None:
        self._tick_subs.append(({str(t) for _, t in tokens}, handler))

    async def get_candles(self, exchange: str, token: str, interval: str, start: datetime,
                          end: datetime) -> pd.DataFrame:
        if self.candle_source is None:
            return empty_candles()
        return self.candle_source(exchange, token, interval, start, end)

    async def get_ltp(self, exchange: str, token: str, symbol: str = "") -> float:
        tick = self._ticks.get(str(token))
        if tick is None:
            raise BrokerError(f"no price yet for token {token}")
        return tick.ltp

    async def feed_tick(self, tick: Tick) -> None:
        """Advance the simulation: update LTP / positions, match orders, then dispatch to subscribers."""
        self._ticks[tick.token] = tick
        pos = self._positions.get(tick.token)
        if pos is not None:
            pos.ltp = tick.ltp
        for order in [o for o in self._orders.values()
                      if o.request.instrument.token == tick.token and not o.status.is_terminal]:
            await self._match(order, tick)
        for tokens, handler in list(self._tick_subs):
            if not tokens or tick.token in tokens:
                res = handler(tick)
                if hasattr(res, "__await__"):
                    await res

    # ------------------------------------------------------------------ orders
    def _next_id(self) -> str:
        self._seq += 1
        return f"PAPER{self._seq:08d}"

    async def _publish(self, order: Order) -> None:
        order.updated_at = self.clock.now()
        await self._emit_order(dataclasses.replace(order))

    async def _reject(self, order: Order, reason: str) -> Order:
        order.status = OrderStatus.REJECTED
        order.message = reason
        log.info("paper order %s rejected: %s", order.broker_id, reason)
        await self._publish(order)
        return order

    def _sl_error(self, req: OrderRequest, price: float, trigger: float | None) -> str | None:
        if req.order_type is not OrderType.SL_LIMIT:
            return None
        if not trigger or trigger <= 0:
            return "SL-L order requires a positive trigger price"
        if req.side is Side.BUY and price < trigger:
            return "BUY SL-L limit price must be >= trigger price"
        if req.side is Side.SELL and price > trigger:
            return "SELL SL-L limit price must be <= trigger price"
        return None

    def _opening_qty(self, inst: Instrument, side: Side, qty: int) -> int:
        net = self._positions[inst.token].net_qty if inst.token in self._positions else 0
        if net * side.sign < 0:
            return max(qty - abs(net), 0)
        return qty

    def _order_margin(self, order: Order) -> float:
        req = order.request
        opening = self._opening_qty(req.instrument, req.side, order.pending_qty)
        return self.margin.required(req.instrument, req.side, opening, req.price) if opening else 0.0

    def _position_margin(self, pos: Position) -> float:
        if not pos.net_qty:
            return 0.0
        side = Side.BUY if pos.net_qty > 0 else Side.SELL
        return self.margin.required(pos.instrument, side, pos.net_qty, pos.avg_price)

    async def place_order(self, req: OrderRequest) -> Order:
        now = self.clock.now()
        order = Order(request=req, broker_id=self._next_id(), created_at=now, updated_at=now)
        self._orders[order.broker_id] = order
        if not isinstance(req.order_type, OrderType) or req.order_type not in ALLOWED_ORDER_TYPES:
            return await self._reject(order, f"order type {req.order_type!r} not allowed")
        if req.quantity <= 0 or req.price <= 0:
            return await self._reject(order, "quantity and price must be positive")
        if req.instrument.lot_size > 1 and req.quantity % req.instrument.lot_size:
            return await self._reject(order, f"quantity must be a multiple of lot size {req.instrument.lot_size}")
        err = self._sl_error(req, req.price, req.trigger_price)
        if err:
            return await self._reject(order, err)
        last = self._ticks.get(req.instrument.token)
        if req.order_type is OrderType.SL_LIMIT and last is not None:
            trig = float(req.trigger_price or 0)
            if (req.side is Side.BUY and last.ltp >= trig) or (req.side is Side.SELL and last.ltp <= trig):
                return await self._reject(order, f"trigger price {trig} already breached (LTP {last.ltp})")
        need = self._order_margin(order)
        avail = (await self.funds()).available_cash
        if need > avail + 1e-6:
            return await self._reject(order, f"insufficient funds: margin {need:,.2f} > available {avail:,.2f}")
        order.status = OrderStatus.TRIGGER_PENDING if req.order_type is OrderType.SL_LIMIT else OrderStatus.OPEN
        await self._publish(order)
        if last is not None:
            await self._match(order, last)
        return order

    async def modify_order(self, order: Order, price: float, trigger_price: float | None = None) -> Order:
        stored = self._orders.get(order.broker_id)
        if stored is None:
            raise BrokerError(f"unknown order {order.broker_id}")
        if stored.status.is_terminal:
            stored.message = f"cannot modify: order already {stored.status.value}"
            return stored
        if price <= 0:
            raise BrokerError("price must be positive")
        trig = trigger_price if trigger_price is not None else stored.request.trigger_price
        err = self._sl_error(stored.request, price, trig)
        if err:
            raise BrokerError(err)
        stored.request = dataclasses.replace(stored.request, price=price, trigger_price=trig)
        stored.message = "modified"
        await self._publish(stored)
        last = self._ticks.get(stored.request.instrument.token)
        if last is not None:
            await self._match(stored, last)
        return stored

    async def cancel_order(self, order: Order) -> Order:
        """Cancel; a terminal order (e.g. filled just before) is returned unchanged with its final status."""
        stored = self._orders.get(order.broker_id)
        if stored is None:
            raise BrokerError(f"unknown order {order.broker_id}")
        if stored.status.is_terminal:
            return stored
        stored.status = OrderStatus.CANCELLED
        stored.message = "cancelled"
        await self._publish(stored)
        return stored

    # ------------------------------------------------------------------ matching
    async def _match(self, order: Order, tick: Tick) -> None:
        req = order.request
        changed = False
        if order.status is OrderStatus.TRIGGER_PENDING:
            trig = float(req.trigger_price or 0)
            hit = tick.ltp >= trig if req.side is Side.BUY else tick.ltp <= trig
            if not hit:
                return
            order.status = OrderStatus.OPEN
            order.message = "triggered"
            changed = True
        if order.status in (OrderStatus.OPEN, OrderStatus.PARTIAL):
            slip = self.slippage_ticks * req.instrument.tick_size
            px = None
            if req.side is Side.BUY:
                ref = tick.ask if tick.ask else tick.ltp
                if ref <= req.price:
                    px = min(req.price, ref + slip)
            else:
                ref = tick.bid if tick.bid else tick.ltp
                if ref >= req.price:
                    px = max(req.price, ref - slip)
            if px is not None:
                qty = order.pending_qty
                if self.max_fill_qty_per_tick:
                    qty = min(qty, self.max_fill_qty_per_tick)
                self._apply_fill(order, qty, round(px, 2), tick.ts)
                changed = True
        if changed:
            await self._publish(order)

    def _apply_fill(self, order: Order, qty: int, price: float, ts: datetime) -> None:
        req = order.request
        total = order.filled_qty + qty
        order.avg_price = (order.avg_price * order.filled_qty + price * qty) / total
        order.filled_qty = total
        order.status = OrderStatus.FILLED if order.pending_qty == 0 else OrderStatus.PARTIAL
        order.message = "filled" if order.status is OrderStatus.FILLED else "partially filled"
        self._fills.append(Fill(order.broker_id, req.instrument, req.side, qty, price, ts))
        self._update_position(req.instrument, req.side.sign * qty, price)

    def _update_position(self, inst: Instrument, dq: int, price: float) -> None:
        pos = self._positions.get(inst.token)
        if pos is None:
            pos = Position(inst, 0, 0.0)
            self._positions[inst.token] = pos
        n = pos.net_qty
        if n == 0 or (n > 0) == (dq > 0):
            new = n + dq
            pos.avg_price = (abs(n) * pos.avg_price + abs(dq) * price) / abs(new)
        else:
            closing = min(abs(dq), abs(n))
            pos.realised_pnl += closing * (price - pos.avg_price) * (1 if n > 0 else -1)
            new = n + dq
            if new != 0 and (new > 0) != (n > 0):
                pos.avg_price = price  # flipped: remainder opened at this price
        pos.net_qty = new
        last = self._ticks.get(inst.token)
        pos.ltp = last.ltp if last is not None else price

    # ------------------------------------------------------------------ books
    async def order_book(self) -> list[Order]:
        return list(self._orders.values())

    async def trade_book(self) -> list[Fill]:
        return list(self._fills)

    async def positions(self) -> list[Position]:
        return list(self._positions.values())

    async def funds(self) -> Funds:
        realised = sum(p.realised_pnl for p in self._positions.values())
        unrealised = sum(p.unrealised_pnl for p in self._positions.values())
        used = sum(self._position_margin(p) for p in self._positions.values())
        blocked = sum(self._order_margin(o) for o in self._orders.values()
                      if o.status in (OrderStatus.OPEN, OrderStatus.TRIGGER_PENDING, OrderStatus.PARTIAL))
        available = self.capital + realised + min(unrealised, 0.0) - used - blocked
        return Funds(available_cash=available, used_margin=used + blocked,
                     net=self.capital + realised + unrealised)
