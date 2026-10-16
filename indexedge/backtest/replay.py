"""Bar-by-bar market replay (and Learn-page quizzes).

``ReplaySession(df, strategies)`` builds features once and scans every strategy once. Both are causal, so the
signals visible at bar *i* are exactly those a live scan of ``df[:i+1]`` would have produced (the
truncation tests in ``tests/strategies`` guarantee this). Stepping therefore only moves a cursor.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from indexedge.backtest.engine import ensure_features
from indexedge.backtest.simulate import TradeOutcome
from indexedge.core.types import StrategySignal
from indexedge.strategies.base import Strategy


class ReplaySession:
    def __init__(self, df: pd.DataFrame, strategies: dict[str, Strategy] | Iterable[Strategy],
                 underlying: str = "NIFTY", extra: dict[str, Any] | None = None, start: int = 0) -> None:
        self.feat = ensure_features(df)
        self.underlying = underlying.upper()
        self.strategies: dict[str, Strategy] = (dict(strategies) if isinstance(strategies, dict)
                                                else {s.id: s for s in strategies})
        self._by_bar: dict[int, list[StrategySignal]] = {}
        pos = {ts: k for k, ts in enumerate(self.feat.index)}
        for strat in self.strategies.values():
            for sig in strat.scan(self.feat, self.underlying, extra):
                k = pos.get(pd.Timestamp(sig.ts))
                if k is not None:
                    self._by_bar.setdefault(k, []).append(sig)
        self.index = start - 1          # nothing visible until the first step

    # ------------------------------------------------------------------ navigation
    def __len__(self) -> int:
        return len(self.feat)

    @property
    def done(self) -> bool:
        return self.index >= len(self.feat) - 1

    @property
    def current_ts(self) -> pd.Timestamp | None:
        return self.feat.index[self.index] if 0 <= self.index < len(self.feat) else None

    def reset(self, index: int = -1) -> None:
        self.index = index

    def seek(self, index: int) -> list[StrategySignal]:
        self.index = max(-1, min(index, len(self.feat) - 1))
        return self.signals_at(self.index)

    def step(self, n: int = 1) -> list[StrategySignal]:
        """Advance ``n`` bars; returns the signals confirmed on the bars stepped over (oldest first)."""
        out: list[StrategySignal] = []
        for _ in range(n):
            if self.done:
                break
            self.index += 1
            out.extend(self.signals_at(self.index))
        return out

    # ------------------------------------------------------------------ queries
    def signals_at(self, index: int) -> list[StrategySignal]:
        return list(self._by_bar.get(index, []))

    def visible_signals(self) -> list[tuple[int, StrategySignal]]:
        return [(k, s) for k in sorted(self._by_bar) if k <= self.index for s in self._by_bar[k]]

    def candles(self) -> pd.DataFrame:
        """Candles visible so far (``open..volume`` up to the cursor)."""
        return self.feat.iloc[: self.index + 1][["open", "high", "low", "close", "volume"]]

    def days(self) -> list[date]:
        return sorted(set(self.feat.index.date))

    def day_range(self, day: date) -> tuple[int, int]:
        """``[start, stop)`` bar positions of ``day``."""
        m = np.flatnonzero(self.feat.index.date == day)
        if not len(m):
            raise KeyError(f"no bars on {day}")
        return int(m[0]), int(m[-1]) + 1

    def outcome(self, signal: StrategySignal, max_bars: int = 75) -> TradeOutcome | None:
        """What happened after ``signal`` (uses future bars — for review/quiz answers only)."""
        try:
            k = self.feat.index.get_loc(pd.Timestamp(signal.ts))
        except KeyError:
            return None
        bars = self.feat.iloc[k + 1:k + 1 + max_bars + 80][["open", "high", "low", "close", "volume"]]
        strat = self.strategies.get(signal.strategy_id)
        if strat is None:
            return None
        return strat.simulate(signal, bars, max_bars=max_bars)

    def quiz_point(self, rng: np.random.Generator | int | None = None,
                   min_bars_after: int = 6) -> tuple[int, StrategySignal] | None:
        """A random (bar index, signal) for a Learn quiz: the user sees candles up to ``index`` and guesses
        whether the setup hits its target or stop. Only signals with a known outcome are chosen."""
        r = rng if isinstance(rng, np.random.Generator) else np.random.default_rng(rng)
        cands = [(k, s) for k in sorted(self._by_bar) for s in self._by_bar[k]
                 if k + min_bars_after < len(self.feat) and s.instrument_hint != "straddle"]
        r.shuffle(cands)
        for k, s in cands:
            out = self.outcome(s)
            if out is not None and out.outcome in ("target", "stop"):
                return k, s
        return cands[0] if cands else None
