"""Strategy plug-in base class and shared helpers.

A strategy turns the output of :func:`indexedge.features.engine.build_features` (a 5-minute frame with every
indicator column) into raw :class:`~indexedge.core.types.StrategySignal` objects.

Contract
--------
* ``scan`` is **causal / truncation-safe**: the signals with ``ts <= T`` are identical whether the strategy
  scans ``build_features(df[:k])`` or ``build_features(df)``. Every decision at bar *i* uses only bars
  ``<= i`` (higher-timeframe bars are mapped with :func:`indexedge.features.indicators.align_htf`, which only
  exposes *completed* bars).
* A signal is stamped with the **open time of the bar whose close confirmed it**; ``entry`` is that bar's
  close. :func:`indexedge.backtest.simulate.simulate_exit` then trades it from the next bar.
* Bad input (empty frame, missing columns, NaN warm-up) never raises: ``scan`` returns ``[]`` and sets
  ``last_skip_reason`` to a plain-English explanation.
* Time windows (``entry_start``/``entry_end``) are checked against the **bar close time**.
"""
from __future__ import annotations

import math
from abc import ABC, abstractmethod
from datetime import time
from typing import Any, ClassVar

import numpy as np
import pandas as pd

from indexedge.backtest.simulate import TradeOutcome, simulate_exit
from indexedge.core.types import Regime, Side, StrategySignal
from indexedge.features.engine import FEATURE_COLUMNS
from indexedge.features.indicators import infer_interval_minutes, resample_ohlc, session_dates

OHLC = ["open", "high", "low", "close", "volume"]


