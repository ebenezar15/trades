"""SENSEX (BSE index, BFO derivatives) works like NIFTY / BANKNIFTY end to end."""
from __future__ import annotations

from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from indexedge.app import create_app
from indexedge.config.settings import Settings
from indexedge.core.clock import IST, SimClock
from indexedge.core.types import Side, StrategySignal
from indexedge.core.underlyings import INFO, UNDERLYINGS, fo_exchange, index_tokens
from indexedge.data.instruments import InstrumentMaster
from indexedge.data.synthetic import synthetic_master_rows
from indexedge.runtime import Runtime
from indexedge.services.yahoo_feed import TICKERS
from indexedge.strategies.registry import get_strategy


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    s = Settings()
    s.app.data_dir = tmp_path_factory.mktemp("sensex")
    s.app.demo_mode = True
    s.app.data_feed = "synthetic"
    s.news.sources = []
    rt = Runtime(s, start_background=False)
    rt.clock = SimClock(datetime(2026, 10, 14, 11, 0, tzinfo=IST))
    with TestClient(create_app(rt, start_background=False)) as c:
        yield c


def test_registry_and_exchanges():
    assert UNDERLYINGS == ("NIFTY", "BANKNIFTY", "SENSEX")
    sx = INFO["SENSEX"]
    assert (sx.index_exchange, sx.fo_exchange, sx.index_token, sx.yahoo) == ("BSE", "BFO", "99919000", "^BSESN")
    assert fo_exchange("NIFTY") == "NFO" and fo_exchange("SENSEX") == "BFO"
    assert TICKERS["SENSEX"] == "^BSESN"
    # an old config file without SENSEX still gets its token
    assert index_tokens({"NIFTY": "99926000", "BANKNIFTY": "99926009"})["SENSEX"] == "99919000"


def test_master_lookups():
    m = InstrumentMaster.from_json(synthetic_master_rows(date(2026, 10, 16)))
    assert m.index("SENSEX").exchange == "BSE"
    assert m.lot_size("SENSEX", date(2026, 10, 16)) == 20
    fut = m.nearest_future("SENSEX", date(2026, 10, 16))
    assert fut.exchange == "BFO" and fut.expiry.weekday() == 3
    exp = m.expiries("SENSEX", date(2026, 10, 16))[0]
    assert exp == date(2026, 10, 22) and m.strike_step("SENSEX", exp) == 100


def test_api_candles_chain_and_lab(client):
    ov = {i["underlying"] for i in client.get("/api/overview").json()["indices"]}
    assert "SENSEX" in ov
    d = client.get("/api/candles?underlying=SENSEX&limit=50").json()
    assert len(d["candles"]) == 50 and d["candles"][-1]["close"] > 50_000
    ch = client.get("/api/option-chain?underlying=SENSEX").json()
    assert ch["rows"] and date.fromisoformat(ch["expiry"]).weekday() == 3
    assert client.get("/api/strategies?underlying=SENSEX").status_code == 200
    assert client.get("/api/candles?underlying=FINNIFTY").status_code == 400


def test_sensex_ticket_uses_bfo_and_lot_20(client):
    rt = client.app.state.rt
    rt.settings.execution.preferred_instrument = "option"
    feat = rt.signals.compute_features("SENSEX")
    px = float(feat["close"].iloc[-1])
    raw = StrategySignal("ema_pullback", "SENSEX", feat.index[-1].to_pydatetime(), Side.BUY, px, px - 120,
                         [px + 240], ["test"])

    async def go():
        await rt.context.refresh()
        snap = rt.context.latest
        snap.risk_off = False
        sig = rt.composer.compose(raw, feat.iloc[-1], snap, {"n": 120, "wins": 60}, "trend_up", now=rt.clock.now())
        await rt.signals.publish(sig)
        return sig

    sig = client.portal.call(go)
    assert sig.instrument is not None and sig.instrument.exchange == "BFO"
    t = client.get(f"/api/signals/{sig.id}/ticket").json()
    assert t["exchange"] == "BFO" and t["symbol"].startswith("SENSEX") and t["symbol"].endswith("CE")
    assert t["lot_size"] == 20 and t["quantity"] % 20 == 0 and t["order_type"] == "LIMIT"


def test_expiry_straddle_uses_thursday_for_sensex():
    s = get_strategy("expiry_straddle")
    assert s._expiry_for(date(2026, 10, 19), "SENSEX") == date(2026, 10, 22)      # Thu
    assert s._expiry_for(date(2026, 10, 19), "NIFTY") == date(2026, 10, 20)       # Tue
    s.params["expiry_dates"] = {"SENSEX": [date(2026, 10, 21)], "NIFTY": [date(2026, 10, 20)]}
    assert s._expiry_for(date(2026, 10, 19), "SENSEX") == date(2026, 10, 21)      # holiday-shifted from master
    assert isinstance(s.info()["params"]["expiry_dates"], dict)
