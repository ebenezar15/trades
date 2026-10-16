import asyncio
import json
import re
import threading
from datetime import date, datetime
from pathlib import Path

import pyotp
import pytest

from indexedge.broker.angel_one import AngelOneBroker, candles_to_df, map_status, parse_ws_tick
from indexedge.broker.base import BrokerError
from indexedge.broker.rate_limiter import RateLimiter
from indexedge.core.clock import IST, SimClock
from indexedge.core.types import (
    Instrument,
    InstrumentKind,
    OptionType,
    OrderRequest,
    OrderStatus,
    OrderType,
    Side,
)

T0 = datetime(2026, 10, 16, 10, 0, tzinfo=IST)
CE = Instrument("43210", "NIFTY20OCT2625000CE", "NIFTY", "NFO", InstrumentKind.OPTION, lot_size=65,
                tick_size=0.05, expiry=date(2026, 10, 20), strike=25000.0, option_type=OptionType.CE)
SECRETS = {"ANGEL_API_KEY": "APIKEY123", "ANGEL_CLIENT_CODE": "C123456", "ANGEL_PIN": "4321",
           "ANGEL_TOTP_SECRET": pyotp.random_base32()}


class FakeSC:
    def __init__(self, api_key):
        self.api_key = api_key
        self.calls = []
        self.book = []
        self.login_resp = {"status": True, "message": "SUCCESS", "data": {
            "clientcode": "C123456", "jwtToken": "Bearer JWTSECRETTOKEN", "refreshToken": "REFRESHSECRET",
            "feedToken": "FEEDSECRET"}}
        self.place_resp = None
        self.n_orders = 0
        self.place_exc = None
        self.cancel_resp = {"status": True, "data": {"orderid": "OID1"}}
        self.auth_fail_once = False

    def _rec(self, name, *args):
        self.calls.append((name, args))

    def generateSession(self, client, pin, totp):
        self._rec("generateSession", client, pin, totp)
        return self.login_resp

    def generateToken(self, rt):
        self._rec("generateToken", rt)
        return {"status": True, "data": {"jwtToken": "NEWJWT", "feedToken": "FEED2", "refreshToken": "R2"}}

    def terminateSession(self, client):
        self._rec("terminateSession", client)
        return {"status": True, "data": "ok"}

    def placeOrderFullResponse(self, params):
        self._rec("placeOrderFullResponse", params)
        if self.place_exc:
            raise self.place_exc
        if self.place_resp is not None:
            return self.place_resp
        self.n_orders += 1
        return {"status": True, "message": "SUCCESS", "data": {"orderid": f"OID{self.n_orders}"}}

    def modifyOrder(self, params):
        self._rec("modifyOrder", params)
        return {"status": True, "data": {"orderid": params["orderid"]}}

    def cancelOrder(self, oid, variety):
        self._rec("cancelOrder", oid, variety)
        return self.cancel_resp

    def orderBook(self):
        self._rec("orderBook")
        if self.auth_fail_once:
            self.auth_fail_once = False
            return {"status": False, "message": "Invalid Token", "errorcode": "AG8001", "data": None}
        return {"status": True, "data": self.book or None}

    def tradeBook(self):
        return {"status": True, "data": [{"orderid": "OID1", "symboltoken": CE.token, "tradingsymbol": CE.symbol,
                                          "exchange": "NFO", "transactiontype": "BUY", "fillsize": "65",
                                          "fillprice": "120.5", "filltime": "10:01:02"}]}

    def position(self):
        return {"status": True, "data": [{"symboltoken": CE.token, "tradingsymbol": CE.symbol, "exchange": "NFO",
                                          "netqty": "-65", "avgnetprice": "120.50", "ltp": "110.0",
                                          "realised": "100.5", "instrumenttype": "OPTIDX", "optiontype": "CE",
                                          "strikeprice": "25000", "expirydate": "20OCT2026", "lotsize": "65"}]}

    def rmsLimit(self):
        return {"status": True, "data": {"net": "150000.5", "availablecash": "120000", "utiliseddebits": "30000"}}

    def ltpData(self, exch, sym, token):
        self._rec("ltpData", exch, sym, token)
        return {"status": True, "data": {"ltp": 25012.35}}

    def getCandleData(self, params):
        self._rec("getCandleData", params)
        return {"status": True, "data": [["2026-10-15T09:15:00+05:30", 1, 2, 0.5, 1.5, 100],
                                         ["2026-10-15T09:20:00+05:30", 1.5, 2.5, 1, 2, 50]]}


