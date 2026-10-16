"""Trade lifecycle manager: risk check → entry → protective stop → management → exit → journal.

Lifecycle
---------
1. ``execute(signal)``: refuses blocked signals and anything while in *safe mode*; runs
   :meth:`RiskEngine.check` (the rejection reason is written to the signal row in the journal, status
   ``rejected``); enters with a chased marketable LIMIT; proceeds with whatever filled (partial fills are fine).
2. The trade is journaled the moment an entry fill exists, then an **SL-L is placed immediately** for the filled
   quantity. If the exchange rejects it because price already went through the stop, the position is exited at
   once; if the stop cannot be placed at all (API error / disconnect) the manager enters safe mode and keeps
   retrying on every tick / timer / reconcile.
3. ``on_tick``: first target ⇒ book 50 % (whole lots) and move the stop to breakeven; final target ⇒ exit;
   ``time_exit`` ⇒ exit; at ``cutoff − square_off_minutes_before_cutoff`` ⇒ forced square-off.
   Stop fills are detected from broker order updates.
4. On close: gross P&L, costs (cost function), MFE/MAE into the journal; ``RiskEngine.refresh_locks``.

Price translation (stop / targets are on the *underlying* in the signal):

* future: same point distances applied to the actual fill (``fill + (level − signal.entry)``);
* option (always bought — CE for long, PE for short): **delta ≈ 0.5** approximation,
  ``premium ± 0.5·|level − signal.entry|`` (stop below, targets above the fill premium).

Consistency rules for review focus #3 (partial fills / rejections / disconnects):

* Every path that places / cancels / modifies orders for a trade (exit, T1 booking, stop placement / move,
  reconcile, kill switch) holds that trade's ``asyncio.Lock`` (``ManagedTrade.lock``), so two of them can never
  work the same position concurrently. Tick / timer management skips a trade whose lock is held.
* Exits always cancel the protective stop first; if that cancel reports the stop already FILLED the trade is
  closed as a stop-out instead (no double exit). If the cancel fails the exit is aborted and safe mode entered.
* A partially filled exit re-places a stop for the remainder and retries the exit on the next tick.
* Changing the stop *quantity* (after partial booking) is cancel + new SL-L (brokers cannot modify quantity of
  a stop reliably); moving only the trigger uses modify.
* Stop orders carry the trade tag (``trade_id[:19]``; the client order id starts with ``trade_id[:16]``) so they
  can be recognised in the broker order book.
* ``reconcile()`` compares broker positions with managed trades, closes trades whose position vanished (using the
  broker trade book for the exit price), shrinks/grows quantities to the broker's, re-places missing stops,
  adopts open journal trades after a restart **together with their still-resting DAY stop** (one is kept and
  resized to the position, duplicates cancelled; a new stop is only placed if none rests), cancels stale /
  surplus stops so the resting stop quantity on an instrument never exceeds the position, and protects
  untracked (orphan) positions with an SL-L ``orphan_stop_pct`` % away (critical alert).
* **Stray entries**: an entry order whose cancel could not be confirmed is remembered; if it fills later the fill
  is *adopted* into a managed trade (new, or the partially filled one) and protected with an SL-L at the signal's
  stop (translated to the actual fill) — adopting is preferred over flattening because it needs one order
  (the stop) instead of a chased exit that could itself get stuck, and the risk was already approved.
* **Kill switch** first acquires every open trade's lock (``kill_lock_timeout_s``, default 20 s). For trades still
  busy after the timeout it cancels their outstanding LIMIT (exit) orders, waits briefly again, and then proceeds:
  cancel all → square off → lock the day. While it runs no new stops / exits are started.
* Safe mode has *kinds* (``stale_feed`` / ``stop_failure`` / ``unknown_order`` / ``reconcile`` / ``manual``);
  several may be active at once and :meth:`TradeManager.exit_safe_mode` clears only the given kind.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from indexedge.core import bus as topics
from indexedge.core.clock import IST, Clock, parse_hhmm
from indexedge.core.types import (
    Instrument,
    InstrumentKind,
    Order,
    OrderStatus,
    OrderType,
    Side,
    Signal,
    Tick,
    new_id,
)
from indexedge.execution.orders import OrderManager, round_to_tick
from indexedge.journal.models import TradeRecord
from indexedge.risk.engine import RiskEngine, TradeProposal
from indexedge.signals.costs import CostFn, default_cost_fn

log = logging.getLogger(__name__)

OPTION_DELTA = 0.5
SAFE_MODE_KINDS = ("stale_feed", "stop_failure", "unknown_order", "reconcile", "manual")
ORPHAN_TAG = "orphan"
_LIVE = (OrderStatus.OPEN, OrderStatus.TRIGGER_PENDING, OrderStatus.PARTIAL)


@dataclass
class ExecutionResult:
    ok: bool
    reason: str = ""
    trade_id: str | None = None
    entry_order: Order | None = None
    stop_order: Order | None = None


@dataclass
class ManagedTrade:
    trade_id: str
    signal_id: str | None
    strategy_id: str
    underlying: str
    instrument: Instrument
    side: Side                       # side of the *instrument* position (BUY = long)
    entry_price: float
    initial_qty: int
    qty_open: int
    stop: float
    targets: list[float]
    entry_ts: datetime
    time_exit: datetime | None = None
    stop_order: Order | None = None
    t1_done: bool = False
    breakeven: bool = False
    stop_missing: bool = False
    pending_exit: str | None = None
    closed: bool = False
    exits: list[tuple[int, float]] = field(default_factory=list)
    stop_seen: dict[str, tuple[int, float]] = field(default_factory=dict)
    mfe: float = 0.0
    mae: float = 0.0
    last_ltp: float | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)

    @property
    def busy(self) -> bool:
        """True while an exit / partial booking / stop change / reconcile / kill switch holds the trade."""
        return self.lock.locked()

    @property
    def kind(self) -> str:
        return "option" if self.instrument.kind is InstrumentKind.OPTION else "future"

    @property
    def tag(self) -> str:
        return self.trade_id[:19]

    def owns(self, order: Order) -> bool:
        """Whether ``order`` was placed for this trade (by request tag or tag-derived client id)."""
        req = order.request
        prefix = self.trade_id[:16]
        return any(x and (x == self.tag or x.startswith(prefix)) for x in (req.tag, req.client_id))

    def gross_pnl(self) -> float:
        return sum(self.side.sign * (px - self.entry_price) * q for q, px in self.exits)

    def avg_exit(self) -> float:
        q = sum(q for q, _ in self.exits)
        return sum(q * px for q, px in self.exits) / q if q else self.entry_price

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.trade_id, "signal_id": self.signal_id, "strategy_id": self.strategy_id,
            "underlying": self.underlying, "instrument_symbol": self.instrument.symbol, "side": self.side.value,
            "qty": self.initial_qty, "qty_open": self.qty_open, "entry_price": self.entry_price,
            "entry_ts": self.entry_ts.isoformat(), "stop": self.stop, "targets": self.targets,
            "t1_done": self.t1_done, "breakeven": self.breakeven, "stop_missing": self.stop_missing,
            "ltp": self.last_ltp, "status": "closed" if self.closed else "open",
            "pnl": round(self.gross_pnl() + (self.last_ltp - self.entry_price) * self.side.sign * self.qty_open, 2)
            if self.last_ltp is not None else round(self.gross_pnl(), 2),
        }


@dataclass
class _OrphanEntry:
    """An entry order whose cancel could not be confirmed: any later fill must be adopted and protected."""

    order_id: str
    signal: Signal
    trade_id: str
    side: Side
    instrument: Instrument
    planned_entry: float
    seen_qty: int = 0
    seen_avg: float = 0.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


async def _try_lock(lock: asyncio.Lock, timeout: float) -> bool:
    if not lock.locked():
        await lock.acquire()
        return True
    try:
        await asyncio.wait_for(lock.acquire(), max(timeout, 0.001))
        return True
    except TimeoutError:
        return False


class TradeManager:
    def __init__(
        self,
        order_mgr: OrderManager,
        broker: Any,
        journal: Any,
        risk: RiskEngine,
        clock: Clock,
        bus: Any,
        settings: Any,
        *,
        calendar: Any = None,
        cost_fn: CostFn | None = None,
        future_margin_pct: float = 0.12,
        kill_lock_timeout_s: float | None = None,
        reconcile_lock_timeout_s: float | None = None,
        orphan_stop_pct: float | None = None,
    ) -> None:
        self.om = order_mgr
        self.broker = broker
        self.journal = journal
        self.risk = risk
        self.clock = clock
        self.bus = bus
        self.settings = settings
        self.calendar = calendar
        self.cost_fn = cost_fn or default_cost_fn()
        self.future_margin_pct = future_margin_pct
        ex = settings.execution
        self.mode = getattr(getattr(ex, "mode", None), "value", "paper")
        self.kill_lock_timeout_s = float(kill_lock_timeout_s if kill_lock_timeout_s is not None
                                         else getattr(ex, "kill_switch_lock_timeout_s", 20.0))
        self.reconcile_lock_timeout_s = float(reconcile_lock_timeout_s if reconcile_lock_timeout_s is not None
                                              else getattr(ex, "reconcile_lock_timeout_s", 5.0))
        # protective stop distance for untracked positions (no ATR available here): % of price
        self.orphan_stop_pct = float(orphan_stop_pct if orphan_stop_pct is not None
                                     else getattr(ex, "orphan_stop_pct", 1.0))
        self.trades: dict[str, ManagedTrade] = {}
        self._last_tick: dict[str, Tick] = {}
        self._last_tick_at: datetime | None = None
        self._safe: dict[str, str] = {}             # active safe-mode kinds -> reason (insertion = recency)
        self._killing = False
        self._our_cancels: set[str] = set()
        self._orphan_entries: dict[str, _OrphanEntry] = {}
        self._tasks: set[asyncio.Task] = set()
        broker.on_order_update(self._on_order)

    def detach(self) -> None:
        """Unregister the broker order-update handler (idempotent); call before discarding this manager."""
        handlers = getattr(self.broker, "_order_handlers", None)
        while handlers is not None and self._on_order in handlers:
            handlers.remove(self._on_order)

    # ---------------------------------------------------------------------------------- utilities
    @property
    def open_trades(self) -> list[ManagedTrade]:
        return [t for t in self.trades.values() if not t.closed]

    @property
    def square_off_time(self):
        cutoff = parse_hhmm(self.settings.broker.intraday_cutoff)
        mins = int(self.settings.execution.square_off_minutes_before_cutoff)
        return (datetime.combine(self.clock.today(), cutoff) - timedelta(minutes=mins)).time()

    async def _publish(self, topic: str, payload: Any) -> None:
        if self.bus is not None:
            await self.bus.publish(topic, payload)

    async def _alert(self, level: str, title: str, body: str = "") -> None:
        log.warning("%s: %s %s", level, title, body)
        await self._publish(topics.ALERT, {"level": level, "title": title, "body": body})

    # ---------------------------------------------------------------------------------- safe mode
    @property
    def safe_mode(self) -> bool:
        return bool(self._safe)

    @property
    def safe_mode_kind(self) -> str:
        """Kind of the most recent active safe-mode reason ("" when not in safe mode)."""
        return next(reversed(self._safe), "") if self._safe else ""

    @property
    def safe_mode_reason(self) -> str:
        return self._safe[self.safe_mode_kind] if self._safe else ""

    @property
    def safe_mode_kinds(self) -> dict[str, str]:
        return dict(self._safe)

    async def enter_safe_mode(self, reason: str, kind: str = "manual") -> None:
        """Block new entries (protective stops and exits keep working) until :meth:`exit_safe_mode` (``kind``)."""
        if kind not in SAFE_MODE_KINDS:
            log.warning("unknown safe-mode kind %r", kind)
        prev = self._safe.pop(kind, None)
        self._safe[kind] = reason
        if prev != reason:
            await self._alert("critical", "Safe mode: new entries blocked", reason)

    def exit_safe_mode(self, kind: str | None = None) -> bool:
        """Clear safe mode of ``kind`` (``None`` = every kind). Returns True if something was cleared.

        Other active kinds stay in force, e.g. a recovered feed (``stale_feed``) does not clear an unprotected
        position (``stop_failure``).
        """
        if kind is None:
            cleared = bool(self._safe)
            self._safe.clear()
            return cleared
        return self._safe.pop(kind, None) is not None

    # ---------------------------------------------------------------------------------- helpers
    def feed_age_s(self, token: str | None = None) -> float | None:
        tick_at = self._last_tick_at if token is None or token not in self._last_tick else \
            self._last_tick[token].ts
        if tick_at is None:
            return None
        return max((self.clock.now() - tick_at).total_seconds(), 0.0)

    def _translate(self, sig: Signal, inst: Instrument, fill: float) -> tuple[float, list[float]]:
        raw = sig.raw
        tick = inst.tick_size or 0.05
        if inst.kind is InstrumentKind.OPTION:
            stop = max(fill - OPTION_DELTA * abs(raw.entry - raw.stop), tick)
            targets = [fill + OPTION_DELTA * abs(t - raw.entry) for t in raw.targets]
        else:
            stop = fill + (raw.stop - raw.entry)
            targets = [fill + (t - raw.entry) for t in raw.targets]
        return round_to_tick(stop, tick), [round_to_tick(t, tick) for t in targets]

    def _time_exit(self, sig: Signal) -> datetime | None:
        if not sig.raw.time_exit:
            return None
        return datetime.combine(self.clock.today(), parse_hhmm(sig.raw.time_exit), IST)

    async def _reject(self, sig: Signal, status: str, reason: str, order: Order | None = None) -> ExecutionResult:
        sig.status = status
        sig.blocked_reason = reason
        try:
            self.journal.update_signal_status(sig.id, status, reason)
        except Exception:
            log.exception("could not update signal %s", sig.id)
        await self._publish(topics.SIGNAL, sig)
        return ExecutionResult(False, reason, entry_order=order)

    def _spawn(self, coro: Any) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ---------------------------------------------------------------------------------- execute
    async def execute(self, signal: Signal) -> ExecutionResult:
        try:
            self.journal.record_signal(signal)
        except Exception:
            log.exception("could not journal signal %s", signal.id)
        if self.safe_mode:
            return await self._reject(signal, "rejected", f"Safe mode: {self.safe_mode_reason}")
        if self._killing:
            return await self._reject(signal, "rejected", "Kill switch in progress")
        if signal.status == "blocked":
            return await self._reject(signal, "blocked", signal.blocked_reason or "Signal is blocked")
        inst = signal.instrument
        if inst is None:
            return await self._reject(signal, "rejected", "No tradeable instrument for this signal")
        side = Side.BUY if inst.kind is InstrumentKind.OPTION else signal.raw.side
        ref = await self.om.ltp(inst, None)
        if ref is None:
            return await self._reject(signal, "rejected", f"No live price for {inst.symbol}")
        stop_plan, _ = self._translate(signal, inst, ref)
        is_opt = inst.kind is InstrumentKind.OPTION
        proposal = TradeProposal(
            underlying=signal.raw.underlying, side=side, entry=ref, stop=stop_plan, lot_size=inst.lot_size,
            premium=ref if is_opt else None, premium_stop=stop_plan if is_opt else None,
            margin_per_lot=None if is_opt else self.future_margin_pct * ref * inst.lot_size,
        )
        try:
            positions = await self.broker.positions()
            funds = await self.broker.funds()
        except Exception as exc:  # noqa: BLE001
            return await self._reject(signal, "rejected", f"Broker unavailable: {exc}")
        blackout = self.calendar.in_blackout(self.clock.now()) if self.calendar is not None else None
        decision = self.risk.check(proposal, positions, funds, self.feed_age_s(), blackout)
        if not decision.allowed:
            return await self._reject(signal, "rejected", "Risk check: " + "; ".join(decision.reasons))
        qty = decision.qty
        if signal.quantity > 0:
            qty = min(qty, max(signal.quantity // inst.lot_size, 1) * inst.lot_size)

        trade_id = new_id("trd_")
        entry = await self.om.enter(side, inst, qty, ref, tag=trade_id)
        if not entry.status.is_terminal and entry.broker_id:
            # cancel not confirmed: the order may still fill -> remember it (before any further await)
            self._orphan_entries[entry.broker_id] = _OrphanEntry(
                entry.broker_id, signal, trade_id, side, inst, ref, entry.filled_qty, entry.avg_price)
        if entry.filled_qty <= 0:
            if not entry.status.is_terminal:
                await self.enter_safe_mode(f"Entry order {entry.broker_id} state unknown: {entry.message}",
                                           "unknown_order")
            status = "rejected" if entry.status is OrderStatus.REJECTED else "expired"
            why = entry.message or entry.status.value
            return await self._reject(signal, status, f"Entry not filled: {why}", entry)
        if not entry.status.is_terminal:
            await self.enter_safe_mode(f"Entry order {entry.broker_id} could not be cancelled; reconcile needed",
                                       "unknown_order")
        t = await self._open_trade(signal, trade_id, inst, side, entry.avg_price, entry.filled_qty, ref)
        return ExecutionResult(True, "", trade_id, entry, t.stop_order)

    async def _open_trade(self, signal: Signal, trade_id: str, inst: Instrument, side: Side, fill: float,
                          qty: int, planned_entry: float, note: str = "") -> ManagedTrade:
        """Register + journal a trade for an entry fill and protect it (lock held from the very start)."""
        stop, targets = self._translate(signal, inst, fill)
        now = self.clock.now()
        t = ManagedTrade(
            trade_id=trade_id, signal_id=signal.id, strategy_id=signal.raw.strategy_id,
            underlying=signal.raw.underlying, instrument=inst, side=side, entry_price=fill,
            initial_qty=qty, qty_open=qty, stop=stop, targets=targets, entry_ts=now,
            time_exit=self._time_exit(signal), last_ltp=fill,
        )
        await t.lock.acquire()
        try:
            self.trades[trade_id] = t
            try:
                self.journal.open_trade(TradeRecord(
                    signal_id=signal.id, strategy_id=t.strategy_id, underlying=t.underlying,
                    instrument_symbol=inst.symbol, side=side.value, qty=t.initial_qty, entry_ts=now,
                    entry_price=fill, stop=stop, targets=targets,
                    mode=self.mode if self.mode in ("paper", "live") else "paper",
                    id=trade_id, planned_entry=planned_entry, regime=signal.regime.value,
                    features=dict(signal.raw.features), reasons=list(signal.reasons) + ([note] if note else []),
                    context={"context_reasons": signal.context_reasons, "probability": signal.probability},
                ))
                signal.status = "taken"
                self.journal.update_signal_status(signal.id, "taken", f"trade {trade_id}")
            except Exception:
                log.exception("could not journal trade %s", trade_id)
            await self._publish(topics.TRADE, {"event": "opened", **t.to_dict()})
            await self._ensure_stop(t)
        finally:
            t.lock.release()
        return t

    # ---------------------------------------------------------------------------------- stray entry fills
    async def _on_orphan_entry(self, ctx: _OrphanEntry, upd: Order) -> None:
        async with ctx.lock:
            dq = upd.filled_qty - ctx.seen_qty
            if upd.status.is_terminal:
                self._orphan_entries.pop(ctx.order_id, None)
            if dq <= 0:
                return
            px = round((upd.avg_price * upd.filled_qty - ctx.seen_avg * ctx.seen_qty) / dq, 2)
            ctx.seen_qty, ctx.seen_avg = upd.filled_qty, upd.avg_price
        await self._alert("critical", f"Late entry fill on {ctx.instrument.symbol}",
                          f"order {ctx.order_id} filled {dq} @ {px} after its cancel failed; "
                          "adopted into a trade and protected with a stop")
        t = self.trades.get(ctx.trade_id)
        if t is not None and not t.closed and t.lock.locked():
            self._spawn(self._adopt_entry_fill(ctx, dq, px))     # never block the broker's update dispatch
        else:
            await self._adopt_entry_fill(ctx, dq, px)

    async def _adopt_entry_fill(self, ctx: _OrphanEntry, dq: int, px: float) -> None:
        t = self.trades.get(ctx.trade_id)
        if t is not None:
            async with t.lock:
                if not t.closed:
                    tot = t.initial_qty + dq
                    t.entry_price = round((t.entry_price * t.initial_qty + px * dq) / tot, 2)
                    t.initial_qty, t.qty_open = tot, t.qty_open + dq
                    try:
                        self.journal.add_note(t.trade_id, f"Late entry fill: +{dq} @ {px} adopted",
                                              ["late_fill"], "")
                    except Exception:
                        log.debug("note failed", exc_info=True)
                    await self._ensure_stop(t)
                    await self._publish(topics.TRADE, {"event": "adjusted", **t.to_dict()})
                    return
        trade_id = ctx.trade_id if ctx.trade_id not in self.trades else new_id("trd_")
        await self._open_trade(ctx.signal, trade_id, ctx.instrument, ctx.side, px, dq, ctx.planned_entry,
                               note=f"Adopted late fill of entry order {ctx.order_id}")

    # ---------------------------------------------------------------------------------- stops
    async def _ensure_stop(self, t: ManagedTrade, _depth: int = 0) -> None:
        """Make sure an active SL-L covers exactly ``qty_open``; exit at once if the stop is already breached.

        Caller must hold ``t.lock``.
        """
        if t.closed or t.qty_open <= 0 or self._killing:
            return
        so = t.stop_order
        half_tick = (t.instrument.tick_size or 0.05) / 2
        if so is not None and not so.status.is_terminal:
            if so.pending_qty == t.qty_open and abs((so.request.trigger_price or 0) - t.stop) < half_tick:
                t.stop_missing = False
                return
            if so.pending_qty == t.qty_open:
                so = await self.om.modify_stop(so, t.stop)
                self._sync_stop(t, so)
                if t.closed:
                    return
                if not so.status.is_terminal and abs((so.request.trigger_price or 0) - t.stop) < half_tick:
                    t.stop_missing = False
                    return
            if not so.status.is_terminal:
                if not await self._cancel_stop(t):
                    return
                if t.closed:
                    return
        new = await self.om.place_stop(t.instrument, t.side.opposite(), t.qty_open, t.stop, tag=t.tag)
        if new.status is OrderStatus.REJECTED:
            t.stop_order = None
            ltp = t.last_ltp
            through = ltp is not None and (ltp - t.stop) * t.side.sign <= 0
            if new.broker_id and ("breached" in new.message.lower() or through) and _depth == 0:
                log.warning("stop for %s already breached; exiting now", t.trade_id)
                await self._exit(t, "stop", _depth=1)
                return
            t.stop_missing = True
            await self.enter_safe_mode(f"Protective stop for {t.instrument.symbol} not placed: {new.message}",
                                       "stop_failure")
            return
        t.stop_order = new
        t.stop_seen.setdefault(new.broker_id, (0, 0.0))
        t.stop_missing = False
        self._sync_stop(t, new)

    async def _cancel_stop(self, t: ManagedTrade) -> bool:
        """Cancel the active stop; False if the cancel could not be confirmed (caller must abort)."""
        so = t.stop_order
        if so is None or so.status.is_terminal:
            if so is not None:
                self._sync_stop(t, so)
            return True
        self._our_cancels.add(so.broker_id)
        res = await self.om.cancel(so)
        self._sync_stop(t, res)
        if not res.status.is_terminal:
            await self.enter_safe_mode(f"Could not cancel stop {so.broker_id} for {t.instrument.symbol}",
                                       "stop_failure")
            return False
        t.stop_order = None
        return True

    def _sync_stop(self, t: ManagedTrade, order: Order) -> None:
        """Account stop fills not yet booked (idempotent: works from updates, cancel results and reconcile)."""
        prev_q, prev_avg = t.stop_seen.get(order.broker_id, (0, 0.0))
        dq = order.filled_qty - prev_q
        if dq <= 0:
            return
        px = (order.avg_price * order.filled_qty - prev_avg * prev_q) / dq
        t.stop_seen[order.broker_id] = (order.filled_qty, order.avg_price)
        self._book_exit(t, dq, round(px, 2))
        if t.qty_open <= 0 and not t.closed:
            self._close_sync(t, "stop")

    async def _on_order(self, upd: Order) -> None:
        ctx = self._orphan_entries.get(upd.broker_id)
        if ctx is not None:
            await self._on_orphan_entry(ctx, upd)
        for t in self.open_trades:
            if t.stop_order is not None and t.stop_order.broker_id == upd.broker_id:
                self._sync_stop(t, upd)
                if t.closed:
                    await self._publish(topics.TRADE, {"event": "closed", **t.to_dict(), "outcome": "stop"})
                elif upd.status in (OrderStatus.CANCELLED, OrderStatus.REJECTED) and not self._killing \
                        and upd.broker_id not in self._our_cancels:
                    # cancelled / rejected by the broker, not by us -> unprotected position
                    t.stop_missing = True
                    await self.enter_safe_mode(f"Stop order {upd.broker_id} {upd.status.value} by broker",
                                               "stop_failure")

    # ---------------------------------------------------------------------------------- exits
    def _book_exit(self, t: ManagedTrade, qty: int, price: float) -> None:
        qty = min(qty, t.qty_open)
        if qty <= 0:
            return
        t.exits.append((qty, price))
        t.qty_open -= qty

    def _close_sync(self, t: ManagedTrade, outcome: str) -> None:
        t.closed = True
        exit_px = round(t.avg_exit(), 2)
        pnl = round(t.gross_pnl(), 2)
        try:
            costs = round(float(self.cost_fn(t.kind, t.initial_qty, t.entry_price, exit_px)), 2)
        except Exception:
            log.exception("cost function failed")
            costs = 0.0
        self.journal.close_trade(t.trade_id, self.clock.now(), exit_px, outcome, pnl, costs,
                                 mfe=round(t.mfe, 2), mae=round(t.mae, 2))
        t.pending_exit = None
        try:
            self.risk.refresh_locks()
        except Exception:
            log.exception("risk lock refresh failed")
        log.info("trade %s closed (%s) pnl %.2f costs %.2f", t.trade_id, outcome, pnl, costs)

    async def _close(self, t: ManagedTrade, outcome: str) -> None:
        self._close_sync(t, outcome)
        await self._publish(topics.TRADE, {"event": "closed", **t.to_dict(), "outcome": outcome})

    async def _exit(self, t: ManagedTrade, outcome: str, _depth: int = 0) -> None:
        """Cancel stop → chased exit of ``qty_open`` → close, or re-protect the remainder on a partial exit.

        Caller must hold ``t.lock``.
        """
        if t.closed or self._killing:
            return
        if not await self._cancel_stop(t):
            return
        if t.closed:                      # stop filled before we could cancel it
            await self._publish(topics.TRADE, {"event": "closed", **t.to_dict(), "outcome": "stop"})
            return
        order = await self.om.exit_position(t.side.opposite(), t.instrument, t.qty_open,
                                            t.last_ltp or t.entry_price, tag=t.tag)
        if t.closed:                      # the kill switch flattened + closed it while we were chasing
            return
        if order.filled_qty > 0:
            self._book_exit(t, order.filled_qty, order.avg_price)
        if t.qty_open <= 0:
            await self._close(t, outcome)
            return
        t.pending_exit = outcome
        if self._killing:                 # kill switch owns the position now; it re-protects any leftover
            t.stop_missing = True
            return
        await self._alert("warning", f"Exit of {t.instrument.symbol} incomplete",
                          f"{t.qty_open} still open ({order.message}); stop re-placed, retrying")
        if not order.status.is_terminal:
            await self.enter_safe_mode(f"Exit order {order.broker_id} state unknown", "unknown_order")
        await self._ensure_stop(t, _depth=max(_depth, 1))

    async def _book_t1(self, t: ManagedTrade) -> None:
        """Caller must hold ``t.lock``."""
        t.t1_done = True
        lot = max(t.instrument.lot_size, 1)
        book = (t.qty_open // lot // 2) * lot
        if book > 0:
            if not await self._cancel_stop(t):
                return
            if t.closed:
                return
            order = await self.om.exit_position(t.side.opposite(), t.instrument, book,
                                                t.last_ltp or t.entry_price, tag=t.tag)
            if order.filled_qty > 0:
                self._book_exit(t, order.filled_qty, order.avg_price)
        t.stop = round_to_tick(t.entry_price, t.instrument.tick_size)
        t.breakeven = True
        await self._ensure_stop(t)
        try:
            self.journal.add_note(t.trade_id, f"T1 hit: booked {book}, stop moved to breakeven {t.stop}",
                                  ["t1", "breakeven"], "")
        except Exception:
            log.debug("note failed", exc_info=True)
        await self._publish(topics.TRADE, {"event": "partial", **t.to_dict()})

    # ---------------------------------------------------------------------------------- ticks
    async def on_tick(self, tick: Tick) -> None:
        self._last_tick[tick.token] = tick
        self._last_tick_at = tick.ts
        for t in self.open_trades:
            if t.instrument.token != tick.token:
                continue
            t.last_ltp = tick.ltp
            fav = (tick.ltp - t.entry_price) * t.side.sign
            t.mfe = max(t.mfe, fav)
            t.mae = max(t.mae, -fav)
            if not t.lock.locked() and not self._killing:
                async with t.lock:
                    if not t.closed:
                        await self._manage(t, tick.ltp)

    async def on_timer(self) -> None:
        """Time-based management without a tick (call every few seconds from the scheduler)."""
        for t in self.open_trades:
            if not t.lock.locked() and not self._killing:
                async with t.lock:
                    if not t.closed:
                        await self._manage(t, t.last_ltp, price_rules=False)

    async def _manage(self, t: ManagedTrade, ltp: float | None, price_rules: bool = True) -> None:
        """Caller must hold ``t.lock``."""
        now = self.clock.now()
        if now.time() >= self.square_off_time:
            await self._exit(t, "square_off")
            return
        if t.pending_exit:
            await self._exit(t, t.pending_exit)
            return
        if t.stop_missing:
            await self._ensure_stop(t)
            if t.closed:
                return
        if t.time_exit is not None and now >= t.time_exit:
            await self._exit(t, "time")
            return
        if not price_rules or ltp is None or not t.targets:
            return
        s = t.side.sign
        if (ltp - t.targets[-1]) * s >= 0:
            await self._exit(t, "target")
        elif not t.t1_done and len(t.targets) > 1 and (ltp - t.targets[0]) * s >= 0:
            await self._book_t1(t)

    # ---------------------------------------------------------------------------------- kill switch
    async def _cancel_trade_orders(self, trades: list[ManagedTrade]) -> int:
        """Cancel outstanding LIMIT (exit / entry) orders of ``trades`` (used when their lock is stuck)."""
        try:
            book = await self.broker.order_book()
        except Exception as exc:  # noqa: BLE001
            log.error("order book unavailable: %s", exc)
            book = []
        seen = {o.broker_id: o for o in book}
        for bid, o in self.om._tracked.items():
            seen.setdefault(bid, o)
        n = 0
        for bid, o in seen.items():
            o = self.om._tracked.get(bid, o)
            if o.status.is_terminal or o.request.order_type is not OrderType.LIMIT:
                continue
            if any(t.owns(o) for t in trades):
                self._our_cancels.add(bid)
                await self.om.cancel(o)
                n += 1
        return n

    async def kill_switch(self, reason: str = "Kill switch activated") -> dict[str, Any]:
        """Wait for in-flight trade actions → cancel all orders → square off with chased limits → lock the day."""
        self._killing = True
        held: dict[str, ManagedTrade] = {}
        cancelled: list[Order] = []
        squared: list[Order] = []
        leftover: list[ManagedTrade] = []
        try:
            trades = self.open_trades
            got = await asyncio.gather(*(_try_lock(t.lock, self.kill_lock_timeout_s) for t in trades))
            held.update({t.trade_id: t for t, ok in zip(trades, got) if ok})
            stuck = [t for t, ok in zip(trades, got) if not ok]
            if stuck:
                await self._alert("critical", "Kill switch: trades busy",
                                  f"{len(stuck)} trades still working after {self.kill_lock_timeout_s:.0f}s; "
                                  "cancelling their exit orders and proceeding")
                await self._cancel_trade_orders(stuck)
                again = await asyncio.gather(*(_try_lock(t.lock, min(5.0, self.kill_lock_timeout_s))
                                               for t in stuck))
                held.update({t.trade_id: t for t, ok in zip(stuck, again) if ok})
            cancelled = await self.om.cancel_all()
            for t in self.open_trades:
                if t.stop_order is not None:
                    self._sync_stop(t, t.stop_order)
                    t.stop_order = None
            for _ in range(3):
                orders = await self.om.square_off_all()
                if not orders:
                    break
                squared += orders
                self._allocate_exits(orders)
                try:
                    if not [p for p in await self.broker.positions() if p.net_qty != 0]:
                        break
                except Exception:  # noqa: BLE001
                    break
            for t in self.open_trades:
                if t.qty_open <= 0:
                    await self._close(t, "kill_switch")
            leftover = self.open_trades
            self._killing = False
            for t in leftover:            # could not flatten: keep it protected
                t.stop_missing = True
                if t.trade_id in held:
                    await self._ensure_stop(t)
        finally:
            self._killing = False
            for t in held.values():
                if t.lock.locked():
                    t.lock.release()
        self.risk.lock_day(reason)
        ok = not leftover
        await self._alert("critical", "Kill switch", f"cancelled {len(cancelled)} orders, "
                          f"squared off {sum(1 for o in squared if o.filled_qty > 0)} positions"
                          + ("" if ok else f"; {len(leftover)} trades still open"))
        return {"ok": ok, "cancelled": len(cancelled),
                "squared_off": sum(1 for o in squared if o.filled_qty > 0)}

    def _allocate_exits(self, orders: list[Order]) -> None:
        """Spread square-off fills over the open trades of each instrument (oldest first)."""
        for o in orders:
            left = o.filled_qty
            for t in sorted(self.open_trades, key=lambda x: x.entry_ts):
                if left <= 0:
                    break
                if t.instrument.token != o.request.instrument.token or o.request.side is t.side:
                    continue
                q = min(left, t.qty_open)
                self._book_exit(t, q, o.avg_price)
                left -= q

    # ---------------------------------------------------------------------------------- reconcile
    async def reconcile(self, master: Any = None) -> dict[str, Any]:
        """Align managed trades with the broker after a reconnect / restart (see module docstring)."""
        summary: dict[str, Any] = {"ok": True, "closed": [], "adjusted": [], "stops_replaced": [],
                                   "adopted": [], "orphans": [], "orphan_stops": [], "stops_cancelled": [],
                                   "busy": []}
        held: dict[str, ManagedTrade] = {}
        try:
            trades = self.open_trades
            got = await asyncio.gather(*(_try_lock(t.lock, self.reconcile_lock_timeout_s) for t in trades))
            held.update({t.trade_id: t for t, ok in zip(trades, got) if ok})
            busy_tokens = {t.instrument.token for t, ok in zip(trades, got) if not ok}
            summary["busy"] = [t.trade_id for t, ok in zip(trades, got) if not ok]
            try:
                positions = await self.broker.positions()
                book_list = await self.broker.order_book()
                fills = await self.broker.trade_book()
            except Exception as exc:  # noqa: BLE001
                await self.enter_safe_mode(f"Reconcile failed: {exc}", "reconcile")
                return {**summary, "ok": False, "error": str(exc)}
            book = {o.broker_id: o for o in book_list}
            net = {p.instrument.token: p for p in positions if p.net_qty != 0}
            for t in await self._adopt_journal_trades(net, book_list, summary, master):
                await t.lock.acquire()
                held[t.trade_id] = t

            by_token: dict[str, list[ManagedTrade]] = {}
            for t in self.open_trades:
                if t.trade_id not in held or t.instrument.token in busy_tokens:
                    continue
                if t.stop_order is not None and t.stop_order.broker_id in book:
                    fresh = book[t.stop_order.broker_id]
                    self._sync_stop(t, fresh)
                    t.stop_order = fresh
                if t.closed:
                    summary["closed"].append(t.trade_id)
                    await self._publish(topics.TRADE, {"event": "closed", **t.to_dict(), "outcome": "stop"})
                    continue
                by_token.setdefault(t.instrument.token, []).append(t)

            for token, tok_trades in by_token.items():
                tok_trades.sort(key=lambda x: x.entry_ts)
                pos = net.get(token)
                side = tok_trades[0].side
                avail = pos.net_qty * side.sign if pos is not None else 0
                for t in tok_trades:
                    have = max(min(avail, t.qty_open), 0)
                    avail -= have
                    if have < t.qty_open:
                        px = self._exit_price_from_fills(t, fills)
                        self._book_exit(t, t.qty_open - have, px)
                        if t.qty_open <= 0:
                            await self._close(t, "reconciled")
                            summary["closed"].append(t.trade_id)
                            continue
                        summary["adjusted"].append(t.trade_id)
                if avail > 0 and tok_trades:
                    t = next((x for x in reversed(tok_trades) if not x.closed), None)
                    if t is not None:
                        t.qty_open += avail
                        summary["adjusted"].append(t.trade_id)
                        await self._alert("warning",
                                          f"Broker holds {avail} more {t.instrument.symbol} than tracked",
                                          "Added to the latest trade and protected with the stop")
                for t in tok_trades:
                    if t.closed:
                        continue
                    before = t.stop_order.broker_id if t.stop_order is not None else None
                    await self._ensure_stop(t)
                    after = t.stop_order.broker_id if t.stop_order is not None else None
                    if after != before:
                        summary["stops_replaced"].append(t.trade_id)
                    if t.stop_missing:
                        summary["ok"] = False

            await self._prune_and_protect(summary, busy_tokens)
        finally:
            for t in held.values():
                if t.lock.locked():
                    t.lock.release()
        return summary

    async def _prune_and_protect(self, summary: dict[str, Any], busy_tokens: set[str]) -> None:
        """Cancel stale / duplicate / surplus stops, then protect untracked positions with an SL-L."""
        try:
            positions = await self.broker.positions()
            book_list = await self.broker.order_book()
        except Exception as exc:  # noqa: BLE001
            await self.enter_safe_mode(f"Reconcile failed: {exc}", "reconcile")
            summary["ok"] = False
            return
        net = {p.instrument.token: p.net_qty for p in positions if p.net_qty != 0}
        stops = [self.om._tracked.get(o.broker_id, o) for o in book_list
                 if o.request.order_type is OrderType.SL_LIMIT and o.status in _LIVE]
        attached = {t.stop_order.broker_id for t in self.open_trades if t.stop_order is not None}
        covered: dict[str, int] = {}
        for o in stops:
            if o.broker_id in attached:
                tok = o.request.instrument.token
                covered[tok] = covered.get(tok, 0) + o.pending_qty
        foreign: list[Order] = []
        for o in sorted(stops, key=lambda x: x.created_at.timestamp() if x.created_at else 0.0):
            tok = o.request.instrument.token
            if o.broker_id in attached or tok in busy_tokens:
                continue
            pos = net.get(tok, 0)
            ours = any(t.owns(o) for t in self.trades.values())
            reducing = pos * o.request.side.sign < 0
            if ours or not reducing or covered.get(tok, 0) + o.pending_qty > abs(pos):
                self._our_cancels.add(o.broker_id)
                res = await self.om.cancel(o)
                summary["stops_cancelled"].append(o.broker_id)
                if not res.status.is_terminal:
                    summary["ok"] = False
                    await self.enter_safe_mode(f"Could not cancel surplus stop {o.broker_id}", "stop_failure")
                if not ours:
                    foreign.append(o)
            else:
                covered[tok] = covered.get(tok, 0) + o.pending_qty
        if foreign:
            await self._alert("warning", "Surplus stop orders cancelled",
                              ", ".join(f"{o.request.instrument.symbol} {o.request.side.value} {o.pending_qty}"
                                        for o in foreign) + " (resting stops would exceed the position)")

        tracked = {t.instrument.token for t in self.open_trades}
        by_tok = {p.instrument.token: p for p in positions if p.net_qty != 0}
        placed = []
        for token, p in by_tok.items():
            if token in tracked:
                continue
            summary["orphans"].append({"symbol": p.instrument.symbol, "qty": p.net_qty})
            need = abs(p.net_qty) - covered.get(token, 0)
            if need <= 0 or token in busy_tokens:
                continue
            side = Side.SELL if p.net_qty > 0 else Side.BUY
            ref = p.ltp or p.avg_price
            pct = self.orphan_stop_pct / 100.0
            trig = ref * (1 - pct) if p.net_qty > 0 else ref * (1 + pct)
            order = await self.om.place_stop(p.instrument, side, need, trig, tag=ORPHAN_TAG)
            if order.status is OrderStatus.REJECTED:
                summary["ok"] = False
                await self.enter_safe_mode(f"Protective stop for untracked {p.instrument.symbol} not placed: "
                                           f"{order.message}", "stop_failure")
            else:
                placed.append(f"{p.instrument.symbol} {side.value} {need} @ {order.request.trigger_price}")
                summary["orphan_stops"].append(p.instrument.symbol)
        if summary["orphans"]:
            body = ", ".join(f"{o['symbol']} {o['qty']}" for o in summary["orphans"])
            if placed:
                body += "; protective SL-L placed: " + ", ".join(placed)
            await self._alert("critical", "Untracked broker positions", body)

    def _exit_price_from_fills(self, t: ManagedTrade, fills: list) -> float:
        opp = t.side.opposite()
        rel = [f for f in fills if f.instrument.token == t.instrument.token and f.side is opp
               and f.ts >= t.entry_ts]
        q = sum(f.quantity for f in rel)
        if q:
            return round(sum(f.quantity * f.price for f in rel) / q, 2)
        return t.last_ltp or t.entry_price

    async def _adopt_journal_trades(self, net: dict, book: list[Order], summary: dict,
                                    master: Any) -> list[ManagedTrade]:
        """After a restart: re-attach open journal trades to broker positions (or close them).

        The trade's still-resting stop (DAY SL-L tagged with the trade) is adopted instead of placing a second
        one; surplus tagged stops are cancelled later by :meth:`_prune_and_protect`.
        """
        try:
            df = self.journal.trades(status="open")
        except Exception:  # noqa: BLE001
            return []
        known = set(self.trades)
        adopted: list[ManagedTrade] = []
        for row in df.to_dict("records") if not df.empty else []:
            if row["id"] in known or row.get("mode") not in (self.mode, None):
                continue
            pos = next((p for p in net.values() if p.instrument.symbol == row["instrument_symbol"]), None)
            if pos is None:
                inst_px = float(row["entry_price"])
                self.journal.close_trade(row["id"], self.clock.now(), inst_px, "reconciled", 0.0, 0.0)
                summary["closed"].append(row["id"])
                continue
            side = Side(row["side"])
            qty = min(int(row["qty"]), abs(pos.net_qty)) if pos.net_qty * side.sign > 0 else 0
            if qty <= 0:
                continue
            targets = row["targets"]
            if isinstance(targets, str):
                targets = json.loads(targets)
            entry_ts = row["entry_ts"]
            if hasattr(entry_ts, "to_pydatetime"):
                entry_ts = entry_ts.to_pydatetime()
            if entry_ts.tzinfo is None:
                entry_ts = entry_ts.replace(tzinfo=IST)
            t = ManagedTrade(
                trade_id=row["id"], signal_id=row.get("signal_id"), strategy_id=row["strategy_id"],
                underlying=row["underlying"], instrument=pos.instrument, side=side,
                entry_price=float(row["entry_price"]), initial_qty=int(row["qty"]), qty_open=qty,
                stop=float(row["stop"]) if row.get("stop") is not None else float(row["entry_price"]),
                targets=[float(x) for x in targets or []], entry_ts=entry_ts, last_ltp=pos.ltp or None,
                stop_missing=True,
            )
            t.t1_done = t.qty_open < t.initial_qty
            cands = [o for o in book
                     if o.request.order_type is OrderType.SL_LIMIT and o.status in _LIVE
                     and o.request.instrument.token == pos.instrument.token
                     and o.request.side is side.opposite() and t.owns(o)]
            if cands:
                cands.sort(key=lambda o: (o.pending_qty != qty,
                                          -(o.created_at or entry_ts).timestamp()))
                keep = self.om.adopt(cands[0])
                t.stop_order = keep
                t.stop_seen[keep.broker_id] = (keep.filled_qty, keep.avg_price)
                if keep.request.trigger_price:
                    t.stop = float(keep.request.trigger_price)       # the live stop may have been moved
                t.breakeven = (t.stop - t.entry_price) * side.sign >= 0
                t.stop_missing = False
            self.trades[t.trade_id] = t
            summary["adopted"].append(t.trade_id)
            adopted.append(t)
        return adopted
