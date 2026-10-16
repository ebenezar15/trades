"""Yahoo live feed, agent switch/log, chart annotations, broker setup wizard."""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from indexedge.app import create_app
from indexedge.config.settings import Settings
from indexedge.core.clock import IST, SimClock
from indexedge.core.types import ExecutionMode
from indexedge.runtime import Runtime
from indexedge.services.yahoo_feed import YahooFeed, session_only


def _one_min(day: str, start: str, n: int, base: float = 25000.0) -> pd.DataFrame:
    idx = pd.date_range(f"{day} {start}", periods=n, freq="1min", tz=IST)
    px = base + np.arange(n, dtype=float)
    return pd.DataFrame({"open": px, "high": px + 2, "low": px - 2, "close": px + 1, "volume": 0.0}, index=idx)


async def test_yahoo_feed_publishes_only_closed_5m_bars():
    clock = SimClock(datetime(2026, 10, 14, 9, 31, 30, tzinfo=IST))
    data = _one_min("2026-10-14", "09:15", 16)                       # 09:15 .. 09:30
    feed = YahooFeed(clock, downloader=lambda t, i, p: data)
    bars, quotes = [], []

    async def on_bar(u, row):
        bars.append((u, row.name))

    async def on_quote(u, px, ts):
        quotes.append((u, px))

    await feed.poll_once(on_bar, on_quote)
    nifty = [ts for u, ts in bars if u == "NIFTY"]
    # 09:15 and 09:20 bars closed (+1 min lag); 09:25 bar ends 09:30 -> needs 09:31 -> also closed; 09:30 forming
    assert [t.strftime("%H:%M") for t in nifty] == ["09:15", "09:20", "09:25"]
    assert feed.forming["NIFTY"].name.strftime("%H:%M") == "09:30"
    assert ("NIFTY", float(data["close"].iloc[-1])) in quotes
    bars.clear()
    await feed.poll_once(on_bar, on_quote)                           # no duplicates on the next poll
    assert not [b for b in bars if b[0] == "NIFTY"]


def test_session_only_filters_pre_post_market():
    df = _one_min("2026-10-14", "09:00", 30)
    out = session_only(df)
    assert out.index[0].strftime("%H:%M") == "09:15"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    s = Settings()
    s.app.data_dir = tmp_path_factory.mktemp("data2")
    s.app.demo_mode = True
    s.app.data_feed = "synthetic"
    s.news.sources = []
    rt = Runtime(s, start_background=False)
    rt.clock = SimClock(datetime(2026, 10, 14, 11, 0, tzinfo=IST))
    with TestClient(create_app(rt, start_background=False)) as c:
        yield c


def test_agent_switch_gates_auto_mode(client):
    rt = client.app.state.rt
    rt.settings.strategies.execution_mode["orb15"] = ExecutionMode.AUTO
    assert client.get("/api/agent").json()["enabled"] is False
    assert rt.execution_mode("orb15") is ExecutionMode.SIGNAL_ONLY     # agent OFF => no auto trading
    assert client.post("/api/agent", json={"enabled": True}).json()["enabled"] is True
    assert rt.execution_mode("orb15") is ExecutionMode.AUTO
    d = client.get("/api/agent").json()
    assert d["enabled"] and "orb15" in d["auto_strategies"]
    assert any("Agent switched ON" in e["message"] for e in d["log"])
    assert {w["strategy_id"] for w in d["watching"]} >= {"orb15", "vwap_pullback"}
    client.post("/api/agent", json={"enabled": False})


def test_agent_logs_bar_scan(client):
    rt = client.app.state.rt
    client.portal.call(rt.signals._run, "NIFTY")
    log = client.get("/api/agent").json()["log"]
    assert any(e["kind"] == "scan" and "NIFTY" in e["message"] for e in log)


def test_candles_include_markers_and_trade_lines(client):
    d = client.get("/api/candles?underlying=NIFTY&limit=200").json()
    assert "markers" in d and "trade_lines" in d
    assert all({"time", "position", "shape", "text"} <= set(m) for m in d["markers"])


def test_broker_setup_lists_https_redirect(client):
    d = client.get("/api/broker/setup").json()
    redirect = next(f for f in d["smartapi_fields"] if f["field"] == "Redirect URL")
    assert redirect["value"].startswith("https://") and "localhost" not in redirect["value"]
    assert d["connected"] is False


def test_broker_test_without_secrets_explains(client, monkeypatch):
    import indexedge.services.broker_setup as bs

    monkeypatch.setattr(bs, "secrets_status", lambda: {k: False for k in bs.BROKER_SECRETS} | {"TELEGRAM_BOT_TOKEN": False})
    d = client.post("/api/broker/test").json()
    assert d["ok"] is False and "Missing" in d["steps"][0]["detail"]