class FakeWS:
    def __init__(self, *args, fail_first=0):
        self.args = args
        self.subs = []
        self.closed = threading.Event()
        self.fail_first = fail_first

    def connect(self):
        if self.fail_first:
            raise ConnectionError("refused")
        self.on_open(None)
        self.closed.wait(5)

    def subscribe(self, cid, mode, token_list):
        self.subs.append((cid, mode, token_list))

    def close_connection(self):
        self.closed.set()


class Env:
    def __init__(self, tmp_path, dry_run=False, ws_fail=0, order_updates=False):
        self.sc = None
        self.ws = []
        self.order_ws = []
        self.fail = ws_fail

        def connect_factory(api_key):
            self.sc = FakeSC(api_key)
            return self.sc

        def ws_factory(*args):
            ws = FakeWS(*args, fail_first=1 if self.fail > 0 else 0)
            self.fail -= 1
            self.ws.append(ws)
            return ws

        def order_ws_factory(*args):
            ws = FakeWS(*args)
            self.order_ws.append(ws)
            return ws

        self.broker = AngelOneBroker(
            secrets_getter=SECRETS.get, clock=SimClock(T0), connect_factory=connect_factory,
            ws_factory=ws_factory, order_ws_factory=order_ws_factory, dry_run=dry_run,
            audit_dir=tmp_path / "audit", order_updates=order_updates, reconnect_base_s=0.01,
            order_limiter=RateLimiter(1000, burst=1000))
        for k in self.broker._limiters:
            self.broker._limiters[k] = RateLimiter(1000, burst=1000)
        self.audit = tmp_path / "audit" / "2026-10-16.jsonl"


async def wait_for(cond, timeout=2.0):
    for _ in range(int(timeout / 0.01)):
        if cond():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met")


def req(order_type=OrderType.LIMIT, trig=None, side=Side.BUY, price=120.0):
    return OrderRequest(CE, side, 65, order_type, price, trigger_price=trig, tag="orb15")


async def test_login_totp_and_redacted_audit(tmp_path):
    env = Env(tmp_path)
    await env.broker.connect()
    assert env.broker.connected and env.sc.api_key == "APIKEY123"
    _, (client, pin, totp) = env.sc.calls[0]
    assert (client, pin) == ("C123456", "4321") and totp == pyotp.TOTP(SECRETS["ANGEL_TOTP_SECRET"]).now()
    await env.broker.place_order(req())
    await env.broker.disconnect()
    text = env.audit.read_text()
    assert "generateSession" in text and "placeOrderFullResponse" in text
    for secret in ["APIKEY123", "C123456", SECRETS["ANGEL_TOTP_SECRET"], "JWTSECRETTOKEN", "REFRESHSECRET",
                   "FEEDSECRET"]:
        assert secret not in text
    assert '"4321"' not in text and f'"{totp}"' not in text  # PIN / TOTP code are never written
    assert all(json.loads(line)["ts"] for line in text.splitlines())
    assert ("terminateSession", ("C123456",)) in env.sc.calls and not env.broker.connected


async def test_login_failures(tmp_path):
    b = AngelOneBroker(secrets_getter=lambda n: None, audit_dir=tmp_path)
    with pytest.raises(BrokerError, match="missing secrets"):
        await b.connect()
    env = Env(tmp_path)
    orig = env.broker._connect_factory

    def bad_login(api_key):
        sc = orig(api_key)
        sc.login_resp = {"status": False, "message": "Invalid totp", "errorcode": "AB1050", "data": None}
        return sc

    env.broker._connect_factory = bad_login
    with pytest.raises(BrokerError, match="Invalid totp"):
        await env.broker.connect()
    assert not env.broker.connected


