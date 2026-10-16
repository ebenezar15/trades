"""News polling service: fetch enabled RSS sources, de-duplicate, classify, store, publish and alert.

Politeness: one shared ``httpx.AsyncClient`` with a descriptive User-Agent, per-request timeout,
conditional GET (ETag / Last-Modified) and exponential per-source backoff on errors (see ``SourceState``).
"""
from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from indexedge.core import bus as topics
from indexedge.core.types import NewsItem
from indexedge.news.classifier import NewsClassifier
from indexedge.news.sources import USER_AGENT, FeedError, SourceState, fetch_feed

log = logging.getLogger(__name__)

SEEN_LIMIT = 5000
RECENT_LIMIT = 500
ALERT_MAX_AGE = timedelta(hours=2)     # never alert on old stories (e.g. the first poll after startup)

StoreCallback = Callable[[NewsItem], Awaitable[None] | None]


class NewsService:
    def __init__(self, settings: Any, bus: Any, clock: Any, classifier: NewsClassifier | None = None,
                 store_cb: StoreCallback | None = None, client: Any = None) -> None:
        self.cfg = getattr(settings, "news", settings)
        self.notify_cfg = getattr(settings, "notify", None)
        heavy = getattr(settings, "heavyweights", None)
        self.bus = bus
        self.clock = clock
        self.classifier = classifier or NewsClassifier(heavy, use_finbert=getattr(self.cfg, "use_finbert",
                                                                                   False))
        self.store_cb = store_cb
        self._client = client
        self._own_client = client is None
        self.states: dict[str, SourceState] = {}
        self._seen: OrderedDict[str, None] = OrderedDict()
        self.recent: deque[NewsItem] = deque(maxlen=RECENT_LIMIT)
        self._stop = asyncio.Event()

    # ------------------------------------------------------------------ helpers
    def _get_client(self) -> Any:
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, follow_redirects=True,
                                             timeout=15.0)
        return self._client

    def _mark_seen(self, item_id: str) -> bool:
        """Return True if new."""
        if item_id in self._seen:
            return False
        self._seen[item_id] = None
        while len(self._seen) > SEEN_LIMIT:
            self._seen.popitem(last=False)
        return True

    def latest(self, limit: int = 50) -> list[NewsItem]:
        return sorted(self.recent, key=lambda i: i.ts, reverse=True)[:limit]

    def source_health(self) -> dict[str, dict[str, Any]]:
        return {name: {"failures": s.failures, "last_error": s.last_error,
                       "next_allowed": s.next_allowed.isoformat() if s.next_allowed else None}
                for name, s in self.states.items()}

    # ------------------------------------------------------------------ polling
    async def poll_once(self) -> list[NewsItem]:
        """Poll every enabled source that is not backing off. Returns newly seen, classified items."""
        now = self.clock.now()
        sources = [s for s in self.cfg.sources if s.enabled and s.kind == "rss"]
        due = []
        for src in sources:
            state = self.states.setdefault(src.name, SourceState())
            if state.allowed(now):
                due.append((src, state))
        client = self._get_client()
        results = await asyncio.gather(*(fetch_feed(src, client, state, now=now) for src, state in due),
                                       return_exceptions=True)
        new_items: list[NewsItem] = []
        for (src, state), res in zip(due, results):
            if isinstance(res, BaseException):
                msg = str(res) if isinstance(res, FeedError) else f"{src.name}: {type(res).__name__}"
                state.record_failure(now, self.cfg.poll_seconds, msg)
                log.info("news source failed (%d in a row): %s", state.failures, msg)
                continue
            state.record_success(now, self.cfg.poll_seconds)
            for raw in res:
                if not self._mark_seen(raw.id):
                    continue
                new_items.append(self.classifier.classify(raw))
        new_items.sort(key=lambda i: i.ts)
        for item in new_items:
            await self._handle(item, now)
        return new_items

    async def _handle(self, item: NewsItem, now: datetime) -> None:
        self.recent.append(item)
        if self.store_cb is not None:
            try:
                res = self.store_cb(item)
                if asyncio.iscoroutine(res):
                    await res
            except Exception:
                log.exception("news store callback failed")
        if self.bus is None:
            return
        await self.bus.publish(topics.NEWS, item)
        wants_alert = getattr(self.notify_cfg, "notify_high_impact_news", True) if self.notify_cfg else True
        if item.impact == "high" and item.relevance > 0 and wants_alert and now - item.ts <= ALERT_MAX_AGE:
            mood = "positive" if item.sentiment > 0.2 else "negative" if item.sentiment < -0.2 else "neutral"
            await self.bus.publish(topics.ALERT, {
                "level": "warning", "title": f"High-impact news: {item.title}",
                "body": f"{item.source} · sentiment {mood} ({item.sentiment:+.2f}) · {item.url}",
                "kind": "news", "news_id": item.id})

    async def run(self) -> None:
        """Poll every ``poll_seconds`` until :meth:`stop`. Never raises."""
        self._stop.clear()
        try:
            while not self._stop.is_set():
                try:
                    await self.poll_once()
                except Exception:
                    log.exception("news poll failed")
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=max(5, self.cfg.poll_seconds))
                except TimeoutError:
                    pass
        finally:
            await self.aclose()

    def stop(self) -> None:
        self._stop.set()

    async def aclose(self) -> None:
        if self._own_client and self._client is not None:
            await self._client.aclose()
            self._client = None
