"""Quote providers for the external context engine.

Every provider implements :class:`QuoteProvider`: ``async fetch(symbols) -> dict[symbol, Quote]``.
Providers return only the symbols they could fetch; they never raise for a missing/failed symbol, so the
engine can merge several providers and flag whatever is still missing.

* :class:`YFinanceProvider` — Yahoo Finance via the ``yfinance`` package, run in a thread executor with a
  timeout (yfinance is blocking).
* :class:`GiftNiftyProvider` — best-effort scrape of a public quote page / JSON endpoint; returns ``{}`` on
  any failure.
* :class:`StaticProvider` — fixed quotes (tests, manual override).
* :class:`SyntheticProvider` — plausible moving quotes for demo mode (offline).
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Protocol, runtime_checkable

import numpy as np

from indexedge.core.clock import IST

log = logging.getLogger(__name__)

GIFT_NIFTY_SYMBOL = "GIFTNIFTY"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) IndexEdge/1.0 (personal use)"


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: float
    prev_close: float | None
    change_pct: float          # % change vs prev_close (0.0 when unknown)
    ts: datetime               # tz-aware time of the last price (IST)

    @staticmethod
    def make(symbol: str, price: float, prev_close: float | None, ts: datetime) -> Quote:
        chg = 0.0
        if prev_close and prev_close > 0 and math.isfinite(price):
            chg = (price / prev_close - 1.0) * 100.0
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=IST)
        return Quote(symbol, float(price), None if prev_close is None else float(prev_close), chg,
                     ts.astimezone(IST))


@runtime_checkable
class QuoteProvider(Protocol):
    name: str

    async def fetch(self, symbols: list[str]) -> dict[str, Quote]: ...


def _now() -> datetime:
    return datetime.now(IST)


# --------------------------------------------------------------------------------------------- static
class StaticProvider:
    """Returns the given quotes (for tests / demo / manual override). Unknown symbols are omitted."""

    name = "static"

    def __init__(self, quotes: dict[str, Quote]) -> None:
        self.quotes = dict(quotes)

    def set(self, quote: Quote) -> None:
        self.quotes[quote.symbol] = quote

    async def fetch(self, symbols: list[str]) -> dict[str, Quote]:
        return {s: self.quotes[s] for s in symbols if s in self.quotes}


# ------------------------------------------------------------------------------------------ synthetic
# Plausible base level and typical daily % move (1 sigma) per symbol, for demo mode.
_SYNTH_BASE: dict[str, tuple[float, float]] = {
    GIFT_NIFTY_SYMBOL: (25200.0, 0.8),
    "^NSEI": (25100.0, 0.8),
    "^NSEBANK": (56500.0, 1.0),
    "ES=F": (6650.0, 1.0),
    "NQ=F": (24500.0, 1.3),
    "^N225": (45000.0, 1.3),
    "^HSI": (26000.0, 1.5),
    "INR=X": (88.5, 0.3),
    "DX-Y.NYB": (99.0, 0.4),
    "^TNX": (4.10, 2.0),
    "BZ=F": (66.0, 2.0),
    "CL=F": (62.0, 2.2),
    "^INDIAVIX": (12.5, 5.0),
}


class SyntheticProvider:
    """Deterministic (seeded) random-walk quotes for every requested symbol. Never touches the network.

    Each symbol keeps a previous close and drifts a little on every ``fetch`` so the UI looks alive.
    Unknown symbols start at 100 with a 1% daily sigma.
    """

    name = "synthetic"

    def __init__(self, seed: int = 0, now_fn: Callable[[], datetime] | None = None) -> None:
        self._rng = np.random.default_rng(seed)
        self._now = now_fn or _now
        self._state: dict[str, tuple[float, float]] = {}   # symbol -> (prev_close, price)

    def _init_symbol(self, sym: str) -> tuple[float, float]:
        base, sigma = _SYNTH_BASE.get(sym, (100.0, 1.0))
        prev = base * (1 + self._rng.normal(0, 0.01))
        price = prev * (1 + self._rng.normal(0, sigma * 0.5) / 100)
        return prev, price

    async def fetch(self, symbols: list[str]) -> dict[str, Quote]:
        now = self._now()
        out: dict[str, Quote] = {}
        for sym in symbols:
            prev, price = self._state.get(sym) or self._init_symbol(sym)
            _, sigma = _SYNTH_BASE.get(sym, (100.0, 1.0))
            # small step with mean reversion so change stays within a plausible band
            chg = (price / prev - 1) * 100
            step = self._rng.normal(-0.05 * chg, sigma * 0.05)
            price = price * (1 + step / 100)
            self._state[sym] = (prev, price)
            out[sym] = Quote.make(sym, round(price, 4), round(prev, 4), now)
        return out


# ------------------------------------------------------------------------------------------- yfinance
def _yf_download(symbols: list[str], period: str, interval: str) -> Any:
    import yfinance as yf  # imported lazily: heavy and only needed in live mode

    return yf.download(symbols, period=period, interval=interval, group_by="ticker", threads=False,
                       progress=False, auto_adjust=False, prepost=False)


def _quotes_from_frame(df: Any, symbols: list[str], interval_min: int, now: datetime) -> dict[str, Quote]:
    """Build quotes from a yfinance multi-ticker frame (columns (ticker, field))."""
    out: dict[str, Quote] = {}
    if df is None or getattr(df, "empty", True):
        return out
    for sym in symbols:
        try:
            if getattr(df.columns, "nlevels", 1) > 1:
                if sym not in df.columns.get_level_values(0):
                    continue
                close = df[sym]["Close"]
            else:
                close = df["Close"]
            close = close.dropna()
            if close.empty:
                continue
            idx = close.index
            if idx.tz is None:
                idx = idx.tz_localize("UTC")
            close.index = idx
            dates = np.array([t.date() for t in idx])
            last_date = dates[-1]
            prev = close[dates < last_date]
            prev_close = float(prev.iloc[-1]) if not prev.empty else None
            ts = (idx[-1] + timedelta(minutes=interval_min)).to_pydatetime().astimezone(IST)
            ts = min(ts, now)
            out[sym] = Quote.make(sym, float(close.iloc[-1]), prev_close, ts)
        except Exception:  # one bad ticker must not break the others
            log.debug("yfinance parse failed for %s", sym, exc_info=True)
    return out


class YFinanceProvider:
    """Yahoo Finance quotes via ``yfinance`` (one batched download per fetch, in a thread).

    ``price`` is the last 15-minute bar close, ``prev_close`` the last close of the previous exchange-local
    date and ``ts`` the end of the last bar, so staleness is measured honestly (a closed market looks old).
    """

    name = "yfinance"

    def __init__(self, timeout_s: float = 20.0, interval: str = "15m", period: str = "5d",
                 downloader: Callable[[list[str], str, str], Any] | None = None,
                 now_fn: Callable[[], datetime] | None = None,
                 skip: set[str] | None = None) -> None:
        self.timeout_s = timeout_s
        self.interval = interval
        self.period = period
        self._download = downloader or _yf_download
        self._now = now_fn or _now
        self.skip = set(skip) if skip is not None else {GIFT_NIFTY_SYMBOL}

    async def fetch(self, symbols: list[str]) -> dict[str, Quote]:
        syms = [s for s in dict.fromkeys(symbols) if s not in self.skip]
        if not syms:
            return {}
        loop = asyncio.get_running_loop()
        try:
            df = await asyncio.wait_for(
                loop.run_in_executor(None, self._download, syms, self.period, self.interval), self.timeout_s)
        except TimeoutError:
            log.warning("yfinance fetch timed out after %.0fs", self.timeout_s)
            return {}
        except Exception as exc:  # noqa: BLE001 - best-effort, must never raise
            log.warning("yfinance fetch failed: %s", exc)
            return {}
        interval_min = int(re.sub(r"\D", "", self.interval) or 15)
        if self.interval.endswith("d"):
            interval_min = 0
        return _quotes_from_frame(df, syms, interval_min, self._now())


# ------------------------------------------------------------------------------------------ GIFT Nifty
_PRICE_KEYS = ("lastPrice", "last_price", "ltp", "lastTradedPrice", "last", "price", "regularMarketPrice")
_PREV_KEYS = ("previousClose", "prevClose", "prev_close", "previous_close", "close", "regularMarketPreviousClose")


def _num(v: Any) -> float | None:
    try:
        x = float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) and x > 0 else None


def _walk_json(obj: Any) -> tuple[float | None, float | None]:
    """Depth-first search for the first dict that holds a price key (and optionally prev close)."""
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            price = next((_num(cur[k]) for k in _PRICE_KEYS if k in cur and _num(cur[k])), None)
            if price:
                prev = next((_num(cur[k]) for k in _PREV_KEYS if k in cur and _num(cur[k])), None)
                return price, prev
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return None, None


def parse_gift_nifty(text: str) -> tuple[float | None, float | None]:
    """Extract (price, prev_close) from a JSON body or an HTML page. Sanity-checks Nifty-like levels."""
    price = prev = None
    try:
        price, prev = _walk_json(json.loads(text))
    except (ValueError, TypeError):
        for key in _PRICE_KEYS:
            m = re.search(rf'"{key}"\s*:\s*"?([\d,]+\.?\d*)', text)
            if m:
                price = _num(m.group(1))
                break
        for key in _PREV_KEYS:
            m = re.search(rf'"{key}"\s*:\s*"?([\d,]+\.?\d*)', text)
            if m:
                prev = _num(m.group(1))
                break
    ok = lambda x: x is not None and 5_000 < x < 100_000
    return (price if ok(price) else None), (prev if ok(prev) else None)


class GiftNiftyProvider:
    """Best-effort GIFT Nifty quote from a public page / JSON endpoint.

    There is no official free API; ``url`` should point at a page that embeds ``lastPrice``/``ltp`` style JSON.
    Any failure (network, parse, sanity bound) returns ``{}`` — the engine then marks GIFT Nifty missing.
    """

    name = "gift_nifty"

    def __init__(self, url: str | None = None, client: Any = None, timeout_s: float = 10.0,
                 now_fn: Callable[[], datetime] | None = None) -> None:
        self.url = url
        self._client = client
        self.timeout_s = timeout_s
        self._now = now_fn or _now

    async def fetch(self, symbols: list[str]) -> dict[str, Quote]:
        if GIFT_NIFTY_SYMBOL not in symbols or not self.url:
            return {}
        try:
            import httpx

            headers = {"User-Agent": USER_AGENT, "Accept": "application/json,text/html;q=0.9"}
            if self._client is not None:
                resp = await self._client.get(self.url, headers=headers, timeout=self.timeout_s)
            else:
                async with httpx.AsyncClient(timeout=self.timeout_s, follow_redirects=True) as client:
                    resp = await client.get(self.url, headers=headers)
            if resp.status_code != 200:
                return {}
            price, prev = parse_gift_nifty(resp.text)
        except Exception as exc:  # noqa: BLE001 - best-effort, must never raise
            log.info("GIFT Nifty fetch failed: %s", type(exc).__name__)
            return {}
        if price is None:
            return {}
        return {GIFT_NIFTY_SYMBOL: Quote.make(GIFT_NIFTY_SYMBOL, price, prev, self._now())}


def with_ts(q: Quote, ts: datetime) -> Quote:
    """Copy of ``q`` with a new timestamp (helper for tests and replay)."""
    return replace(q, ts=ts)