async def test_order_params_mapping(tmp_path):
    env = Env(tmp_path)
    await env.broker.connect()
    seen = []
    env.broker.on_order_update(lambda o: seen.append(o.status))
    o = await env.broker.place_order(req(price=120.03))
    assert o.broker_id == "OID1" and o.status is OrderStatus.PENDING and seen == [OrderStatus.PENDING]
    params = env.sc.calls[-1][1][0]
    assert params == {"variety": "NORMAL", "tradingsymbol": CE.symbol, "symboltoken": CE.token, "exchange": "NFO",
                      "transactiontype": "BUY", "ordertype": "LIMIT", "producttype": "INTRADAY",
                      "duration": "DAY", "price": "120.05", "triggerprice": "0", "quantity": "65",
                      "ordertag": o.request.client_id}
    await env.broker.place_order(req(OrderType.SL_LIMIT, trig=100.0, side=Side.SELL, price=99.5))
    p2 = env.sc.calls[-1][1][0]
    assert (p2["variety"], p2["ordertype"], p2["triggerprice"], p2["price"], p2["duration"]) == \
        ("STOPLOSS", "STOPLOSS_LIMIT", "100.00", "99.50", "DAY")
    bad = req()
    object.__setattr__(bad, "order_type", "MARKET")
    with pytest.raises(BrokerError, match="not allowed"):
        await env.broker.place_order(bad)
    m = await env.broker.modify_order(o, 121.0)
    mp = env.sc.calls[-1][1][0]
    assert mp["orderid"] == "OID1" and mp["price"] == "121.00" and "ordertag" not in mp
    assert m.request.price == 121.0


async def test_rejected_place_and_blocked_orders(tmp_path):
    env = Env(tmp_path)
    await env.broker.connect()
    env.sc.place_resp = {"status": False, "message": "Insufficient funds", "errorcode": "AB4036", "data": None}
    o = await env.broker.place_order(req())
    assert o.status is OrderStatus.REJECTED and "Insufficient funds" in o.message
    env.broker.block_orders("static IP mismatch")
    with pytest.raises(BrokerError, match="static IP"):
        await env.broker.place_order(req())
    env.broker.block_orders(None)


async def test_send_timeout_reconciles_by_tag(tmp_path):
    env = Env(tmp_path)
    await env.broker.connect()
    r = req()
    env.sc.place_exc = TimeoutError("read timeout")
    env.sc.book = [{"orderid": "OID9", "status": "open", "ordertype": "LIMIT", "transactiontype": "BUY",
                    "quantity": "65", "filledshares": "0", "price": "120", "symboltoken": CE.token,
                    "tradingsymbol": CE.symbol, "exchange": "NFO", "producttype": "INTRADAY",
                    "ordertag": r.client_id}]
    o = await env.broker.place_order(r)
    assert o.broker_id == "OID9" and o.status is OrderStatus.OPEN
    env.sc.book = []
    o2 = await env.broker.place_order(req())
    assert o2.status is OrderStatus.REJECTED and "not found" in o2.message


async def test_dry_run_never_sends(tmp_path):
    env = Env(tmp_path, dry_run=True)
    await env.broker.connect()
    o = await env.broker.place_order(req())
    assert o.status is OrderStatus.OPEN and o.broker_id.startswith("DRY")
    await env.broker.modify_order(o, 125.0)
    c = await env.broker.cancel_order(o)
    assert c.status is OrderStatus.CANCELLED
    sent = {c[0] for c in env.sc.calls}
    assert not sent & {"placeOrderFullResponse", "modifyOrder", "cancelOrder"}
    assert "dry_run" in env.audit.read_text()
    assert o in await env.broker.order_book()


