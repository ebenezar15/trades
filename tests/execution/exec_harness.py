"""Execution test harness: SimClock + PaperBroker + synthetic instrument master + in-memory journal."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime

from indexedge.broker.paper import PaperBroker
from indexedge.broker.rate_limiter import RateLimiter
from indexedge.config.settings import Settings
from indexedge.core.bus import EventBus
from indexedge.core.clock import IST, SimClock
from indexedge.core.types import ContextSnapshot, Regime, Side, Signal, StrategySignal, Tick
from indexedge.data.instruments import InstrumentMaster
from indexedge.data.synthetic import synthetic_master_rows
from indexedge.execution.orders import OrderManager
from indexedge.execution.trades import TradeManager
from indexedge.journal.db import Journal
from indexedge.risk.engine import RiskEngine
from indexedge.signals.composer import SignalComposer

DAY = date(2026, 10, 16)  # Friday


def at(h: int, m: int = 0, s: int = 0) -> datetime:
    return datetime(DAY.year, DAY.month, DAY.day, h, m, s, tzinfo=IST)


def flat_costs(kind: str, qty: int, entry: float, exit: float) -> float:
    return 100.0


@dataclass
class Harness:
    clock: SimClock
    settings: Settings
    master: InstrumentMaster
    journal: Journal
    risk: RiskEngine
    broker: PaperBroker
    bus: EventBus
    limiter: RateLimiter
    om: OrderManager
    tm: TradeManager
    composer: SignalComposer
    hooks: list = field(default_factory=list)       # async callables run on every chase sleep
    calls: list = field(default_factory=list)       # (sim time, broker method)
    events: list = field(default_factory=list)      # bus (topic, payload)

    @property
    def fut(self):
        return self.master.nearest_future("NIFTY", DAY)

    async def tick(self, inst, ltp, bid=None, ask=None):
        await self.broker.feed_tick(Tick(inst.token, self.clock.now(), ltp, bid=bid, ask=ask))

    def signal(self, side=Side.BUY, entry=25000.0, stop=24980.0, targets=(25020.0, 25040.0), **kw) -> Signal:
        raw = StrategySignal("orb15", "NIFTY", self.clock.now(), side, entry, stop, list(targets),
                             ["Opening range breakout"], **kw)
        ctx = ContextSnapshot(self.clock.now(), 0.4, False, 0.0, {}, ["S&P 500 fut +0.8% (bullish)"])
        return self.composer.compose(raw, None, ctx, {"wins": 60, "n": 100}, Regime.TREND_UP)


def build(preferred="future", max_fill=None, capital=1_000_000.0, start=None) -> Harness:
    clock = SimClock(start or at(10, 0))
    settings = Settings()
    settings.risk.capital = capital
    settings.execution.preferred_instrument = preferred
    master = InstrumentMaster.from_json(synthetic_master_rows(DAY, spots={"NIFTY": 25000, "BANKNIFTY": 56000}))
    journal = Journal(":memory:")
    risk = RiskEngine(settings.risk, clock, journal, cutoff=settings.broker.intraday_cutoff)
    broker = PaperBroker(clock, capital=capital * 5, slippage_ticks=0, max_fill_qty_per_tick=max_fill)
    bus = EventBus()
    h_ref: dict = {}

    async def chase_sleep(dt: float) -> None:
        clock.advance(seconds=dt)
        for hook in list(h_ref["h"].hooks):
            await hook()
        await asyncio.sleep(0)

    async def limiter_sleep(dt: float) -> None:
        clock.advance(seconds=dt)
        await asyncio.sleep(0)

    limiter = RateLimiter(settings.execution.max_orders_per_second,
                          clock=lambda: clock.now().timestamp(), sleep=limiter_sleep)
    om = OrderManager(broker, limiter, settings, journal, bus=bus, sleep=chase_sleep)
    tm = TradeManager(om, broker, journal, risk, clock, bus, settings, cost_fn=flat_costs)
    composer = SignalComposer(settings, None, risk, None, master, cost_fn=flat_costs)
    h = Harness(clock, settings, master, journal, risk, broker, bus, limiter, om, tm, composer)
    h_ref["h"] = h

    for name in ("place_order", "modify_order", "cancel_order"):
        orig = getattr(broker, name)

        def wrap(orig=orig, name=name):
            async def inner(*a, **k):
                h.calls.append((clock.now(), name))
                return await orig(*a, **k)
            return inner

        setattr(broker, name, wrap())
    for topic in ("trade", "order", "alert", "signal"):
        bus.subscribe(topic, lambda p, topic=topic: h.events.append((topic, p)))
    return h


