"""Tick -> candle aggregation for live feeds.

Buckets are anchored at 09:15 IST each session. Bar volume is derived from the feed's *cumulative* day volume
(difference between the last cumulative value in this bar and the one at the end of the previous bar). A bar is
emitted when a tick for the next bucket arrives or when :meth:`flush` is called past the bucket end.
"""
from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Awaitable, Callable

import pandas as pd

from indexedge.core.clock import IST
from indexedge.core.types import Tick

log = logging.getLogger(__name__)

CloseHandler = Callable[[str, pd.Series], Awaitable[None] | None]


@dataclass
class _Bar:
    start: datetime
    end: datetime
    open: float
    high: float
    low: float
    close: float
    vol_base: float
    cum: float
    oi: float

    def series(self) -> pd.Series:
        return pd.Series(
            {"open": self.open, "high": self.high, "low": self.low, "close": self.close,
             "volume": max(self.cum - self.vol_base, 0.0), "oi": self.oi},
            name=pd.Timestamp(self.start),
            dtype=float,
        )


class CandleAggregator:
    def __init__(
        self,
        interval_min: int,
        on_close: CloseHandler,
        session_start: time = time(9, 15),
        session_end: time = time(15, 30),
        session_only: bool = True,
    ) -> None:
        if interval_min < 1:
            raise ValueError("interval_min must be >= 1")
        self.interval = timedelta(minutes=interval_min)
        self.on_close = on_close
        self.session_start = session_start
        self.session_end = session_end
        self.session_only = session_only
        self._bars: dict[str, _Bar] = {}
        self._last_cum: dict[str, float] = {}
        self._last_day: dict[str, object] = {}

    def _bucket(self, ts: datetime) -> tuple[datetime, datetime] | None:
        ts = ts.astimezone(IST)
        open_dt = datetime.combine(ts.date(), self.session_start, IST)
        close_dt = datetime.combine(ts.date(), self.session_end, IST)
        if self.session_only and not (open_dt <= ts < close_dt):
            return None
        n = (ts - open_dt) // self.interval
        start = open_dt + n * self.interval
        end = start + self.interval
        if start < close_dt:
            end = min(end, close_dt)
        return start, end

    def current(self, token: str) -> pd.Series | None:
        bar = self._bars.get(token)
        return bar.series() if bar else None

    async def _emit(self, token: str, bar: _Bar) -> tuple[str, pd.Series]:
        s = bar.series()
        res = self.on_close(token, s)
        if inspect.isawaitable(res):
            await res
        return token, s

    async def on_tick(self, tick: Tick) -> list[tuple[str, pd.Series]]:
        """Process a tick; returns bars closed by it (also passed to ``on_close``)."""
        bucket = self._bucket(tick.ts)
        if bucket is None:
            return []
        start, end = bucket
        token = tick.token
        closed: list[tuple[str, pd.Series]] = []
        day = start.date()
        last_cum = self._last_cum.get(token)
        new_day = self._last_day.get(token) != day
        bar = self._bars.get(token)

        if bar is not None and start < bar.start:
            log.debug("late tick for %s at %s ignored", token, tick.ts)
            return []
        if bar is not None and start > bar.start:
            closed.append(await self._emit(token, bar))
            self._bars.pop(token, None)
            bar = None

        cum = float(tick.volume or 0.0)
        if bar is None:
            if last_cum is None or new_day:
                # first tick of the day: the opening bucket owns all volume traded so far
                base = 0.0 if start.time() == self.session_start else cum
            elif cum < last_cum:  # cumulative counter reset
                base = 0.0
            else:
                base = last_cum
            bar = _Bar(start, end, tick.ltp, tick.ltp, tick.ltp, tick.ltp, base, cum, float(tick.oi or 0.0))
            self._bars[token] = bar
        else:
            if cum < bar.cum:  # feed reset mid-bar: keep monotonic
                bar.vol_base -= bar.cum - cum
            bar.high = max(bar.high, tick.ltp)
            bar.low = min(bar.low, tick.ltp)
            bar.close = tick.ltp
            bar.cum = cum
            if tick.oi:
                bar.oi = float(tick.oi)
        self._last_cum[token] = cum
        self._last_day[token] = day
        return closed

    async def flush(self, now: datetime) -> list[tuple[str, pd.Series]]:
        """Emit every forming bar whose bucket has ended at ``now``."""
        closed = []
        for token, bar in list(self._bars.items()):
            if now >= bar.end:
                self._bars.pop(token, None)
                closed.append(await self._emit(token, bar))
        return closed
