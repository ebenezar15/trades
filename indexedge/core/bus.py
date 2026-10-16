"""Tiny in-process async pub/sub event bus (replaces Redis for a single-user app)."""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger(__name__)

Handler = Callable[[Any], Awaitable[None] | None]

# Well-known topics
TICK = "tick"
CANDLE = "candle"            # payload: dict(underlying, timeframe, candle_ts, df)
CONTEXT = "context"          # ContextSnapshot
NEWS = "news"                # NewsItem
SIGNAL = "signal"            # Signal
ORDER = "order"              # Order
FILL = "fill"                # Fill
TRADE = "trade"              # dict trade record
HEALTH = "health"            # dict
ALERT = "alert"              # dict(level, title, body)


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Handler]] = defaultdict(list)
        self._queues: dict[str, list[asyncio.Queue]] = defaultdict(list)

    def subscribe(self, topic: str, handler: Handler) -> None:
        self._subs[topic].append(handler)

    def unsubscribe(self, topic: str, handler: Handler) -> None:
        if handler in self._subs.get(topic, []):
            self._subs[topic].remove(handler)

    def queue(self, topic: str, maxsize: int = 1000) -> asyncio.Queue:
        """Get a queue that receives every payload published on ``topic`` (for websockets)."""
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._queues[topic].append(q)
        return q

    def drop_queue(self, topic: str, q: asyncio.Queue) -> None:
        if q in self._queues.get(topic, []):
            self._queues[topic].remove(q)

    async def publish(self, topic: str, payload: Any) -> None:
        for handler in list(self._subs.get(topic, [])):
            try:
                res = handler(payload)
                if asyncio.iscoroutine(res):
                    await res
            except Exception:  # a broken subscriber must never break the publisher
                log.exception("bus handler failed on topic %s", topic)
        for q in list(self._queues.get(topic, [])):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(payload)

    def publish_nowait(self, topic: str, payload: Any) -> None:
        """Fire-and-forget publish from sync code running inside the event loop thread."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.publish(topic, payload))
