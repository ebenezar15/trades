"""End-to-end API tests on the real runtime in demo mode (synthetic data, paper broker, no network)."""
from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from indexedge.app import create_app
from indexedge.config.settings import Settings
from indexedge.core.clock import IST, SimClock
from indexedge.core.types import ExecutionMode
from indexedge.runtime import Runtime


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    s = Settings()
    s.app.data_dir = tmp_path_factory.mktemp("data")
    s.app.demo_mode = True
    s.news.sources = []
    rt = Runtime(s, start_background=False)
    rt.clock = SimClock(datetime(2026, 10, 14, 11, 0, tzinfo=IST))   # a Wednesday, market open
    app = create_app(rt, start_background=False)
    with TestClient(app) as c:
        yield c


def test_status_and_overview(client):
    st = client.get("/api/status").json()
    assert st["mode"] == "paper" and st["demo_mode"] is True and st["data_source"] == "synthetic"
    ov = client.get("/api/overview").json()["indices"]
    assert {i["underlying"] for i in ov} == {"NIFTY", "BANKNIFTY"}
    assert all(i["regime"] != "unknown" for i in ov)


def test_candles_overlays_levels(client):
    d = client.get("/api/candles?underlying=NIFTY&limit=100").json()
    assert len(d["candles"]) == 100 and d["overlays"]["ema9"] and "pivot" in d["levels"]
    assert client.get("/api/candles?underlying=XYZ").status_code == 400


def test_option_chain_has_atm(client):
    d = client.get("/api/option-chain?underlying=NIFTY").json()
    strikes = [r["strike"] for r in d["rows"]]
    assert min(strikes) < d["spot"] < max(strikes)
    assert d["pcr"] > 0 and d["rows"][0]["ce"]["iv"] is not None


def test_strategies_listed_with_learn(client):
    d = client.get("/api/strategies").json()["strategies"]
    assert len(d) == 13 and all(s["learn"] for s in d)


def test_backtest_endpoint_labels_synthetic(client):
    d = client.post("/api/backtest", json={"strategy_id": "orb15", "underlying": "NIFTY", "days": 40}).json()
    assert d["data_source"] == "synthetic" and d["real_performance"] is False
    assert "status" in d["report"]


def test_replay_and_quiz(client):
    days = client.get("/api/replay/days").json()["days"]
    r = client.get(f"/api/replay?underlying=NIFTY&day={days[0]}").json()
    assert len(r["candles"]) == 75
    q = client.get("/api/learn/quiz").json()
    assert q["answer_id"] in {"take", "skip"}


def test_live_mode_requires_typed_confirmation(client):
    r = client.post("/api/mode", json={"mode": "live", "confirm_text": "yes"})
    assert r.status_code == 400 and "I UNDERSTAND THE RISK" in r.json()["detail"]


def test_risk_limits_locked_during_market(client):
    r = client.put("/api/settings", json={"risk": {"risk_per_trade_pct": 5}})
    assert r.status_code == 409


def test_settings_never_expose_secrets(client):
    d = client.get("/api/settings").json()
    assert set(d["secrets"]) >= {"ANGEL_API_KEY", "ANGEL_PIN"}
    assert all(isinstance(v, bool) for v in d["secrets"].values())


def test_premarket_eod_health(client):
    assert client.get("/api/premarket").json()["key_levels"]["NIFTY"]
    assert client.get("/api/eod").json()["trades"] == 0
    h = client.get("/api/health").json()
    assert isinstance(h["ws_status"], str) and isinstance(h["jobs"], list)


def test_signal_confirm_paper_trade_and_kill_switch(client):
    """Drive one synthetic signal through compose -> confirm -> paper execution -> kill switch."""
    import asyncio

    rt = client.app.state.rt
    rt.settings.strategies.execution_mode["ema_supertrend_adx"] = ExecutionMode.CONFIRM
    rt.settings.strategies.enabled["ema_supertrend_adx"] = True
    rt.settings.execution.preferred_instrument = "future"
    rt.settings.risk.capital = 1_000_000
    rt.paper.capital = 1_000_000
    rt.refresh_composer_settings()
    feat = rt.signals.compute_features("NIFTY")
    from indexedge.core.types import Side, StrategySignal

    last = feat.iloc[-1]
    px = float(last["close"])
    raw = StrategySignal("ema_supertrend_adx", "NIFTY", feat.index[-1].to_pydatetime(), Side.BUY, px, px - 30,
                         [px + 60], ["test setup"])
    rt.context.latest = None

    async def go():
        await rt.context.refresh()
        snap = rt.context.latest
        snap.risk_off = False
        snap.macro_bias = 0.3
        sig = rt.composer.compose(raw, last, snap, {"n": 120, "wins": 66}, "trend_up", now=rt.clock.now())
        await rt.signals.publish(sig)
        from indexedge.core.types import Tick

        if sig.instrument is not None:
            await rt.market.on_tick(Tick(sig.instrument.token, rt.clock.now(), px))
            await rt.paper.feed_tick(Tick(sig.instrument.token, rt.clock.now(), px))
            await rt.trades.on_tick(Tick(sig.instrument.token, rt.clock.now(), px))
        return sig

    sig = client.portal.call(go) if hasattr(client, "portal") and client.portal else asyncio.run(go())
    assert sig.status in ("pending_confirm", "blocked"), sig.blocked_reason
    if sig.status == "blocked":
        pytest.skip(f"signal blocked by gate: {sig.blocked_reason}")
    lst = client.get("/api/signals?status=pending_confirm").json()["signals"]
    assert any(s["id"] == sig.id for s in lst)
    res = client.post(f"/api/signals/{sig.id}/confirm").json()
    assert res["ok"] is True, res
    trades = client.get("/api/trades").json()["trades"]
    assert trades and trades[0]["status"] == "open" and trades[0]["mode"] == "paper"
    pos = client.get("/api/positions").json()
    assert pos["open_trades"]
    ks = client.post("/api/kill-switch").json()
    assert ks["ok"] is True
    assert client.get("/api/status").json()["risk"]["locked"] is True
    assert client.get("/api/positions").json()["open_trades"] == []
    assert client.get("/api/trades").json()["trades"][0]["status"] == "closed"


def test_lan_device_needs_pairing(client):
    from starlette.testclient import TestClient as RawClient

    app = client.app
    token = app.state.access_token
    phone = RawClient(app, client=("192.168.1.50", 50000))
    assert phone.get("/api/status").status_code == 401
    assert phone.post("/api/kill-switch").status_code == 401
    r = phone.get(f"/?token={token}", follow_redirects=False)
    assert r.status_code == 303 and "ie_token" in r.headers["set-cookie"]
    assert phone.get("/api/status").status_code == 200          # cookie now stored
    assert "phone_url" not in phone.get("/api/settings").json()  # link only shown on the PC itself
    assert "phone_url" in client.get("/api/settings").json()
