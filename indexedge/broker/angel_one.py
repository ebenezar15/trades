"""Angel One SmartAPI broker (smartapi-python), see docs/SMARTAPI_NOTES.md.

Design
------
* The SDK is synchronous and ``import SmartApi`` performs network I/O, so it is imported lazily inside the
  default factories and every REST call runs in a worker thread (``asyncio.to_thread``). Tests inject fakes via
  ``connect_factory`` / ``ws_factory`` / ``order_ws_factory``.
* Login: client code + PIN + TOTP (``pyotp``) -> ``generateSession``. ``refresh()`` uses ``generateToken`` and
  falls back to a full login. Auth-expiry error codes trigger one transparent re-login + retry.
* Orders: LIMIT -> variety NORMAL / ordertype LIMIT; SL-L -> variety STOPLOSS / ordertype STOPLOSS_LIMIT;
  duration always DAY. A final guard refuses any other combination. place/modify/cancel share one
  ``RateLimiter`` (default 8/s). ``ordertag`` = ``OrderRequest.client_id`` so an order whose send timed out
  can be found again in the order book.
* ``dry_run=True`` logs in and streams data but never sends place/modify/cancel to the broker.
* Ticks: SmartWebSocketV2 (SnapQuote) runs in a daemon thread; prices arrive in paise and are converted to
  rupees; ticks are handed to the asyncio loop with ``loop.call_soon_threadsafe``. Both websockets reconnect
  with exponential backoff (SDK-internal retries disabled).
* Every broker request/response is appended to ``<audit_dir>/YYYY-MM-DD.jsonl`` with secrets redacted.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import threading
import time as _time
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pyotp

from indexedge.broker.base import BrokerBase, BrokerError, TickHandler
from indexedge.broker.rate_limiter import RateLimiter
from indexedge.config.secrets import get_secret
from indexedge.config.settings import Settings
from indexedge.core.clock import IST, Clock
from indexedge.core.types import (
    CANDLE_COLUMNS,
    Fill,
    Funds,
    Instrument,
    InstrumentKind,
    OptionType,
    Order,
    OrderRequest,
    OrderStatus,
    OrderType,
    Position,
    ProductType,
    Side,
    Tick,
    new_id,
)
from indexedge.data.history import INTERVALS, chunks_for
from indexedge.data.instruments import parse_expiry
from indexedge.data.store import empty_candles

log = logging.getLogger(__name__)

EXCHANGE_TYPES = {"NSE": 1, "NFO": 2, "BSE": 3, "BFO": 4, "MCX": 5}
SNAP_QUOTE = 3
CORRELATION_ID = "idxedge001"     # SmartWebSocketV2 wants a 10-char id
MAX_SUBSCRIPTIONS = 1000
AUTH_ERROR_CODES = {"AG8001", "AG8002", "AG8003", "AB8050", "AB8051", "AB1010"}

# Only these combinations may ever reach the broker (SEBI / Angel One algo rules).
_ORDER_MAP = {
    OrderType.LIMIT: ("NORMAL", "LIMIT"),
    OrderType.SL_LIMIT: ("STOPLOSS", "STOPLOSS_LIMIT"),
}
_ALLOWED_WIRE = {("NORMAL", "LIMIT"), ("STOPLOSS", "STOPLOSS_LIMIT")}

_STATUS_MAP = {
    "open": OrderStatus.OPEN,
    "modified": OrderStatus.OPEN,
    "not modified": OrderStatus.OPEN,
    "not cancelled": OrderStatus.OPEN,
    "modify pending": OrderStatus.OPEN,
    "modify validation pending": OrderStatus.OPEN,
    "modify order req received": OrderStatus.OPEN,
    "cancel pending": OrderStatus.OPEN,
    "trigger pending": OrderStatus.TRIGGER_PENDING,
    "complete": OrderStatus.FILLED,
    "cancelled": OrderStatus.CANCELLED,
    "rejected": OrderStatus.REJECTED,
    "open pending": OrderStatus.PENDING,
    "validation pending": OrderStatus.PENDING,
    "put order req received": OrderStatus.PENDING,
    "after market order req received": OrderStatus.PENDING,
}

_REDACT_KEYS = {
    "password", "pin", "totp", "jwttoken", "refreshtoken", "feedtoken", "authorization", "api_key",
    "apikey", "x-api-key", "access_token", "auth_token", "feed_token", "refresh_token", "secret",
    "totp_secret", "clientcode", "client_code",
}


def map_status(status: str, filled: int = 0, quantity: int = 0) -> OrderStatus:
    """Map an Angel One order-book status string to :class:`OrderStatus` (partial fills -> PARTIAL)."""
    st = _STATUS_MAP.get(str(status or "").strip().lower(), OrderStatus.PENDING)
    if st in (OrderStatus.OPEN, OrderStatus.PENDING) and 0 < filled < quantity:
        return OrderStatus.PARTIAL
    return st


def _num(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _paise(v: Any) -> float | None:
    return None if v is None else float(v) / 100.0


def parse_ws_tick(msg: dict) -> Tick:
    """Convert a parsed SmartWebSocketV2 message (prices in paise) into a :class:`Tick` (rupees, IST)."""
    ts_ms = msg.get("exchange_timestamp")
    ts = datetime.fromtimestamp(ts_ms / 1000.0, IST) if ts_ms else datetime.now(IST)
    bid = ask = None
    tops = [lvl[0].get("price") for lvl in (msg.get("best_5_buy_data"), msg.get("best_5_sell_data"))
            if lvl and isinstance(lvl[0], dict) and lvl[0].get("price")]
    if len(tops) == 2:  # the SDK swaps buy/sell sides in SnapQuote; best bid is the lower price
        bid, ask = min(tops) / 100.0, max(tops) / 100.0
    return Tick(
        token=str(msg.get("token", "")),
        ts=ts,
        ltp=float(msg.get("last_traded_price", 0)) / 100.0,
        volume=float(msg.get("volume_trade_for_the_day") or 0),
        oi=float(msg.get("open_interest") or 0),
        bid=bid,
        ask=ask,
        open=_paise(msg.get("open_price_of_the_day")),
        high=_paise(msg.get("high_price_of_the_day")),
        low=_paise(msg.get("low_price_of_the_day")),
        close=_paise(msg.get("closed_price")),
    )


def candles_to_df(rows: list | None) -> pd.DataFrame:
    """``getCandleData`` rows ``[ts, o, h, l, c, v]`` -> candle DataFrame indexed by IST bar-open time."""
    if not rows:
        return empty_candles()
    df = pd.DataFrame(rows, columns=["ts", *CANDLE_COLUMNS])
    idx = pd.DatetimeIndex(pd.to_datetime(df.pop("ts")))
    df.index = idx.tz_localize(IST) if idx.tz is None else idx.tz_convert(IST)
    df.index.name = None
    df = df.astype(float).sort_index()
    return df[~df.index.duplicated(keep="last")]


class AuditLog:
    """Thread-safe JSON-lines audit log with key- and value-based secret redaction."""

    def __init__(self, directory: Path, secret_values: Callable[[], list[str]] | None = None,
                 clock: Clock | None = None) -> None:
        self.directory = Path(directory)
        self._secret_values = secret_values or (list)
        self._clock = clock or Clock()
        self._lock = threading.Lock()

    def path_for(self, day: date) -> Path:
        return self.directory / f"{day.isoformat()}.jsonl"

    def _redact(self, obj: Any, secrets: list[str]) -> Any:
        if isinstance(obj, dict):
            return {k: ("***" if str(k).lower() in _REDACT_KEYS and v not in (None, "") else
                        self._redact(v, secrets)) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self._redact(v, secrets) for v in obj]
        if isinstance(obj, str):
            for s in secrets:
                if s and len(s) >= 6 and s in obj:
                    obj = obj.replace(s, "***")
            return obj
        if obj is None or isinstance(obj, (int, float, bool)):
            return obj
        return self._redact(str(obj), secrets)

    def write(self, kind: str, **payload: Any) -> None:
        try:
            now = self._clock.now()
            rec = {"ts": now.isoformat(), "kind": kind,
                   **self._redact(payload, [s for s in self._secret_values() if s])}
            line = json.dumps(rec, default=str)
            with self._lock:
                self.directory.mkdir(parents=True, exist_ok=True)
                with open(self.path_for(now.date()), "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
        except Exception:  # auditing must never break trading
            log.exception("audit write failed")


def _default_connect_factory(api_key: str):
    from SmartApi.smartConnect import SmartConnect  # lazy: SDK import performs network I/O

    return SmartConnect(api_key=api_key)


def _default_ws_factory(auth_token: str, api_key: str, client_code: str, feed_token: str):
    from SmartApi.smartWebSocketV2 import SmartWebSocketV2

    return SmartWebSocketV2(auth_token, api_key, client_code, feed_token, max_retry_attempt=0)


def _default_order_ws_factory(auth_token: str, api_key: str, client_code: str, feed_token: str):
    from SmartApi.smartWebSocketOrderUpdate import SmartWebSocketOrderUpdate

    return SmartWebSocketOrderUpdate(auth_token, api_key, client_code, feed_token)


class AngelOneBroker(BrokerBase):
    name = "angel_one"

    def __init__(
        self,
        settings: Settings | None = None,
        secrets_getter: Callable[[str], str | None] = get_secret,
        clock: Clock | None = None,
        *,
        connect_factory: Callable[[str], Any] | None = None,
        ws_factory: Callable[[str, str, str, str], Any] | None = None,
        order_ws_factory: Callable[[str, str, str, str], Any] | None = None,
        dry_run: bool = False,
        audit_dir: str | Path | None = None,
        master: Any = None,
        order_limiter: RateLimiter | None = None,
        order_updates: bool = True,
        reconnect_base_s: float = 1.0,
        reconnect_max_s: float = 60.0,
    ) -> None:
        self.settings = settings or Settings()
        self._secrets_getter = secrets_getter
        self.clock = clock or Clock()
        self._connect_factory = connect_factory or _default_connect_factory
        self._ws_factory = ws_factory or _default_ws_factory
        self._order_ws_factory = order_ws_factory or _default_order_ws_factory
        self.dry_run = dry_run
        self.master = master
        self.order_updates = order_updates
        self.reconnect_base_s = reconnect_base_s
        self.reconnect_max_s = reconnect_max_s
        self.order_limiter = order_limiter or RateLimiter(self.settings.execution.max_orders_per_second)
        self._limiters = {
            "login": RateLimiter(1, burst=1),
            "book": RateLimiter(1, burst=1),       # orderBook / tradeBook / position: 1/s
            "rms": RateLimiter(2, burst=1),
            "ltp": RateLimiter(1, burst=1),        # docs conflict (1/s vs 10/s): be conservative
            "candle": RateLimiter(3, burst=1),
        }
        directory = Path(audit_dir) if audit_dir else Path(self.settings.app.data_dir) / "audit"
        self.audit = AuditLog(directory, self._secret_values, self.clock)

        self._sc: Any = None
        self._api_key = self._client_code = ""
        self._jwt = self._refresh_token = self._feed_token = ""
        self._login_secrets: list[str] = []
        self._connected = False
        self.logged_in = False
        self.login_at: datetime | None = None
        self.error_count = 0
        self.orders_blocked_reason: str | None = None
        self._orders: dict[str, Order] = {}
        self._tick_handlers: list[tuple[set[str], TickHandler]] = []
        self._subscribed: dict[int, set[str]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: set[asyncio.Task] = set()
        self._threads: dict[str, threading.Thread] = {}
        self._ws: dict[str, Any] = {}
        self._ws_attempts: dict[str, int] = {}
        self.ws_status: dict[str, str] = {"tick": "stopped", "order": "stopped"}
        self._stop = threading.Event()
        self.last_tick_at: float | None = None

    # ================================================================== helpers
    def _secret_values(self) -> list[str]:
        return [*self._login_secrets, self._jwt, self._jwt.removeprefix("Bearer "), self._refresh_token,
                self._feed_token]

    @property
    def connected(self) -> bool:
        return self._connected and self.logged_in

    def block_orders(self, reason: str | None) -> None:
        """Block (reason) or unblock (None) live order traffic, e.g. on static-IP mismatch."""
        self.orders_blocked_reason = reason

    def health(self) -> dict:
        age = None if self.last_tick_at is None else round(_time.monotonic() - self.last_tick_at, 3)
        return {"connected": self.connected, "logged_in": self.logged_in, "dry_run": self.dry_run,
                "ws": dict(self.ws_status), "last_tick_age_s": age, "errors": self.error_count,
                "order_rate": self.order_limiter.recent_count(), "orders_blocked": self.orders_blocked_reason}

    async def _call(self, method: str, *args: Any, limiter: str | None = None, retry_auth: bool = True) -> Any:
        if self._sc is None:
            raise BrokerError("not logged in")
        if limiter == "order":
            await self.order_limiter.acquire()
        elif limiter:
            await self._limiters[limiter].acquire()
        self.audit.write("request", method=method, args=list(args))
        try:
            resp = await asyncio.to_thread(getattr(self._sc, method), *args)
        except Exception as exc:
            self.error_count += 1
            self.audit.write("error", method=method, error=f"{exc.__class__.__name__}: {exc}")
            raise BrokerError(f"{method} failed: {exc}") from exc
        self.audit.write("response", method=method, response=resp)
        if isinstance(resp, dict) and resp.get("status") is False:
            self.error_count += 1
            if retry_auth and str(resp.get("errorcode", "")) in AUTH_ERROR_CODES:
                log.warning("session expired (%s); logging in again", resp.get("errorcode"))
                await self._login()
                return await self._call(method, *args, limiter=limiter, retry_auth=False)
        return resp

    @staticmethod
    def _ok(resp: Any) -> bool:
        return isinstance(resp, dict) and bool(resp.get("status")) and resp.get("data") is not None

    @staticmethod
    def _err(resp: Any) -> str:
        if isinstance(resp, dict):
            return f"{resp.get('message') or 'error'} ({resp.get('errorcode') or '-'})"
        return f"unexpected response {resp!r}"

    # ================================================================== session
    async def _login(self) -> None:
        names = ("ANGEL_API_KEY", "ANGEL_CLIENT_CODE", "ANGEL_PIN", "ANGEL_TOTP_SECRET")
        vals = {n: self._secrets_getter(n) for n in names}
        missing = [n for n, v in vals.items() if not v]
        if missing:
            raise BrokerError(f"missing secrets: {', '.join(missing)}")
        self._api_key = str(vals["ANGEL_API_KEY"])
        self._client_code = str(vals["ANGEL_CLIENT_CODE"])
        # value-redaction list: long secrets only (PIN / TOTP code are never passed to the audit log, and
        # redacting short numbers would mangle prices and tokens)
        self._login_secrets = [str(vals[n]) for n in ("ANGEL_API_KEY", "ANGEL_CLIENT_CODE", "ANGEL_TOTP_SECRET")]
        try:
            totp = pyotp.TOTP(str(vals["ANGEL_TOTP_SECRET"]).replace(" ", "")).now()
        except Exception as exc:
            raise BrokerError("invalid TOTP secret") from exc
        await self._limiters["login"].acquire()
        try:
            self._sc = await asyncio.to_thread(self._connect_factory, self._api_key)
            self.audit.write("request", method="generateSession", args=[self._client_code])
            resp = await asyncio.to_thread(self._sc.generateSession, self._client_code,
                                           str(vals["ANGEL_PIN"]), totp)
        except Exception as exc:
            self.error_count += 1
            self.audit.write("error", method="generateSession", error=str(exc))
            raise BrokerError(f"login failed: {exc}") from exc
        if not self._ok(resp):
            self.audit.write("response", method="generateSession", response=resp)
            self.logged_in = False
            raise BrokerError(f"login failed: {self._err(resp)}")
        data = resp["data"]
        self._jwt = str(data.get("jwtToken", ""))
        self._refresh_token = str(data.get("refreshToken", ""))
        self._feed_token = str(data.get("feedToken", ""))
        self.audit.write("response", method="generateSession", response=resp)
        self.logged_in = True
        self.login_at = self.clock.now()
        log.info("Angel One login ok")

    async def connect(self) -> None:
        self._loop = asyncio.get_running_loop()
        await self._login()
        self._connected = True
        self._stop.clear()
        if self._tick_handlers:
            self._start_ws("tick")
        if self.order_updates:
            self._start_ws("order")

    async def refresh(self) -> None:
        """Renew the JWT/feed token with the refresh token; falls back to a full login."""
        try:
            resp = await self._call("generateToken", self._refresh_token, limiter="login", retry_auth=False)
        except BrokerError:
            resp = None
        if self._ok(resp):
            jwt = str(resp["data"].get("jwtToken", ""))
            self._jwt = jwt if jwt.startswith("Bearer ") else f"Bearer {jwt}"
            self._feed_token = str(resp["data"].get("feedToken", self._feed_token))
            self._refresh_token = str(resp["data"].get("refreshToken", self._refresh_token))
            return
        await self._login()

    async def disconnect(self) -> None:
        self._stop.set()
        for ws in list(self._ws.values()):
            try:
                ws.close_connection()
            except Exception:
                log.debug("ws close failed", exc_info=True)
        for t in list(self._threads.values()):
            t.join(timeout=5)
        self._threads.clear()
        self._ws.clear()
        if self.logged_in and self._sc is not None:
            try:
                await self._call("terminateSession", self._client_code, retry_auth=False)
            except BrokerError:
                pass
        self.logged_in = False
        self._connected = False

    # ================================================================== websockets
    def _start_ws(self, kind: str) -> None:
        t = self._threads.get(kind)
        if t is not None and t.is_alive():
            return
        t = threading.Thread(target=self._ws_loop, args=(kind,), name=f"angel-{kind}-ws", daemon=True)
        self._threads[kind] = t
        t.start()

    def _set_status(self, kind: str, status: str) -> None:
        self.ws_status[kind] = status

    def _make_ws(self, kind: str) -> Any:
        args = (self._jwt, self._api_key, self._client_code, self._feed_token)
        if kind == "tick":
            ws = self._ws_factory(*args)
            ws.on_open = lambda *a: self._on_tick_open(ws)
            ws.on_data = lambda wsapp, msg, *a: self._on_tick_data(msg)
        else:
            ws = self._order_ws_factory(*args)
            ws.on_open = lambda *a: self._on_open("order")
            ws.on_message = lambda wsapp, msg, *a: self._on_order_message(msg)
            ws.retry_connect = lambda *a: None  # our loop owns reconnection
        ws.on_error = lambda *a: self._on_ws_error(kind, a)
        ws.on_close = lambda *a: self._set_status(kind, "closed")
        return ws

    def _ws_loop(self, kind: str) -> None:
        """Thread body: (re)connect with exponential backoff until ``disconnect``."""
        while not self._stop.is_set():
            self._set_status(kind, "connecting")
            try:
                ws = self._make_ws(kind)
                self._ws[kind] = ws
                ws.connect()  # blocks until the socket closes
            except Exception as exc:  # noqa: BLE001
                self.error_count += 1
                log.warning("%s websocket error: %s", kind, exc)
            self._set_status(kind, "disconnected")
            if self._stop.is_set():
                break
            n = self._ws_attempts.get(kind, 0)
            self._ws_attempts[kind] = n + 1
            delay = min(self.reconnect_base_s * (2 ** n), self.reconnect_max_s)
            log.info("%s websocket reconnect in %.1fs", kind, delay)
            self._stop.wait(delay)
        self._set_status(kind, "stopped")

    def _on_open(self, kind: str) -> None:
        self._ws_attempts[kind] = 0
        self._set_status(kind, "connected")

    def _on_ws_error(self, kind: str, args: tuple) -> None:
        self.error_count += 1
        log.warning("%s websocket error: %s", kind, args)

    def _on_tick_open(self, ws: Any) -> None:
        self._on_open("tick")
        for et, tokens in self._subscribed.items():
            if tokens:
                try:
                    ws.subscribe(CORRELATION_ID, SNAP_QUOTE, [{"exchangeType": et, "tokens": sorted(tokens)}])
                except Exception as exc:  # noqa: BLE001
                    log.warning("subscribe failed: %s", exc)

    def _on_tick_data(self, msg: Any) -> None:
        try:
            tick = parse_ws_tick(msg)
        except Exception:
            log.debug("bad tick %r", msg, exc_info=True)
            return
        self.last_tick_at = _time.monotonic()
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._dispatch_tick, tick)

    def _spawn(self, res: Any) -> None:
        if asyncio.iscoroutine(res):
            task = asyncio.ensure_future(res)
            self._tasks.add(task)
            task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("tick/order handler failed", exc_info=task.exception())

    def _dispatch_tick(self, tick: Tick) -> None:
        for tokens, handler in list(self._tick_handlers):
            if not tokens or tick.token in tokens:
                try:
                    self._spawn(handler(tick))
                except Exception:
                    log.exception("tick handler failed")

    def _on_order_message(self, message: Any) -> None:
        try:
            msg = json.loads(message) if isinstance(message, (str, bytes)) else message
            row = msg.get("orderData") if isinstance(msg, dict) else None
        except Exception:  # noqa: BLE001
            log.debug("bad order update %r", message)
            return
        self.audit.write("order_update", message=msg)
        if not row or not row.get("orderid"):
            return
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._apply_order_row, row)

    def _apply_order_row(self, row: dict) -> None:
        order, changed = self._order_from_row(row)
        if order is not None and changed:
            self._spawn(self._emit_order(dataclasses.replace(order)))

    async def subscribe_ticks(self, tokens: list[tuple[str, str]], handler: TickHandler) -> None:
        new: dict[int, list[str]] = {}
        for exch, token in tokens:
            et = EXCHANGE_TYPES.get(str(exch).upper())
            if et is None:
                raise BrokerError(f"unsupported exchange {exch}")
            if str(token) not in self._subscribed.setdefault(et, set()):
                new.setdefault(et, []).append(str(token))
        if sum(len(v) for v in self._subscribed.values()) + sum(len(v) for v in new.values()) > MAX_SUBSCRIPTIONS:
            raise BrokerError(f"more than {MAX_SUBSCRIPTIONS} subscriptions")
        for et, toks in new.items():
            self._subscribed[et].update(toks)
        self._tick_handlers.append(({str(t) for _, t in tokens}, handler))
        if not self.connected:
            return  # subscribed on connect
        ws = self._ws.get("tick")
        if ws is not None and self.ws_status.get("tick") == "connected":
            for et, toks in new.items():
                await asyncio.to_thread(ws.subscribe, CORRELATION_ID, SNAP_QUOTE,
                                        [{"exchangeType": et, "tokens": list(toks)}])
        else:
            self._start_ws("tick")

    # ================================================================== market data
    async def get_candles(self, exchange: str, token: str, interval: str, start: datetime,
                          end: datetime) -> pd.DataFrame:
        if interval not in INTERVALS:
            raise BrokerError(f"unsupported interval {interval}")
        name = INTERVALS[interval][0]
        frames = []
        for s, e in chunks_for(interval, start.astimezone(IST), end.astimezone(IST)):
            params = {"exchange": exchange, "symboltoken": str(token), "interval": name,
                      "fromdate": s.strftime("%Y-%m-%d %H:%M"), "todate": e.strftime("%Y-%m-%d %H:%M")}
            resp = await self._call("getCandleData", params, limiter="candle")
            if not self._ok(resp):
                raise BrokerError(f"getCandleData failed: {self._err(resp)}")
            frames.append(candles_to_df(resp["data"]))
        frames = [f for f in frames if not f.empty]
        if not frames:
            return empty_candles()
        out = pd.concat(frames).sort_index()
        return out[~out.index.duplicated(keep="last")]

    async def get_ltp(self, exchange: str, token: str, symbol: str = "") -> float:
        resp = await self._call("ltpData", exchange, symbol, str(token), limiter="ltp")
        if not self._ok(resp) or "ltp" not in resp["data"]:
            raise BrokerError(f"ltpData failed: {self._err(resp)}")
        return float(resp["data"]["ltp"])

    # ================================================================== orders
    @staticmethod
    def _fmt(px: float | None, tick: float) -> str:
        if not px:
            return "0"
        return f"{round(round(px / tick) * tick, 2):.2f}"

    def order_params(self, req: OrderRequest) -> dict[str, str]:
        """Broker wire params for a request. Raises if anything other than LIMIT / SL-L DAY would be sent."""
        if not isinstance(req.order_type, OrderType) or req.order_type not in _ORDER_MAP:
            raise BrokerError(f"order type {req.order_type!r} not allowed")
        variety, ordertype = _ORDER_MAP[req.order_type]
        tick = req.instrument.tick_size or 0.05
        params = {
            "variety": variety,
            "tradingsymbol": req.instrument.symbol,
            "symboltoken": req.instrument.token,
            "exchange": req.instrument.exchange,
            "transactiontype": req.side.value,
            "ordertype": ordertype,
            "producttype": req.product.value,
            "duration": "DAY",
            "price": self._fmt(req.price, tick),
            "triggerprice": self._fmt(req.trigger_price, tick) if req.order_type is OrderType.SL_LIMIT else "0",
            "quantity": str(int(req.quantity)),
            "ordertag": req.client_id[:19],
        }
        self._guard(params)
        return params

    @staticmethod
    def _guard(params: dict) -> None:
        if (params.get("variety"), params.get("ordertype")) not in _ALLOWED_WIRE or params.get("duration") != "DAY":
            raise BrokerError(f"refusing to send forbidden order params {params.get('variety')}/"
                              f"{params.get('ordertype')}/{params.get('duration')}")

    def _check_can_send(self) -> None:
        if self.orders_blocked_reason:
            raise BrokerError(f"orders blocked: {self.orders_blocked_reason}")
        if not self.dry_run and not self.connected:
            raise BrokerError("not connected")

    async def place_order(self, req: OrderRequest) -> Order:
        params = self.order_params(req)
        self._check_can_send()
        now = self.clock.now()
        if self.dry_run:
            await self.order_limiter.acquire()
            order = Order(req, broker_id=new_id("DRY"), status=(
                OrderStatus.TRIGGER_PENDING if req.order_type is OrderType.SL_LIMIT else OrderStatus.OPEN),
                message="dry run - not sent", created_at=now, updated_at=now)
            self.audit.write("dry_run", method="placeOrder", params=params, tag=req.tag)
            self._orders[order.broker_id] = order
            await self._emit_order(dataclasses.replace(order))
            return order
        try:
            resp = await self._call("placeOrderFullResponse", dict(params), limiter="order")
        except BrokerError as exc:
            # outcome unknown (e.g. timeout after send): look for our tag in the order book
            found = await self._find_by_tag(req.client_id[:19])
            if found is not None:
                return found
            order = Order(req, broker_id="", status=OrderStatus.REJECTED, created_at=now, updated_at=now,
                          message=f"send failed and order not found in order book: {exc}")
            await self._emit_order(dataclasses.replace(order))
            return order
        if self._ok(resp) and resp["data"].get("orderid"):
            order = Order(req, broker_id=str(resp["data"]["orderid"]), status=OrderStatus.PENDING,
                          message=str(resp.get("message", "")), created_at=now, updated_at=now)
            self._orders[order.broker_id] = order
        else:
            order = Order(req, broker_id="", status=OrderStatus.REJECTED, message=self._err(resp),
                          created_at=now, updated_at=now)
        await self._emit_order(dataclasses.replace(order))
        return order

    async def _find_by_tag(self, tag: str) -> Order | None:
        try:
            orders = await self.order_book()
        except BrokerError:
            return None
        return next((o for o in orders if o.request.client_id[:19] == tag), None)

    async def _refresh_order(self, order: Order, message: str) -> Order:
        try:
            await self.order_book()
        except BrokerError:
            pass
        current = self._orders.get(order.broker_id, order)
        current.message = message
        return current

    async def modify_order(self, order: Order, price: float, trigger_price: float | None = None) -> Order:
        self._check_can_send()
        stored = self._orders.get(order.broker_id, order)
        if stored.status.is_terminal:
            return stored
        trig = trigger_price if trigger_price is not None else stored.request.trigger_price
        new_req = dataclasses.replace(stored.request, price=price, trigger_price=trig)
        params = self.order_params(new_req)
        params.pop("ordertag", None)
        params["orderid"] = stored.broker_id
        if self.dry_run:
            await self.order_limiter.acquire()
            self.audit.write("dry_run", method="modifyOrder", params=params)
            stored.request = new_req
            stored.updated_at = self.clock.now()
            await self._emit_order(dataclasses.replace(stored))
            return stored
        resp = await self._call("modifyOrder", params, limiter="order")
        if not self._ok(resp):
            return await self._refresh_order(stored, f"modify failed: {self._err(resp)}")
        stored.request = new_req
        stored.updated_at = self.clock.now()
        await self._emit_order(dataclasses.replace(stored))
        return stored

    async def cancel_order(self, order: Order) -> Order:
        """Cancel. If the broker refuses (e.g. already filled) the refreshed order is returned."""
        self._check_can_send()
        stored = self._orders.get(order.broker_id, order)
        if stored.status.is_terminal:
            return stored
        variety = _ORDER_MAP[stored.request.order_type][0]
        if self.dry_run:
            await self.order_limiter.acquire()
            self.audit.write("dry_run", method="cancelOrder", orderid=stored.broker_id, variety=variety)
            stored.status = OrderStatus.CANCELLED
            stored.updated_at = self.clock.now()
            await self._emit_order(dataclasses.replace(stored))
            return stored
        resp = await self._call("cancelOrder", stored.broker_id, variety, limiter="order")
        if not self._ok(resp):
            return await self._refresh_order(stored, f"cancel failed: {self._err(resp)}")
        return await self._refresh_order(stored, "cancel requested")

    # ================================================================== books
    def _instrument_from_row(self, row: dict) -> Instrument:
        token = str(row.get("symboltoken", ""))
        if self.master is not None:
            inst = self.master.by_token(token)
            if inst is not None:
                return inst
        itype = str(row.get("instrumenttype", "")).upper()
        kind = {"OPTIDX": InstrumentKind.OPTION, "FUTIDX": InstrumentKind.FUTURE,
                "AMXIDX": InstrumentKind.INDEX}.get(itype, InstrumentKind.EQUITY)
        opt = str(row.get("optiontype", "")).upper()
        strike = _num(row.get("strikeprice"), -1)
        return Instrument(
            token=token,
            symbol=str(row.get("tradingsymbol", "")),
            name=str(row.get("symbolname") or row.get("name") or ""),
            exchange=str(row.get("exchange", "")),
            kind=kind,
            lot_size=int(_num(row.get("lotsize"), 1)) or 1,
            expiry=parse_expiry(row.get("expirydate")),
            strike=strike if strike > 0 else None,
            option_type=OptionType(opt) if opt in ("CE", "PE") else None,
        )

    def _order_from_row(self, row: dict) -> tuple[Order | None, bool]:
        """Create/update the tracked order from an order-book / order-update row -> (order, changed)."""
        oid = str(row.get("orderid", ""))
        qty = int(_num(row.get("quantity")))
        filled = int(_num(row.get("filledshares")))
        avg = _num(row.get("averageprice"))
        status = map_status(row.get("status") or row.get("orderstatus") or "", filled, qty)
        msg = str(row.get("text") or "")
        existing = self._orders.get(oid)
        if existing is not None:
            changed = (existing.status, existing.filled_qty) != (status, filled)
            existing.status, existing.filled_qty = status, filled
            existing.avg_price = avg or existing.avg_price
            existing.message = msg or existing.message
            if changed:
                existing.updated_at = self.clock.now()
            return existing, changed
        otype = {"LIMIT": OrderType.LIMIT, "STOPLOSS_LIMIT": OrderType.SL_LIMIT}.get(
            str(row.get("ordertype", "")).upper())
        if otype is None or not oid:
            return None, False  # e.g. a manual MARKET order placed outside IndexEdge
        try:
            price = _num(row.get("price")) or avg or 0.05
            trig = _num(row.get("triggerprice")) or None
            req = OrderRequest(
                instrument=self._instrument_from_row(row),
                side=Side(str(row.get("transactiontype", "BUY")).upper()),
                quantity=max(qty, 1),
                order_type=otype,
                price=price,
                trigger_price=trig if otype is OrderType.SL_LIMIT else None,
                product=ProductType(str(row.get("producttype") or "INTRADAY").upper()),
                tag=str(row.get("ordertag") or ""),
                client_id=str(row.get("ordertag") or new_id("ext_")),
            )
        except (ValueError, KeyError):
            log.debug("cannot map order row %s", oid, exc_info=True)
            return None, False
        now = self.clock.now()
        order = Order(req, oid, status, filled, avg, msg, created_at=now, updated_at=now)
        self._orders[oid] = order
        return order, True

    async def order_book(self) -> list[Order]:
        resp = await self._call("orderBook", limiter="book")
        if not isinstance(resp, dict) or resp.get("status") is False:
            raise BrokerError(f"orderBook failed: {self._err(resp)}")
        out = []
        for row in resp.get("data") or []:
            order, changed = self._order_from_row(row)
            if order is None:
                continue
            out.append(order)
            if changed:
                await self._emit_order(dataclasses.replace(order))
        if self.dry_run:
            out.extend(o for o in self._orders.values() if o.broker_id.startswith("DRY"))
        return out

    async def trade_book(self) -> list[Fill]:
        resp = await self._call("tradeBook", limiter="book")
        if not isinstance(resp, dict) or resp.get("status") is False:
            raise BrokerError(f"tradeBook failed: {self._err(resp)}")
        today = self.clock.today()
        fills = []
        for row in resp.get("data") or []:
            ts = self.clock.now()
            try:
                t = datetime.strptime(str(row.get("filltime", "")).strip(), "%H:%M:%S").time()
                ts = datetime.combine(today, t, IST)
            except ValueError:
                pass
            fills.append(Fill(str(row.get("orderid", "")), self._instrument_from_row(row),
                              Side(str(row.get("transactiontype", "BUY")).upper()),
                              int(_num(row.get("fillsize"))), _num(row.get("fillprice")), ts))
        return fills

    async def positions(self) -> list[Position]:
        resp = await self._call("position", limiter="book")
        if not isinstance(resp, dict) or resp.get("status") is False:
            raise BrokerError(f"position failed: {self._err(resp)}")
        out = []
        for row in resp.get("data") or []:
            out.append(Position(
                instrument=self._instrument_from_row(row),
                net_qty=int(_num(row.get("netqty"))),
                avg_price=_num(row.get("avgnetprice") or row.get("netprice")),
                ltp=_num(row.get("ltp")),
                realised_pnl=_num(row.get("realised")),
            ))
        return out

    async def funds(self) -> Funds:
        resp = await self._call("rmsLimit", limiter="rms")
        if not self._ok(resp):
            raise BrokerError(f"rmsLimit failed: {self._err(resp)}")
        d = resp["data"]
        return Funds(available_cash=_num(d.get("availablecash")), used_margin=_num(d.get("utiliseddebits")),
                     net=_num(d.get("net")))
