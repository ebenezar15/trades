"""Offline tests for RSS parsing/fetching and the rule-based classifier."""
from __future__ import annotations

from datetime import datetime, timedelta

import httpx
import pytest

from indexedge.config.settings import NewsSource
from indexedge.core.clock import IST
from indexedge.core.types import NewsItem
from indexedge.news.classifier import NewsClassifier
from indexedge.news.sources import FeedError, SourceState, fetch_feed, item_id, parse_feed

NOW = datetime(2026, 10, 16, 10, 0, tzinfo=IST)

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Markets</title>
<item><title>Nifty surges to record high as FIIs pour money into banks</title>
  <link>https://example.test/a1</link>
  <description><![CDATA[<p>Benchmark indices <b>rallied</b> &amp; closed higher.</p>]]></description>
  <pubDate>Fri, 16 Oct 2026 03:45:00 GMT</pubDate></item>
<item><title>RBI keeps repo rate unchanged, MPC stance neutral</title>
  <link>https://example.test/a2</link><pubDate>Fri, 16 Oct 2026 04:30:00 GMT</pubDate></item>
<item><title></title><link>https://example.test/empty</link></item>
<item><title>No date story</title><link>https://example.test/a3</link></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>t</title>
<entry><title>Sensex slumps 800 points</title><link href="https://example.test/b1"/>
<updated>2026-10-16T04:00:00Z</updated><summary>Selloff deepens</summary></entry></feed>"""

SRC = NewsSource(name="Test", url="https://example.test/rss")


def test_parse_rss():
    items = parse_feed(RSS, "Test", NOW)
    assert [i.url for i in items] == ["https://example.test/a1", "https://example.test/a2",
                                      "https://example.test/a3"]
    a1 = items[0]
    assert a1.ts == datetime(2026, 10, 16, 9, 15, tzinfo=IST)
    assert a1.summary == "Benchmark indices rallied & closed higher."
    assert a1.id == item_id("https://example.test/a1")
    assert items[2].ts == NOW                     # missing date falls back to now


def test_parse_atom_and_garbage():
    items = parse_feed(ATOM, "A", NOW)
    assert items[0].title == "Sensex slumps 800 points" and items[0].url == "https://example.test/b1"
    assert parse_feed(b"not xml at all", "x", NOW) == []


async def test_fetch_feed_sends_polite_headers_and_conditional_get():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, content=RSS, headers={"ETag": '"v1"'})

    state = SourceState()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        first = await fetch_feed(SRC, client, state, now=NOW)
        second = await fetch_feed(SRC, client, state, now=NOW)
    assert len(first) == 3 and second == []
    assert "IndexEdge" in seen[0].headers["user-agent"]
    assert state.etag == '"v1"'


async def test_fetch_feed_errors_raise_feed_error():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as client:
        with pytest.raises(FeedError, match="HTTP 500"):
            await fetch_feed(SRC, client)

    def boom(request):
        raise httpx.ReadTimeout("slow")

    async with httpx.AsyncClient(transport=httpx.MockTransport(boom)) as client:
        with pytest.raises(FeedError, match="ReadTimeout"):
            await fetch_feed(SRC, client)


def test_source_state_backoff():
    s = SourceState()
    s.record_failure(NOW, 120, "x")
    assert s.next_allowed == NOW + timedelta(seconds=240) and not s.allowed(NOW)
    s.record_failure(NOW, 120, "x")
    assert s.next_allowed == NOW + timedelta(seconds=480)
    for _ in range(10):
        s.record_failure(NOW, 120, "x")
    assert s.next_allowed == NOW + timedelta(seconds=3600)
    s.record_success(NOW, 120)
    assert s.failures == 0 and s.allowed(NOW + timedelta(seconds=120))


# ------------------------------------------------------------------ classifier
HEAVY = {"NIFTY": {"HDFCBANK": 13.0, "RELIANCE": 8.5, "INFY": 5.0, "M&M": 2.5},
         "BANKNIFTY": {"HDFCBANK": 28.0, "FEDERALBNK": 2.5}}


@pytest.fixture(scope="module")
def clf() -> NewsClassifier:
    return NewsClassifier(HEAVY)


def item(title: str, summary: str = "") -> NewsItem:
    return NewsItem(id=item_id("", title), ts=NOW, source="t", title=title, url="", summary=summary)


def test_relevance_and_entities(clf):
    out = clf.classify(item("HDFC Bank and Infosys drag Nifty lower"))
    assert out.relevance >= 0.9
    assert set(out.entities) == {"HDFCBANK", "INFY"}
    off = clf.classify(item("Local football club wins cup final"))
    assert off.relevance == 0.0 and off.impact == "low" and off.entities == []


def test_alias_boundaries(clf):
    assert clf.heavyweight_hits("M&M shares jump") == ["M&M"]
    assert clf.heavyweight_hits("Federal Bank posts profit") == ["FEDERALBNK"]
    assert clf.heavyweight_hits("Reliability concerns") == []


def test_sentiment_direction(clf):
    assert clf.classify(item("Nifty surges to record high as FIIs pour money")).sentiment > 0.4
    assert clf.classify(item("Sensex crashes as foreign investors sell, rupee slumps")).sentiment < -0.4
    assert clf.classify(item("RBI announces rate cut")).sentiment > 0
    assert clf.classify(item("Fed signals rate hike")).sentiment < 0


@pytest.mark.parametrize("title", [
    "RBI keeps repo rate unchanged at 5.5%",
    "Union Budget 2027: key announcements for markets",
    "Fed holds rates steady, Powell flags caution",
    "India CPI inflation eases to 3.1% in September",
    "Missile attack escalates tensions in Middle East",
    "SEBI bars brokers from offering weekly expiry products",
    "Reliance Q2 results: net profit rises 12%",
    "Government bans export of rice, markets react",
])
def test_high_impact_rules(clf, title):
    assert clf.classify(item(title)).impact == "high"


def test_medium_and_low_impact(clf):
    assert clf.classify(item("Nifty ends flat in choppy trade")).impact == "medium"
    assert clf.classify(item("Country bans social media for kids")).impact == "low"


def test_finbert_requested_without_transformers_falls_back():
    c = NewsClassifier(["HDFCBANK"], use_finbert=True)
    try:
        import transformers  # noqa: F401
    except ImportError:
        assert c.use_finbert is False
    assert -1 <= c.sentiment("Markets rally") <= 1
