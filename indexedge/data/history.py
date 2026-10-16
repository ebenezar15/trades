"""Historical candle backfill, chunked per SmartAPI per-request limits, plus an offline-safe yfinance helper.

SmartAPI ``getCandleData`` limits (max days per request, docs/SMARTAPI_NOTES.md): ONE_MINUTE 30,
THREE_MINUTE 60, FIVE_MINUTE 100, TEN_MINUTE 100, FIFTEEN_MINUTE 200, THIRTY_MINUTE 200, ONE_HOUR 400,
ONE_DAY 2000. Chunks are one day shorter than the limit to stay clear of inclusive-boundary rejections.
Historical requests are throttled to < 3/s.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import pandas as pd

from indexedge.broker.rate_limiter import RateLimiter
from indexedge.core.clock import IST, Clock
from indexedge.core.types import CANDLE_COLUMNS, Instrument, InstrumentKind
from indexedge.data.instruments import DEFAULT_INDEX_TOKENS
from indexedge.data.store import empty_candles

if TYPE_CHECKING:
    from indexedge.broker.base import BrokerBase
    from indexedge.data.instruments import InstrumentMaster
    from indexedge.data.store import CandleStore

log = logging.getLogger(__name__)

# timeframe -> (SmartAPI interval name, max days per request)
INTERVALS: dict[str, tuple[str, int]] = {
    "1m": ("ONE_MINUTE", 30),
    "3m": ("THREE_MINUTE", 60),
    "5m": ("FIVE_MINUTE", 100),
    "10m": ("TEN_MINUTE", 100),
    "15m": ("FIFTEEN_MINUTE", 200),
    "30m": ("THIRTY_MINUTE", 200),
    "1h": ("ONE_HOUR", 400),
    "1d": ("ONE_DAY", 2000),
}


def chunk_ranges(start: datetime, end: datetime, max_days: int) -> list[tuple[datetime, datetime]]:
    """Split [start, end] into consecutive ranges each spanning < ``max_days`` days."""
    if end <= start:
        return []
    span = timedelta(days=max(max_days - 1, 1))
    out = []
    cur = start
    while cur < end:
        nxt = min(cur + span, end)
        out.append((cur, nxt))
        cur = nxt
    return out


def chunks_for(tf: str, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    if tf not in INTERVALS:
        raise ValueError(f"unsupported timeframe {tf!r}; expected one of {list(INTERVALS)}")
    return chunk_ranges(start, end, INTERVALS[tf][1])


class HistoryService:
    def __init__(self, broker: BrokerBase, store: CandleStore, master: InstrumentMaster | None,
                 clock: Clock | None = None, limiter: RateLimiter | None = None) -> None:
        self.broker = broker
        self.store = store
        self.master = master
        self.clock = clock or Clock()
        self.limiter = limiter or RateLimiter(2.5, burst=1)
        self.errors = 0

    def _index(self, underlying: str) -> Instrument:
        if self.master is not None:
            return self.master.index(underlying)
        key = underlying.upper().replace(" ", "")
        if key not in DEFAULT_INDEX_TOKENS:
            raise KeyError(f"unknown underlying {underlying!r}")
        return Instrument(DEFAULT_INDEX_TOKENS[key], key, key, "BSE" if key == "SENSEX" else "NSE",
                          InstrumentKind.INDEX)

    async def fetch(self, inst: Instrument, tf: str, start: datetime, end: datetime) -> pd.DataFrame:
        """Download candles for ``inst`` in chunks; failed chunks are logged and skipped."""
        frames = []
        for s, e in chunks_for(tf, start, end):
            await self.limiter.acquire()
            try:
                df = await self.broker.get_candles(inst.exchange, inst.token, tf, s, e)
            except Exception as exc:  # noqa: BLE001
                self.errors += 1
                log.warning("candle chunk %s %s %s..%s failed: %s", inst.symbol, tf, s, e, exc)
                continue
            if df is not None and not df.empty:
                frames.append(df)
        if not frames:
            return empty_candles()
        out = pd.concat(frames).sort_index()
        return out[~out.index.duplicated(keep="last")]

    async def backfill(self, underlying: str, tf: str, days: int, end: datetime | None = None,
                       incremental: bool = True) -> int:
        """Fetch the last ``days`` calendar days of ``tf`` candles for an index into the store.

        With ``incremental`` only the range after the last stored bar is requested. Returns rows fetched.
        """
        inst = self._index(underlying)
        end = end or self.clock.now()
        start = end - timedelta(days=days)
        if incremental:
            last = self.store.last_ts(underlying, tf)
            if last is not None and last.to_pydatetime() > start:
                start = last.to_pydatetime()
        df = await self.fetch(inst, tf, start, end)
        if df.empty:
            return 0
        self.store.save(underlying, tf, df)
        return len(df)


def yfinance_download(ticker: str, interval: str = "5m", period: str = "60d") -> pd.DataFrame:
    """Download candles via yfinance; returns an empty candle frame on any error (offline-safe)."""
    try:
        import yfinance as yf

        raw = yf.download(ticker, interval=interval, period=period, progress=False, auto_adjust=False,
                          threads=False)
        if raw is None or raw.empty:
            return empty_candles()
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        raw.columns = [str(c).lower() for c in raw.columns]
        df = raw[[c for c in CANDLE_COLUMNS if c in raw.columns]].astype(float)
        idx = pd.DatetimeIndex(df.index)
        if idx.tz is None:
            idx = idx.tz_localize("UTC" if interval[-1] in "mh" else IST)
        df.index = idx.tz_convert(IST)
        return df.dropna(subset=["close"])
    except Exception as exc:  # noqa: BLE001
        log.warning("yfinance download %s failed: %s", ticker, exc)
        return empty_candles()
