"""Fixtures for model tests: synthetic candles, a trivial signal rule and tabular datasets (all offline)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd
import pytest

from indexedge.core.clock import IST
from indexedge.core.types import Side, StrategySignal


def make_candles(days: int = 30, seed: int = 0, start: date = date(2026, 1, 5), interval: int = 5,
                 price: float = 24000.0) -> pd.DataFrame:
    """Weekday 09:15-15:30 sessions, GBM closes, U-shaped volume."""
    rng = np.random.default_rng(seed)
    per_day = (6 * 60 + 15) // interval
    stamps: list[datetime] = []
    d = start
    while len(stamps) < days * per_day:
        if d.weekday() < 5:
            t0 = datetime.combine(d, time(9, 15), IST)
            stamps += [t0 + timedelta(minutes=interval * k) for k in range(per_day)]
        d += timedelta(days=1)
    n = len(stamps)
    rets = rng.normal(0, 0.0012, n)
    rets[::per_day] += rng.normal(0, 0.004, days)
    close = price * np.exp(np.cumsum(rets))
    open_ = np.r_[price, close[:-1]]
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 0.0008, n)) * close
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 0.0008, n)) * close
    k = np.tile(np.arange(per_day), days)
    vol = ((1.0 + 2.0 * ((k - per_day / 2) / (per_day / 2)) ** 2) * rng.lognormal(10, 0.3, n)).round()
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": vol},
                        index=pd.DatetimeIndex(stamps))


class RsiRule:
    """Trivial causal rule: every 4th bar between 10:00 and 14:00, trade in the direction of RSI vs 50."""

    id = "rsi_rule"

    def scan(self, feat: pd.DataFrame, underlying: str) -> list[StrategySignal]:
        out = []
        for i, (ts, row) in enumerate(feat.iterrows()):
            if i % 4 or not (600 <= row["minute_of_day"] <= 840):
                continue
            if not (np.isfinite(row["rsi14"]) and np.isfinite(row["atr14"]) and row["atr14"] > 0):
                continue
            side = Side.BUY if row["rsi14"] > 50 else Side.SELL
            c, a = float(row["close"]), float(row["atr14"])
            out.append(StrategySignal(self.id, underlying, ts, side, c, c - side.sign * a,
                                      [c + side.sign * 1.5 * a], ["rsi side"]))
        return out


class BrokenRule:
    id = "broken"

    def scan(self, feat, underlying):
        raise RuntimeError("boom")


def make_tabular(n: int = 1200, seed: int = 0, signal: float = 1.5, nan_frac: float = 0.05) -> pd.DataFrame:
    """Labelled dataset with one informative feature ``x0`` (P(win)=sigmoid(signal*x0)) and noise features."""
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2024-01-01 10:00", periods=n, freq="7h", tz=IST)
    X = rng.normal(size=(n, 5))
    p = 1 / (1 + np.exp(-signal * X[:, 0]))
    y = (rng.random(n) < p).astype(int)
    df = pd.DataFrame(X, columns=[f"x{i}" for i in range(5)])
    for c in ("x1", "x2"):
        df.loc[rng.random(n) < nan_frac, c] = np.nan
    df["ts"] = ts
    df["exit_ts"] = ts + pd.Timedelta(hours=1)
    df["strategy_id"] = "tab"
    df["win"] = y
    df["r_multiple"] = np.where(y == 1, 1.5, -1.0)
    df["fwd_ret_pct"] = (y * 2 - 1) * np.abs(rng.normal(0.3, 0.1, n))
    return df


FEATS = [f"x{i}" for i in range(5)]


@pytest.fixture(scope="session")
def candles() -> pd.DataFrame:
    return make_candles(days=40, seed=3)


@pytest.fixture(scope="session")
def feat(candles):
    from indexedge.features.engine import build_features

    return build_features(candles)


@pytest.fixture(scope="session")
def labelled(candles, feat):
    from indexedge.models.labels import label_signals

    sigs = RsiRule().scan(feat, "NIFTY")
    return sigs, label_signals(sigs, candles, max_bars=24, feat=feat)


@pytest.fixture
def tabular():
    return make_tabular


@pytest.fixture
def rule():
    return RsiRule()


@pytest.fixture
def broken_rule():
    return BrokenRule()


@pytest.fixture
def make_candles_fn():
    return make_candles
