"""Real Nifty / Bank Nifty prices without a broker, from Yahoo Finance (may be delayed ~1–2 minutes).

Used when Angel One is not connected. History: 5-minute candles for the last ~60 days. Live: every
``poll_s`` seconds the day's 1-minute candles are downloaded and re-aggregated into 5-minute bars, so the
bars match the exchange session exactly (no tick sampling error). Index volume is not available from Yahoo.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

import pandas as pd

from indexedge.core.clock import IST, Clock
from indexedge.data.history import yfinance_download
from indexedge.data.store import empty_candles, resample

log = logging.getLogger(__name__)

TICKERS = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK", "INDIAVIX": "^INDIAVIX"}
Downloader = Callable[[str, str, str], pd.DataFrame]
BarHandler = Callable[[str, pd.Series], Awaitable[None]]
QuoteHandler = Callable[[str, float, datetime], Awaitable[None]]


def session_only(df: pd.DataFrame) -> pd.DataFrame:
    """Keep 09:15–15:29 bars on weekdays."""
    if df.empty:
        return df
    t = df.index.time
    keep = (df.index.weekday < 5) & (t >= pd.Timestamp("09:15").time()) & (t < pd.Timestamp("15:30").time())
    return df[keep]


class YahooFeed:
    def __init__(self, clock: Clock, downloader: Downloader = yfinance_download, poll_s: float = 20.0) -> None:
        self.clock = clock
        self.download = downloader
        self.poll_s = poll_s
        self.last_ok: datetime | None = None
        self.last_error: str = ""
        self._published: dict[str, pd.Timestamp] = {}
        self.forming: dict[str, pd.Series] = {}        # current (unfinished) 5-minute bar per underlying
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------ history
    async def history(self, underlying: str, period: str = "60d") -> pd.DataFrame:
        ticker = TICKERS[underlying]
        df = await asyncio.to_thread(self.download, ticker, "5m", period)
        df = session_only(df)
        if df.empty:
            return empty_candles()
        df = df.copy()
        df["volume"] = df.get("volume", 0.0).fillna(0.0) if "volume" in df else 0.0
        return df

    # ------------------------------------------------------------------ live
    async def poll_once(self, on_bar: BarHandler, on_quote: QuoteHandler) -> int:
        """One poll: publish newly closed 5-minute bars and the latest price. Returns bars published."""
        now = self.clock.now()
        published = 0
        for u in ("NIFTY", "BANKNIFTY", "INDIAVIX"):
            one = await asyncio.to_thread(self.download, TICKERS[u], "1m", "1d")
            one = session_only(one)
            one = one[one.index.date == now.date()] if not one.empty else one
            if one.empty:
                continue
            self.last_ok = now
            last_px = float(one["close"].iloc[-1])
            await on_quote(u, last_px, one.index[-1].to_pydatetime())
            if u == "INDIAVIX":
                continue
            bars = resample(one, "5m")
            if bars.empty:
                continue
            # a 5-minute bar is closed once ``now`` has passed its end (plus 1 minute for Yahoo's lag)
            closed = bars[bars.index + pd.Timedelta(minutes=6) <= pd.Timestamp(now)]
            self.forming[u] = bars.iloc[-1]
            last_pub = self._published.get(u)
            for ts, row in closed.iterrows():
                if last_pub is not None and ts <= last_pub:
                    continue
                row = row.copy()
                row["volume"] = 0.0 if pd.isna(row.get("volume")) else float(row.get("volume", 0.0))
                await on_bar(u, row)
                self._published[u] = ts
                published += 1
        return published

    async def run(self, on_bar: BarHandler, on_quote: QuoteHandler) -> None:
        while True:
            try:
                if self.clock.is_market_open() or (self.clock.minutes_to_close() > -10
                                                   and self.clock.is_trading_day()):
                    await self.poll_once(on_bar, on_quote)
                    self.last_error = ""
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - keep polling
                self.last_error = str(exc)
                log.warning("Yahoo poll failed: %s", exc)
            await asyncio.sleep(self.poll_s)

    def mark_published(self, underlying: str, ts: pd.Timestamp) -> None:
        self._published[underlying] = ts


__all__ = ["YahooFeed", "TICKERS", "session_only", "IST"]
