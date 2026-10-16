"""RSS/Atom news source fetching (polite: User-Agent, timeout, conditional GET, per-source backoff).

``fetch_feed`` downloads with an injected ``httpx.AsyncClient`` and parses the bytes with ``feedparser``
(feedparser never touches the network itself here). Network/HTTP failures raise :class:`FeedError` so the
caller can back off; :class:`SourceState` tracks ETag/Last-Modified and the backoff schedule per source.
"""
from __future__ import annotations

import calendar
import hashlib
import html
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import feedparser

from indexedge.config.settings import NewsSource
from indexedge.core.clock import IST
from indexedge.core.types import NewsItem

USER_AGENT = "Mozilla/5.0 (compatible; IndexEdge/1.0; personal news reader; +https://localhost)"
DEFAULT_TIMEOUT_S = 15.0
MAX_SUMMARY_CHARS = 500
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


class FeedError(Exception):
    """A source could not be fetched or parsed (caller should back off)."""


@dataclass
class SourceState:
    """Per-source politeness state: conditional-GET validators and exponential backoff."""

    etag: str | None = None
    last_modified: str | None = None
    failures: int = 0
    next_allowed: datetime | None = None
    last_error: str = ""

    def allowed(self, now: datetime) -> bool:
        return self.next_allowed is None or now >= self.next_allowed

    def record_success(self, now: datetime, poll_seconds: float) -> None:
        self.failures = 0
        self.last_error = ""
        self.next_allowed = now + timedelta(seconds=poll_seconds)

    def record_failure(self, now: datetime, poll_seconds: float, error: str,
                       max_backoff_s: float = 3600.0) -> None:
        self.failures += 1
        self.last_error = error
        delay = min(max_backoff_s, poll_seconds * (2 ** self.failures))
        self.next_allowed = now + timedelta(seconds=delay)


def item_id(url: str, title: str = "") -> str:
    """Stable id used for de-duplication (hash of the canonical URL, falling back to the title)."""
    key = (url or "").strip().split("#")[0] or title.strip().lower()
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def clean_text(text: str, limit: int = MAX_SUMMARY_CHARS) -> str:
    text = html.unescape(_TAG_RE.sub(" ", text or ""))
    text = _WS_RE.sub(" ", text).strip()
    return text[:limit]


def _entry_ts(entry: Any, fallback: datetime) -> datetime:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        st = entry.get(key)
        if st:
            try:
                return datetime.fromtimestamp(calendar.timegm(st), tz=UTC).astimezone(IST)
            except (OverflowError, ValueError, TypeError):
                continue
    return fallback


def parse_feed(content: bytes | str, source_name: str, now: datetime | None = None) -> list[NewsItem]:
    """Parse RSS/Atom bytes into ``NewsItem`` objects (unclassified). Never raises on malformed input."""
    now = now or datetime.now(IST)
    parsed = feedparser.parse(content)
    items: list[NewsItem] = []
    for e in parsed.entries:
        title = clean_text(e.get("title", ""), 300)
        url = (e.get("link") or "").strip()
        if not title:
            continue
        ts = _entry_ts(e, now)
        if ts > now + timedelta(minutes=5):   # bogus future dates
            ts = now
        items.append(NewsItem(id=item_id(url, title), ts=ts, source=source_name, title=title, url=url,
                              summary=clean_text(e.get("summary", "") or e.get("description", ""))))
    return items


async def fetch_feed(source: NewsSource, client: Any, state: SourceState | None = None,
                     timeout_s: float = DEFAULT_TIMEOUT_S, now: datetime | None = None) -> list[NewsItem]:
    """Fetch and parse one source. Returns ``[]`` on HTTP 304; raises :class:`FeedError` on failures."""
    headers = {"User-Agent": USER_AGENT,
               "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.5"}
    if state is not None:
        if state.etag:
            headers["If-None-Match"] = state.etag
        if state.last_modified:
            headers["If-Modified-Since"] = state.last_modified
    try:
        resp = await client.get(source.url, headers=headers, timeout=timeout_s, follow_redirects=True)
    except Exception as exc:
        raise FeedError(f"{source.name}: {type(exc).__name__}") from exc
    if resp.status_code == 304:
        return []
    if resp.status_code != 200:
        raise FeedError(f"{source.name}: HTTP {resp.status_code}")
    if state is not None:
        state.etag = resp.headers.get("etag") or state.etag
        state.last_modified = resp.headers.get("last-modified") or state.last_modified
    items = parse_feed(resp.content, source.name, now)
    if not items and b"<" not in resp.content[:200]:
        raise FeedError(f"{source.name}: response is not a feed")
    return items
