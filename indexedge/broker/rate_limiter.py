"""Async token-bucket rate limiter with a strict sliding-window cap.

A plain token bucket with ``burst == rate`` can admit up to ~2x``rate`` requests inside one second (a full
bucket plus refill). Broker limits are per-second caps, so in addition to the bucket we keep the timestamps
of recent acquisitions and never admit more than ``floor(rate * window)`` inside any ``window`` seconds.
``RateLimiter(8)`` therefore guarantees <= 8 orders in any 1-second window.

The clock and sleep functions are injectable so tests can run on a fake clock.
"""
from __future__ import annotations

import asyncio
import math
import time
from collections import deque
from typing import Awaitable, Callable


class RateLimiter:
    def __init__(
        self,
        rate: float,
        burst: int | None = None,
        *,
        window: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        self.rate = float(rate)
        self.burst = int(burst) if burst is not None else max(1, int(rate))
        if self.burst < 1:
            raise ValueError("burst must be >= 1")
        self.window = window
        self.max_in_window = max(1, math.floor(self.rate * window))
        self._clock = clock
        self._sleep = sleep
        self._tokens = float(self.burst)
        self._last = clock()
        self._recent: deque[float] = deque()
        self._lock: asyncio.Lock | None = None
        self.acquired = 0

    def _refill(self, now: float) -> None:
        self._tokens = min(self.burst, self._tokens + (now - self._last) * self.rate)
        self._last = now
        while self._recent and now - self._recent[0] >= self.window:
            self._recent.popleft()

    def _wait_time(self, now: float) -> float:
        self._refill(now)
        wait = 0.0
        if self._tokens < 1.0:
            wait = (1.0 - self._tokens) / self.rate
        if len(self._recent) >= self.max_in_window:
            wait = max(wait, self.window - (now - self._recent[0]))
        return wait

    def _take(self, now: float) -> None:
        self._tokens -= 1.0
        self._recent.append(now)
        self.acquired += 1

    def try_acquire(self) -> bool:
        """Non-blocking acquire; True if a slot was taken."""
        now = self._clock()
        if self._wait_time(now) > 0:
            return False
        self._take(now)
        return True

    async def acquire(self) -> None:
        """Wait until a request may be sent (FIFO across concurrent callers)."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            while True:
                now = self._clock()
                wait = self._wait_time(now)
                if wait <= 1e-9:
                    self._take(now)
                    return
                await self._sleep(wait)

    async def __aenter__(self) -> "RateLimiter":
        await self.acquire()
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    def recent_count(self) -> int:
        """Acquisitions within the last ``window`` seconds (for health / order-rate display)."""
        self._refill(self._clock())
        return len(self._recent)
