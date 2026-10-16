"""Offline tests for the event calendar and the news polling service."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import yaml

from indexedge.config.settings import NewsSettings, NewsSource, Settings
from indexedge.core import bus as topics
from indexedge.core.bus import EventBus
from indexedge.core.clock import IST, SimClock
from indexedge.news.calendar import DEFAULT_EVENTS_PATH, EventCalendar, event_to_dict, load_events
from indexedge.news.service import NewsService

EVENTS = [
    {"date": "2026-12-04", "time": "10:00", "title": "RBI MPC policy decision", "category": "rbi",
     "impact": "high", "verified": True},
    {"date": "2026-11-12", "time": "16:00", "title": "India CPI", "category": "cpi", "impact": "medium"},
    {"date": "2026-11-13", "title": "Minor thing", "category": "other", "impact": "low"},
    {"date": "not-a-date", "title": "bad row"},
]


@pytest.fixture()
def events_file(tmp_path: Path) -> Path:
    p = tmp_path / "events.yaml"
    p.write_text(yaml.safe_dump(EVENTS))
    return p


def cal_at(at: datetime, path: Path, **kw) -> EventCalendar:
    return EventCalendar(NewsSettings(), SimClock(at), path=path, **kw)


def test_load_events_skips_bad_rows(events_file):
    evs = load_events(events_file)
    assert len(evs) == 3
    rbi = evs[0]
    assert rbi.ts == datetime(2026, 12, 4, 10, 0, tzinfo=IST) and rbi.category == "rbi"
    assert (rbi.blackout_before_min, rbi.blackout_after_min) == (15, 15)
    assert evs[2].blackout_before_min == 0 and evs[2].ts.hour == 9


def test_repo_events_yaml_is_valid():
    raw = yaml.safe_load(DEFAULT_EVENTS_PATH.read_text())
    assert len(load_events(DEFAULT_EVENTS_PATH)) == len(raw) > 20
    for row in raw:
        assert {"date", "time", "title", "category", "impact", "verified"} <= set(row)
    budget = [r for r in raw if r["category"] == "budget"]
    assert str(budget[0]["date"]) == "2027-02-01"


def test_blackout_window(events_file):
    ev_ts = datetime(2026, 12, 4, 10, 0, tzinfo=IST)
    c = cal_at(ev_ts, events_file)
    assert c.in_blackout(ev_ts - timedelta(minutes=10)).category == "rbi"
    assert c.in_blackout(ev_ts + timedelta(minutes=15)) is not None
    assert c.in_blackout(ev_ts - timedelta(minutes=16)) is None
    assert c.in_blackout(ev_ts + timedelta(minutes=16)) is None
    cpi = datetime(2026, 11, 12, 16, 0, tzinfo=IST)
    assert c.in_blackout(cpi + timedelta(minutes=4)).category == "cpi"    # medium → 5 min
    assert c.in_blackout(cpi + timedelta(minutes=6)) is None


def test_event_risk_profile(events_file):
    ev = datetime(2026, 12, 4, 10, 0, tzinfo=IST)
    c = cal_at(ev, events_file)
    far = c.event_risk(ev - timedelta(days=3))
    day_before = c.event_risk(ev - timedelta(hours=12))
    hour_before = c.event_risk(ev - timedelta(hours=1))
    assert far == 0.0 and 0.4 < day_before < hour_before < 1.0
    assert c.event_risk(ev) == 1.0
    after = c.event_risk(ev + timedelta(hours=2))
    assert 0 < after < 1 and c.event_risk(ev + timedelta(hours=7)) == 0.0
    cpi = datetime(2026, 11, 12, 16, 0, tzinfo=IST)
    assert c.event_risk(cpi) == 0.5                                       # medium counts half


def test_expiries_and_holidays(events_file):
    now = datetime(2026, 10, 26, 9, 0, tzinfo=IST)
    c = cal_at(now, events_file, expiries={"NIFTY": [date(2026, 10, 27)]}, holidays=["2026-11-10"])
    up = c.upcoming(days=20)
    cats = [e.category for e in up]
    assert "expiry" in cats and "holiday" in cats and "cpi" in cats
    assert up == sorted(up, key=lambda e: e.ts)
    # expiry and holiday never create a blackout or event risk
    assert c.in_blackout(datetime(2026, 10, 27, 15, 30, tzinfo=IST)) is None
    assert c.event_risk(datetime(2026, 10, 27, 12, 0, tzinfo=IST)) == 0.0
    assert c.on_day(date(2026, 10, 27))[0].title == "NIFTY expiry"
    assert event_to_dict(up[0])["ts"].endswith("+05:30")
    c.set_expiries([date(2026, 11, 3)])
    assert [e.title for e in c.events if e.category == "expiry"] == ["F&O expiry"]


def test_calendar_accepts_full_settings_and_missing_file(tmp_path):
    s = Settings(holidays=["2026-11-10"])
    c = EventCalendar(s, SimClock(datetime(2026, 11, 1, tzinfo=IST)), path=tmp_path / "nope.yaml")
    assert [e.category for e in c.events] == ["holiday"]


# ------------------------------------------------------------------ service
def rss(*items: tuple[str, str, str]) -> bytes:
    body = "".join(f"<item><title>{t}</title><link>{u}</link><pubDate>{d}</pubDate></item>" for t, u, d in items)
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>{body}</channel></rss>'.encode()


NOW = datetime(2026, 10, 16, 10, 0, tzinfo=IST)
FRESH = "Fri, 16 Oct 2026 04:20:00 GMT"   # 09:50 IST
OLD = "Thu, 15 Oct 2026 04:20:00 GMT"


def make_service(handler, store=None):
    settings = Settings(news=NewsSettings(poll_seconds=60, sources=[
        NewsSource(name="A", url="https://a.test/rss"), NewsSource(name="B", url="https://b.test/rss"),
        NewsSource(name="Off", url="https://off.test/rss", enabled=False)]),
        heavyweights={"NIFTY": {"HDFCBANK": 13.0}})
    clock = SimClock(NOW)
    bus = EventBus()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    svc = NewsService(settings, bus, clock, store_cb=store, client=client)
    return svc, bus, clock, client


async def test_service_dedupes_publishes_and_alerts():
    feed_a = rss(("RBI hikes repo rate by 25 bps", "https://x.test/1", FRESH),
                 ("Nifty ends flat", "https://x.test/2", FRESH),
                 ("Old war story attack", "https://x.test/3", OLD))
    feed_b = rss(("RBI hikes repo rate by 25 bps", "https://x.test/1", FRESH))   # duplicate URL

    calls = {"off": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "off.test":
            calls["off"] += 1
        return httpx.Response(200, content=feed_a if request.url.host == "a.test" else feed_b)

    stored = []

    async def store(item):
        stored.append(item)

    svc, bus, clock, client = make_service(handler, store)
    news, alerts = [], []
    bus.subscribe(topics.NEWS, news.append)
    bus.subscribe(topics.ALERT, alerts.append)
    new = await svc.poll_once()
    assert len(new) == 3 and len(news) == 3 and len(stored) == 3 and calls["off"] == 0
    assert [a["news_id"] for a in alerts] == [new[[i.url for i in new].index("https://x.test/1")].id]
    assert "High-impact news" in alerts[0]["title"]
    # second poll before interval: nothing due
    assert await svc.poll_once() == []
    clock.advance(seconds=61)
    assert await svc.poll_once() == []            # all seen
    assert svc.latest(2)[0].ts >= svc.latest(2)[1].ts
    await client.aclose()


async def test_service_backs_off_failing_source():
    hits = {"a": 0, "b": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.url.host[0]
        hits[key] += 1
        if key == "a":
            return httpx.Response(503)
        return httpx.Response(200, content=rss(("Nifty up", "https://x.test/9", FRESH)))

    svc, _bus, clock, client = make_service(handler)
    await svc.poll_once()
    assert svc.states["A"].failures == 1 and "HTTP 503" in svc.states["A"].last_error
    clock.advance(seconds=61)
    await svc.poll_once()
    assert hits == {"a": 1, "b": 2}               # A still backing off (120 s)
    clock.advance(seconds=60)
    await svc.poll_once()
    assert hits["a"] == 2 and svc.states["A"].failures == 2
    assert svc.source_health()["A"]["failures"] == 2
    await client.aclose()