async def test_order_book_status_mapping_and_updates(tmp_path):
    env = Env(tmp_path)
    await env.broker.connect()
    o = await env.broker.place_order(req())
    seen = []
    env.broker.on_order_update(lambda x: seen.append((x.status, x.filled_qty)))
    row = {"orderid": "OID1", "status": "open", "ordertype": "LIMIT", "transactiontype": "BUY", "quantity": "65",
           "filledshares": "0", "price": "120", "symboltoken": CE.token, "tradingsymbol": CE.symbol,
           "exchange": "NFO", "producttype": "INTRADAY", "averageprice": "0"}
    env.sc.book = [row, {**row, "orderid": "MANUAL", "ordertype": "MARKET"}]
    book = await env.broker.order_book()
    assert book == [o] and o.status is OrderStatus.OPEN
    env.sc.book = [{**row, "status": "complete", "filledshares": "65", "averageprice": "119.95"}]
    await env.broker.order_book()
    await env.broker.order_book()  # unchanged -> no duplicate event
    assert seen == [(OrderStatus.OPEN, 0), (OrderStatus.FILLED, 65)] and o.avg_price == 119.95
    # cancel refused because already complete -> refreshed terminal order returned
    env.sc.cancel_resp = {"status": False, "message": "order already complete", "errorcode": "AB4008"}
    assert (await env.broker.cancel_order(o)).status is OrderStatus.FILLED


def test_map_status():
    assert map_status("open") is OrderStatus.OPEN
    assert map_status("trigger pending") is OrderStatus.TRIGGER_PENDING
    assert map_status("complete", 65, 65) is OrderStatus.FILLED
    assert map_status("cancelled", 20, 65) is OrderStatus.CANCELLED
    assert map_status("rejected") is OrderStatus.REJECTED
    assert map_status("Open", 20, 65) is OrderStatus.PARTIAL
    assert map_status("validation pending") is OrderStatus.PENDING
    assert map_status("modify pending") is OrderStatus.OPEN
    assert map_status("something new") is OrderStatus.PENDING


async def test_auth_expiry_relogin_and_refresh(tmp_path):
    env = Env(tmp_path)
    await env.broker.connect()
    first_sc = env.sc
    first_sc.auth_fail_once = True
    await env.broker.order_book()
    assert env.sc is not first_sc and any(c[0] == "orderBook" for c in env.sc.calls)
    await env.broker.refresh()
    assert env.broker._jwt == "Bearer NEWJWT" and env.broker._feed_token == "FEED2"


async def test_market_data_and_books(tmp_path):
    env = Env(tmp_path)
    await env.broker.connect()
    df = await env.broker.get_candles("NSE", "99926000", "1m", datetime(2026, 8, 1, 9, 15, tzinfo=IST),
                                      datetime(2026, 10, 15, 15, 30, tzinfo=IST))
    calls = [c for c in env.sc.calls if c[0] == "getCandleData"]
    assert len(calls) == 3 and calls[0][1][0]["interval"] == "ONE_MINUTE"
    assert calls[0][1][0]["fromdate"] == "2026-08-01 09:15"
    assert len(df) == 2 and str(df.index.tz) == str(IST) and df.close.tolist() == [1.5, 2.0]
    assert await env.broker.get_ltp("NSE", "99926000", "Nifty 50") == 25012.35
    f = await env.broker.funds()
    assert (f.available_cash, f.used_margin, f.net) == (120000, 30000, 150000.5)
    pos = (await env.broker.positions())[0]
    assert pos.net_qty == -65 and pos.avg_price == 120.5 and pos.instrument.option_type is OptionType.CE
    fill = (await env.broker.trade_book())[0]
    assert fill.quantity == 65 and fill.ts == datetime(2026, 10, 16, 10, 1, 2, tzinfo=IST)


