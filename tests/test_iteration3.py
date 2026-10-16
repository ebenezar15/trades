"""iPad/remote login, Cloudflare-tunnel safety, manual-execution tickets and alerts."""
from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import TestClient as RawClient

from indexedge.app import create_app
from indexedge.config.settings import Settings
from indexedge.core.clock import IST, SimClock
from indexedge.core.types import Side, StrategySignal
from indexedge.runtime import Runtime
from indexedge.services.tunnel import URL_RE, asset_name


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    s = Settings()
    s.app.data_dir = tmp_path_factory.mktemp("data3")
    s.app.demo_mode = True
    s.app.data_feed = "synthetic"
    s.news.sources = []
    rt = Runtime(s, start_background=False)
    rt.clock = SimClock(datetime(2026, 10, 14, 11, 0, tzinfo=IST))
    with TestClient(create_app(rt, start_background=False)) as c:
        yield c


def _ipad(client) -> RawClient:
    return RawClient(client.app, client=("192.168.1.20", 5000))


def _tunnel(client) -> RawClient:
    """A request arriving through cloudflared: loopback + proxy headers."""
    return RawClient(client.app, client=("127.0.0.1", 5001), base_url="https://testserver",
                     headers={"cf-connecting-ip": "203.0.113.9", "x-forwarded-proto": "https", "cf-ray": "x"})


def test_tunnel_requests_are_not_trusted_as_local(client):
    t = _tunnel(client)
    assert t.get("/api/status").status_code == 401
    st = t.get("/api/auth/status").json()
    assert st["local"] is False and st["logged_in"] is False and "setup_code" not in st
    assert t.get("/").status_code == 200            # the web app loads and shows the login screen


def test_setup_from_ipad_needs_code_and_login_works(client):
    auth = client.app.state.auth
    ipad = _ipad(client)
    assert ipad.post("/api/auth/setup", json={"username": "me", "password": "longpassword"}).status_code == 403
    code = client.get("/api/auth/status").json()["setup_code"]          # shown on the PC only
    r = ipad.post("/api/auth/setup", json={"username": "me", "password": "longpassword", "setup_code": code})
    assert r.status_code == 200 and auth.password_set
    assert ipad.get("/api/status").status_code == 200                  # session cookie set
    other = _tunnel(client)
    assert other.post("/api/auth/login", json={"username": "me", "password": "nope"}).status_code == 401
    ok = other.post("/api/auth/login", json={"username": "ME", "password": "longpassword"})
    assert ok.status_code == 200 and "ie_session" in ok.headers["set-cookie"] and "Secure" in ok.headers["set-cookie"]
    assert other.get("/api/status").status_code == 200
    other.post("/api/auth/logout")
    assert other.get("/api/status").status_code == 401


def test_brute_force_lockout(client):
    t = RawClient(client.app, client=("10.0.0.99", 1))
    for _ in range(5):
        assert t.post("/api/auth/login", json={"username": "me", "password": "bad"}).status_code == 401
    assert t.post("/api/auth/login", json={"username": "me", "password": "longpassword"}).status_code == 429


def test_remote_needs_password_and_cloudflare_url_parsing():
    assert URL_RE.search("INF |  https://quiet-river-blue-moon.trycloudflare.com  |").group(0).endswith(
        ".trycloudflare.com")
    assert URL_RE.search('failed to request quick Tunnel: Post "https://api.trycloudflare.com/tunnel"') is None
    assert asset_name() is None or asset_name().startswith("cloudflared-")


def test_ticket_manual_entry_alert_and_exit(client):
    rt = client.app.state.rt
    rt.settings.execution.preferred_instrument = "option"
    feat = rt.signals.compute_features("NIFTY")
    px = float(feat["close"].iloc[-1])
    raw = StrategySignal("ema_pullback", "NIFTY", feat.index[-1].to_pydatetime(), Side.BUY, px, px - 40, [px + 80],
                         ["test"])

    async def go():
        await rt.context.refresh()
        snap = rt.context.latest
        snap.risk_off = False
        sig = rt.composer.compose(raw, feat.iloc[-1], snap, {"n": 120, "wins": 60}, "trend_up", now=rt.clock.now())
        await rt.signals.publish(sig)
        return sig

    sig = client.portal.call(go)
    t = client.get(f"/api/signals/{sig.id}/ticket").json()
    assert t["symbol"].endswith("CE") and t["side"] == "BUY" and t["order_type"] == "LIMIT"
    assert t["quantity"] % t["lot_size"] == 0 and t["stop_trigger"] < t["limit_price"] < t["target"]
    assert t["index_levels"]["stop"] == pytest.approx(px - 40)
    r = client.post(f"/api/signals/{sig.id}/manual-entry", json={"fill_price": t["limit_price"],
                                                                 "quantity": t["quantity"]}).json()
    tid = r["trade_id"]
    pos = client.get("/api/positions").json()["open_trades"]
    assert any(p["id"] == tid and p["mode"] == "manual" for p in pos)
    alerts = []
    rt.bus.subscribe("alert", lambda a: alerts.append(a))
    rt.market.ltp["NIFTY"] = px - 50                                   # index falls through the stop
    client.portal.call(rt.manual.check)
    client.portal.call(rt.manual.check)                                # second check must not re-alert
    stop_alerts = [a for a in alerts if a["title"].startswith("STOP HIT")]
    assert len(stop_alerts) == 1
    pos = client.get("/api/positions").json()["open_trades"]
    assert next(p for p in pos if p["id"] == tid)["alert"].startswith("STOP HIT")
    assert client.post(f"/api/trades/{tid}/manual-exit", json={"exit_price": t["stop_trigger"]}).json()["ok"]
    row = client.get(f"/api/trades/{tid}").json()
    assert row["status"] == "closed" and row["mode"] == "manual" and row["pnl"] < 0


def test_pc_can_reset_forgotten_password_but_remote_cannot(client):
    auth = client.app.state.auth
    if not auth.password_set:
        auth.set_password("me", "longpassword")
    remote = _tunnel(client)
    remote.post("/api/auth/login", json={"username": "me", "password": "longpassword"})
    assert remote.post("/api/auth/password", json={"current": "wrong", "new": "newpassword1"}).status_code == 403
    assert client.post("/api/auth/password", json={"new": "newpassword1"}).status_code == 200   # on the PC
    assert auth.check_password("me", "newpassword1")
