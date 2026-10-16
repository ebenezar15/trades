"""Event-driven single-position backtester for strategy plug-ins.

* Features are built once (causal), the strategy scans them, and every signal is simulated with the
  strategy's ``simulate`` method — :func:`indexedge.backtest.simulate.simulate_exit` for directional
  strategies, a modelled premium path for the straddle strategies.
* One open trade at a time: a signal that arrives before the previous trade's exit bar is skipped.
* P&L (₹) for directional strategies is futures-equivalent: ``points × lot_size × lots × point_value`` minus
  round-trip costs (:class:`CostModel`, futures rates + slippage). Premium strategies use option rates on every
  leg. ``r`` is the **net** R multiple (after costs); ``r_gross`` is before costs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from indexedge.backtest.costs import CostModel
from indexedge.backtest.metrics import compute_metrics
from indexedge.core.types import StrategySignal
from indexedge.features.engine import build_features
from indexedge.strategies.base import Strategy

TRADE_COLUMNS = [
    "strategy_id", "underlying", "signal_ts", "entry_ts", "exit_ts", "side", "kind", "entry", "exit", "stop",
    "target", "outcome", "points", "risk_points", "r_gross", "r", "pnl_gross", "costs", "pnl", "win",
    "bars_held", "mfe", "mae", "qty", "regime", "reasons",
]


@dataclass
class BacktestResult:
    trades: pd.DataFrame
    metrics: dict[str, Any]
    signals: list[StrategySignal] = field(default_factory=list)
    skipped_overlap: int = 0


def ensure_features(df: pd.DataFrame) -> pd.DataFrame:
    """``df`` if it already holds features, else ``build_features(df)``."""
    if df is not None and "atr14" in df.columns and "regime" in df.columns:
        return df
    return build_features(df)


def backtest_signals(strategy: Strategy, signals: list[StrategySignal], feat: pd.DataFrame, lot_size: int,
                     cost_model: CostModel | None = None, point_value: float = 1.0, max_bars: int = 75,
                     lots: int = 1) -> tuple[pd.DataFrame, int]:
    """Simulate ``signals`` (sorted by time) one position at a time. Returns (trades, skipped_overlap)."""
    cm = cost_model or CostModel()
    qty = int(lot_size) * int(lots)
    ohlc = feat[["open", "high", "low", "close", "volume"]]
    regime = feat["regime"] if "regime" in feat else pd.Series("unknown", index=feat.index)
    rows: list[dict[str, Any]] = []
    busy_until = None
    skipped = 0
    for sig in sorted(signals, key=lambda s: s.ts):
        ts = pd.Timestamp(sig.ts)
        if busy_until is not None and ts < busy_until:
            skipped += 1
            continue
        try:
            i = feat.index.get_loc(ts)
        except KeyError:
            continue
        if not isinstance(i, (int, np.integer)):
            continue
        bars = ohlc.iloc[i + 1:i + 1 + max_bars + 80]
        out = strategy.simulate(sig, bars, max_bars=max_bars)
        if out is None:
            continue
        busy_until = pd.Timestamp(out.exit_ts)
        legs = getattr(out, "legs", None)
        if legs:
            kind = "option"
            gross = out.points * qty * point_value
            costs = sum(cm.round_trip("option", qty, e, x, side) for e, x, side in legs)
            risk_pts = float(getattr(out, "risk_points", 0.0)) or sig.risk_points
        else:
            kind = "future"
            gross = out.points * qty * point_value
            costs = cm.round_trip("future", qty, out.entry_price, out.exit_price, sig.side)
            risk_pts = sig.risk_points
        risk_rs = risk_pts * qty * point_value
        net = gross - costs
        rows.append({
            "strategy_id": sig.strategy_id, "underlying": sig.underlying, "signal_ts": ts,
            "entry_ts": pd.Timestamp(out.entry_ts), "exit_ts": pd.Timestamp(out.exit_ts), "side": sig.side.value,
            "kind": kind, "entry": float(out.entry_price), "exit": float(out.exit_price), "stop": sig.stop,
            "target": sig.targets[-1] if sig.targets else np.nan, "outcome": out.outcome,
            "points": float(out.points), "risk_points": float(risk_pts), "r_gross": float(out.r_multiple),
            "r": float(net / risk_rs) if risk_rs > 0 else 0.0, "pnl_gross": float(gross), "costs": float(costs),
            "pnl": float(net), "win": bool(net > 0), "bars_held": int(out.bars_held),
            "mfe": float(out.mfe_points), "mae": float(out.mae_points), "qty": qty,
            "regime": str(regime.iloc[i]), "reasons": " | ".join(sig.reasons),
        })
    trades = pd.DataFrame(rows, columns=TRADE_COLUMNS)
    return trades, skipped


def run_backtest(strategy: Strategy, df: pd.DataFrame, underlying: str, lot_size: int,
                 cost_model: CostModel | None = None, point_value: float = 1.0, max_bars: int = 75,
                 extra: dict[str, Any] | None = None, lots: int = 1) -> BacktestResult:
    """Scan ``df`` (raw candles or prebuilt features) with ``strategy`` and simulate the signals."""
    feat = ensure_features(df)
    signals = strategy.scan(feat, underlying, extra)
    trades, skipped = backtest_signals(strategy, signals, feat, lot_size, cost_model, point_value, max_bars, lots)
    return BacktestResult(trades, compute_metrics(trades), signals, skipped)
