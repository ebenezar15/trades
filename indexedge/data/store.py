"""Parquet candle store: ``<root>/<symbol>/<tf>/<yyyy>.parquet`` plus session-aware resampling."""
from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from indexedge.core.clock import IST
from indexedge.core.types import CANDLE_COLUMNS

log = logging.getLogger(__name__)

TF_MINUTES: dict[str, int | None] = {
    "1m": 1, "3m": 3, "5m": 5, "10m": 10, "15m": 15, "30m": 30, "1h": 60, "1d": None,
}
_AGG = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum", "oi": "last"}
_SESSION_OFFSET = pd.Timedelta(hours=9, minutes=15)


def empty_candles() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype=float) for c in CANDLE_COLUMNS},
                        index=pd.DatetimeIndex([], tz=IST))


def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("candle DataFrame needs a DatetimeIndex")
    out = df.copy()
    out.index = out.index.tz_localize(IST) if out.index.tz is None else out.index.tz_convert(IST)
    keep = [c for c in out.columns if c in _AGG]
    return out[keep].astype(float)


def resample(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Session-aware resample: intraday buckets anchored at 09:15 IST each day; '1d' -> midnight IST index."""
    if tf not in TF_MINUTES:
        raise ValueError(f"unknown timeframe {tf!r}; expected one of {list(TF_MINUTES)}")
    if df.empty:
        return df.copy()
    df = _normalise(df)
    agg = {c: _AGG[c] for c in df.columns}
    day = df.index.normalize()
    mins = TF_MINUTES[tf]
    if mins is None:
        key = day
    else:
        offset = (df.index - day - _SESSION_OFFSET) // pd.Timedelta(minutes=mins)
        key = day + _SESSION_OFFSET + pd.to_timedelta(offset * mins, unit="min")
    out = df.groupby(key).agg(agg).dropna(subset=["close"])
    out.index.name = None
    return out


def _to_ts(x: date | datetime | str | pd.Timestamp | None, end: bool = False) -> pd.Timestamp | None:
    if x is None:
        return None
    if isinstance(x, date) and not isinstance(x, datetime):
        d = pd.Timestamp(x + timedelta(days=1) if end else x).tz_localize(IST)
        return d - pd.Timedelta(microseconds=1) if end else d
    ts = pd.Timestamp(x)
    return ts.tz_localize(IST) if ts.tz is None else ts.tz_convert(IST)


class CandleStore:
    """Append-only, de-duplicating candle store partitioned by symbol / timeframe / year."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    @staticmethod
    def _safe(name: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]", "_", name)

    def _dir(self, symbol: str, tf: str) -> Path:
        return self.root / self._safe(symbol) / tf

    @staticmethod
    def resample(df: pd.DataFrame, tf: str) -> pd.DataFrame:
        return resample(df, tf)

    def save(self, symbol: str, tf: str, df: pd.DataFrame) -> int:
        """Merge ``df`` into the store (later rows win on duplicate timestamps). Returns rows written."""
        if tf not in TF_MINUTES:
            raise ValueError(f"unknown timeframe {tf!r}")
        if df is None or df.empty:
            return 0
        new = _normalise(df)
        d = self._dir(symbol, tf)
        d.mkdir(parents=True, exist_ok=True)
        for year, part in new.groupby(new.index.year):
            path = d / f"{year}.parquet"
            if path.exists():
                part = pd.concat([pd.read_parquet(path), part])
            part = part[~part.index.duplicated(keep="last")].sort_index()
            tmp = path.with_suffix(".tmp")
            part.to_parquet(tmp)
            os.replace(tmp, path)
        return len(new)

    def load(self, symbol: str, tf: str, start=None, end=None) -> pd.DataFrame:
        d = self._dir(symbol, tf)
        if not d.exists():
            return empty_candles()
        s, e = _to_ts(start), _to_ts(end, end=True)
        frames = []
        for path in sorted(d.glob("*.parquet")):
            try:
                year = int(path.stem)
            except ValueError:
                continue
            if (s is not None and year < s.year) or (e is not None and year > e.year):
                continue
            frames.append(pd.read_parquet(path))
        if not frames:
            return empty_candles()
        df = pd.concat(frames).sort_index()
        df.index = df.index.tz_convert(IST)
        if s is not None:
            df = df[df.index >= s]
        if e is not None:
            df = df[df.index <= e]
        return df

    def last_ts(self, symbol: str, tf: str) -> pd.Timestamp | None:
        d = self._dir(symbol, tf)
        files = sorted(d.glob("*.parquet")) if d.exists() else []
        if not files:
            return None
        df = pd.read_parquet(files[-1])
        return df.index.max().tz_convert(IST) if not df.empty else None

    def available(self) -> list[tuple[str, str, pd.Timestamp, pd.Timestamp, int]]:
        out = []
        if not self.root.exists():
            return out
        for sym_dir in sorted(p for p in self.root.iterdir() if p.is_dir()):
            for tf_dir in sorted(p for p in sym_dir.iterdir() if p.is_dir()):
                df = self.load(sym_dir.name, tf_dir.name)
                if not df.empty:
                    out.append((sym_dir.name, tf_dir.name, df.index[0], df.index[-1], len(df)))
        return out