def fmt(x: float, nd: int = 1) -> str:
    """Indian-style readable number for reasons, e.g. 25,140.5."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    return f"{x:,.{nd}f}"


def hhmm_to_min(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


class BarView:
    """Numpy views of a feature frame for fast per-bar loops."""

    def __init__(self, feat: pd.DataFrame) -> None:
        self.feat = feat
        self.index = feat.index
        self.n = len(feat)
        self.step = infer_interval_minutes(feat.index) if self.n else 5
        idx = feat.index
        self.minute = np.asarray(idx.hour * 60 + idx.minute, dtype=int) if self.n else np.array([], int)
        self.close_min = self.minute + self.step
        self.dates = session_dates(idx) if self.n else np.array([], dtype=object)
        self._cache: dict[str, np.ndarray] = {}

    def __getitem__(self, col: str) -> np.ndarray:
        if col not in self._cache:
            if col in self.feat:
                self._cache[col] = self.feat[col].to_numpy(dtype=float)
            else:
                self._cache[col] = np.full(self.n, np.nan)
        return self._cache[col]

    def day_slices(self) -> list[tuple[Any, int, int]]:
        """``[(date, start, stop)]`` for each session in order."""
        if not self.n:
            return []
        d = self.dates
        cuts = np.flatnonzero(np.r_[True, d[1:] != d[:-1]])
        ends = np.r_[cuts[1:], self.n]
        return [(d[s], int(s), int(e)) for s, e in zip(cuts, ends)]


class Strategy(ABC):
    """Base class for all strategy plug-ins."""

    id: ClassVar[str] = ""
    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    timeframe: ClassVar[str] = "5m"
    regimes: ClassVar[set[Regime] | None] = None
    instrument_hint: ClassVar[str] = "future"
    backtest_quality: ClassVar[str] = "full"          # "full" | "approximate"
    time_exit: ClassVar[str | None] = "15:15"
    default_params: ClassVar[dict[str, Any]] = {}
    param_grid: ClassVar[dict[str, list[Any]] | None] = None
    learn: ClassVar[str] = ""
    required_columns: ClassVar[tuple[str, ...]] = ("open", "high", "low", "close", "atr14")

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        unknown = set(params or {}) - set(self.default_params)
        if unknown:
            raise ValueError(f"{self.id}: unknown params {sorted(unknown)}")
        self.params: dict[str, Any] = {**self.default_params, **(params or {})}
        self.last_skip_reason: str = ""

    # ------------------------------------------------------------------ public API
    def scan(self, feat: pd.DataFrame | None, underlying: str,
             extra: dict[str, Any] | None = None) -> list[StrategySignal]:
        """All signals in ``feat`` (one per setup), sorted by time. Never raises on bad data."""
        self.last_skip_reason = ""
        if feat is None or len(feat) == 0:
            self.last_skip_reason = "no candle data"
            return []
        missing = [c for c in self.required_columns if c not in feat.columns]
        if missing:
            self.last_skip_reason = f"missing feature columns: {', '.join(missing)}"
            return []
        sigs = self._scan(feat, underlying.upper(), extra)
        sigs = [s for s in sigs if self._valid(s)]
        sigs.sort(key=lambda s: s.ts)
        if not sigs and not self.last_skip_reason:
            self.last_skip_reason = "setup conditions not met"
        return sigs

    def latest(self, feat: pd.DataFrame | None, underlying: str,
               extra: dict[str, Any] | None = None) -> StrategySignal | None:
        """The signal confirmed on the last bar of ``feat`` (``None`` if none)."""
        if feat is None or len(feat) == 0:
            return None
        last = feat.index[-1]
        for s in reversed(self.scan(feat, underlying, extra)):
            if s.ts == last:
                return s
        return None

    def simulate(self, signal: StrategySignal, bars: pd.DataFrame, max_bars: int = 75) -> TradeOutcome | None:
        """Exit simulation for one signal (``bars`` = candles strictly after the signal bar)."""
        return simulate_exit(signal, bars, max_bars=max_bars)

    def info(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "description": self.description, "timeframe": self.timeframe,
            "regimes": sorted(r.value for r in self.regimes) if self.regimes else None,
            "instrument_hint": self.instrument_hint, "backtest_quality": self.backtest_quality,
            "time_exit": self.time_exit, "params": dict(self.params),
            "param_grid": self.param_grid, "learn": self.learn,
        }

    # ------------------------------------------------------------------ for subclasses
    @abstractmethod
    def _scan(self, feat: pd.DataFrame, underlying: str, extra: dict[str, Any] | None) -> list[StrategySignal]:
        ...

    def _param(self, key: str, underlying: str) -> Any:
        """Param that may be a per-underlying dict, e.g. ``{"NIFTY": 0.8, "BANKNIFTY": 1.0}``."""
        v = self.params[key]
        if isinstance(v, dict):
            return v.get(underlying, v.get("NIFTY", next(iter(v.values()))))
        return v

    def _signal(self, bv: BarView, i: int, underlying: str, side: Side, entry: float, stop: float,
                targets: list[float], reasons: list[str], time_exit: str | None = None,
                instrument_hint: str | None = None) -> StrategySignal:
        return StrategySignal(
            strategy_id=self.id, underlying=underlying, ts=bv.index[i].to_pydatetime(), side=side,
            entry=float(entry), stop=float(stop), targets=[float(t) for t in targets], reasons=reasons,
            timeframe=self.timeframe, features=feature_snapshot(bv.feat, i),
            instrument_hint=instrument_hint or self.instrument_hint,
            time_exit=time_exit if time_exit is not None else self.time_exit,
        )

    @staticmethod
    def _valid(s: StrategySignal) -> bool:
        vals = [s.entry, s.stop, *s.targets]
        if not all(np.isfinite(v) for v in vals) or s.risk_points <= 0:
            return False
        if s.instrument_hint == "straddle":
            return True
        sg = s.side.sign
        if sg * (s.entry - s.stop) <= 0:
            return False
        return all(sg * (t - s.entry) > 0 for t in s.targets)


def feature_snapshot(feat: pd.DataFrame, i: int) -> dict[str, float]:
    """FEATURE_COLUMNS values at bar ``i`` (finite values only, so the dict is JSON-safe)."""
    out: dict[str, float] = {}
    row = feat.iloc[i]
    for c in FEATURE_COLUMNS:
        if c in row.index:
            v = row[c]
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if math.isfinite(fv):
                out[c] = fv
    return out


def r_targets(side: Side, entry: float, stop: float, rs: list[float]) -> list[float]:
    risk = abs(entry - stop)
    return [entry + side.sign * r * risk for r in rs]


def in_window(close_min: int, start: str, end: str) -> bool:
    return hhmm_to_min(start) <= close_min <= hhmm_to_min(end)


def bar_time(ts: pd.Timestamp) -> time:
    return ts.timetz().replace(tzinfo=None)


def htf_bars(bv: BarView, minutes: int) -> tuple[pd.DataFrame, np.ndarray]:
    """Higher-timeframe OHLC bars (session-anchored at 09:15) and, for each, the position of the base bar
    whose close completes it (``-1`` when that bar is absent, i.e. the HTF bar is not complete/visible).

    Only HTF bars with a completion position ``>= 0`` may be used; their values are final, so decisions made
    at that base bar are truncation-safe.
    """
    if bv.n == 0:
        return pd.DataFrame(columns=OHLC), np.array([], dtype=int)
    htf = resample_ohlc(bv.feat[OHLC], minutes)
    done_at = htf.index + pd.Timedelta(minutes=minutes - bv.step)
    pos = bv.index.get_indexer(done_at)
    return htf, np.asarray(pos, dtype=int)