def test_candles_to_df_and_tick_parse():
    assert candles_to_df([]).empty
    msg = {"token": "99926000", "exchange_timestamp": 1792129800000, "last_traded_price": 2501235,
           "volume_trade_for_the_day": 1000, "open_interest": 50, "closed_price": 2490000,
           "best_5_buy_data": [{"price": 2501300, "quantity": 65}],  # SDK swaps sides
           "best_5_sell_data": [{"price": 2501200, "quantity": 65}]}
    t = parse_ws_tick(msg)
    assert t.ltp == 25012.35 and t.close == 24900.0 and t.volume == 1000 and t.oi == 50
    assert (t.bid, t.ask) == (25012.0, 25013.0) and t.ts.tzinfo is not None


async def test_ticks_dispatched_from_ws_thread(tmp_path):
    env = Env(tmp_path)
    got = []

    async def handler(t):
        got.append((t.token, t.ltp, threading.current_thread() is threading.main_thread()))

    await env.broker.subscribe_ticks([("NSE", "99926000")], handler)
    await env.broker.connect()
    await wait_for(lambda: env.broker.ws_status["tick"] == "connected")
    ws = env.ws[0]
    assert ws.subs == [("idxedge001", 3, [{"exchangeType": 1, "tokens": ["99926000"]}])]
    threading.Thread(target=ws.on_data, args=(None, {"token": "99926000", "last_traded_price": 2500005,
                                                     "exchange_timestamp": 1792129800000})).start()
    await wait_for(lambda: got)
    assert got == [("99926000", 25000.05, True)]
    await env.broker.subscribe_ticks([("NFO", CE.token)], handler)
    assert ws.subs[-1] == ("idxedge001", 3, [{"exchangeType": 2, "tokens": [CE.token]}])
    assert env.broker.health()["last_tick_age_s"] is not None
    await env.broker.disconnect()
    assert env.broker.ws_status["tick"] == "stopped"


async def test_ws_reconnects_with_backoff(tmp_path):
    env = Env(tmp_path, ws_fail=2)
    await env.broker.subscribe_ticks([("NSE", "99926009")], lambda t: None)
    await env.broker.connect()
    await wait_for(lambda: env.broker.ws_status["tick"] == "connected")
    assert len(env.ws) == 3 and env.broker._ws_attempts["tick"] == 0
    env.ws[-1].close_connection()  # server drops connection -> reconnect + resubscribe
    await wait_for(lambda: len(env.ws) == 4 and env.broker.ws_status["tick"] == "connected")
    assert env.ws[-1].subs[0][2] == [{"exchangeType": 1, "tokens": ["99926009"]}]
    await env.broker.disconnect()


async def test_order_update_websocket(tmp_path):
    env = Env(tmp_path, order_updates=True)
    await env.broker.connect()
    o = await env.broker.place_order(req())
    seen = []
    env.broker.on_order_update(lambda x: seen.append(x.status))
    await wait_for(lambda: env.order_ws and env.broker.ws_status["order"] == "connected")
    ws = env.order_ws[0]
    msg = {"user-id": "C123456", "status-code": "200", "order-status": "AB00", "orderData": {
        "orderid": "OID1", "status": "complete", "quantity": "65", "filledshares": "65", "averageprice": "120",
        "ordertype": "LIMIT", "transactiontype": "BUY"}}
    ws.on_message(None, json.dumps({"status-code": "200", "order-status": "AB00"}))  # connect ack ignored
    threading.Thread(target=ws.on_message, args=(None, json.dumps(msg))).start()
    await wait_for(lambda: seen)
    assert seen == [OrderStatus.FILLED] and o.status is OrderStatus.FILLED and o.filled_qty == 65
    await env.broker.disconnect()


def test_no_forbidden_order_types_in_broker_code():
    root = Path(__file__).resolve().parents[2] / "indexedge" / "broker"
    pattern = re.compile(r"""["'](MARKET|STOPLOSS_MARKET|IOC|AMO)["']""")
    for path in root.glob("*.py"):
        assert not pattern.search(path.read_text()), path.name
