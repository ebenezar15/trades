"""Turn strategy signals into a supervised-learning dataset (one row per signal).

Every outcome comes from :func:`indexedge.backtest.simulate.simulate_exit`, the same simulator the backtester
uses, so model labels and backtest results always agree.

Columns of the returned frame:

* ``ts, strategy_id, underlying, side`` (+1 BUY / -1 SELL), ``strat_<id>`` one-hot columns
* every name in ``FEATURE_COLUMNS`` taken at the signal bar (causal: computed from bars up to and including it)
* ``ctx_macro_bias, ctx_vix, ctx_event_risk`` (NaN when no context is available)
* labels / forward quantities (never use them as features): ``win`` (1 = target hit before stop),
  ``win_points`` (1 = closed in profit), ``r_multiple``, ``outcome``, ``exit_ts``, ``bars_held``, ``fwd_ret_pct``
  (signed close-to-close return over the label horizon, used only by the leakage check).
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import numpy as np
import pandas as pd

from indexedge.backtest.simulate import simulate_exit
from indexedge.core.types import StrategySignal
from indexedge.features.engine import FEATURE_COLUMNS, build_features

CONTEXT_COLUMNS = ["ctx_macro_bias", "ctx_vix", "ctx_event_risk"]
LABEL_COLUMNS = ["win", "win_points", "r_multiple", "outcome", "exit_ts", "bars_held", "fwd_ret_pct"]
META_COLUMNS = ["ts", "strategy_id", "underlying"]


def _context_for(context: Any, ts: pd.Timestamp) -> dict[str, float]:
    """Context columns as of ``ts`` (backward as-of lookup; never uses later context)."""
    out = {c: np.nan for c in CONTEXT_COLUMNS}
    if context is None:
        return out
    if isinstance(context, pd.DataFrame):
        if context.empty:
            return out
        ctx = context.sort_index()
        pos = ctx.index.searchsorted(ts, side="right") - 1
        if pos < 0:
            return out
        row = ctx.iloc[pos]
        for c in CONTEXT_COLUMNS:
            if c in row.index:
                out[c] = float(row[c]) if pd.notna(row[c]) else np.nan
        return out
    if callable(context):
        got = context(ts) or {}
    elif isinstance(context, Mapping):
        got = context
    else:
        got = {}
    for c in CONTEXT_COLUMNS:
        v = got.get(c, got.get(c.removeprefix("ctx_")))
        out[c] = float(v) if v is not None and pd.notna(v) else np.nan
    return out


def label_signals(
    signals: Iterable[StrategySignal],
    df: pd.DataFrame,
    max_bars: int = 24,
    feat: pd.DataFrame | None = None,
    context: Any = None,
) -> pd.DataFrame:
    """Label each signal with its simulated outcome and attach features at the signal bar.

    ``df`` are the candles the signals were generated on; ``feat`` (optional) is ``build_features(df)`` if
    already computed. ``context`` may be a DataFrame indexed by time with ``ctx_*`` columns, a mapping, or a
    callable ``ts -> mapping``. Signals whose bar is not in ``df`` or that cannot be simulated (no later bars on
    the same day, zero risk) are dropped.
    """
    signals = list(signals)
    cols = META_COLUMNS + ["side"] + FEATURE_COLUMNS + CONTEXT_COLUMNS + LABEL_COLUMNS
    if not signals or df is None or df.empty:
        return pd.DataFrame(columns=cols)
    if feat is None:
        feat = build_features(df)
    candles = feat[["open", "high", "low", "close"]]
    idx = candles.index
    closes = candles["close"].to_numpy(float)
    rows: list[dict[str, Any]] = []
    for sig in signals:
        ts = pd.Timestamp(sig.ts)
        if ts.tzinfo is None and idx.tz is not None:
            ts = ts.tz_localize(idx.tz)
        i = idx.get_indexer([ts])[0]
        if i < 0:
            continue
        out = simulate_exit(sig, candles.iloc[i + 1:], max_bars=max_bars)
        if out is None:
            continue
        s = sig.side.sign
        j = min(i + max_bars, len(closes) - 1)
        # forward return within the same session as the label (diagnostic only)
        while j > i and idx[j].date() != idx[i].date():
            j -= 1
        fwd = s * (closes[j] / closes[i] - 1.0) * 100.0 if closes[i] else np.nan
        row: dict[str, Any] = {"ts": idx[i], "strategy_id": sig.strategy_id, "underlying": sig.underlying,
                               "side": float(s)}
        frow = feat.iloc[i]
        for c in FEATURE_COLUMNS:
            row[c] = float(frow[c]) if c in frow.index and pd.notna(frow[c]) else np.nan
        row.update(_context_for(context, idx[i]))
        row.update({
            "win": int(out.outcome == "target"),
            "win_points": int(out.win),
            "r_multiple": float(out.r_multiple),
            "outcome": out.outcome,
            "exit_ts": pd.Timestamp(out.exit_ts),
            "bars_held": int(out.bars_held),
            "fwd_ret_pct": float(fwd),
        })
        rows.append(row)
    data = pd.DataFrame(rows, columns=cols)
    if data.empty:
        return data
    data = data.sort_values("ts", kind="stable").reset_index(drop=True)
    return add_strategy_onehot(data)


def add_strategy_onehot(data: pd.DataFrame) -> pd.DataFrame:
    """Add ``strat_<id>`` 0/1 columns for every strategy present in ``data``."""
    data = data.copy()
    for sid in sorted(data["strategy_id"].dropna().unique()):
        data[f"strat_{sid}"] = (data["strategy_id"] == sid).astype(float)
    return data


def default_feature_cols(data: pd.DataFrame) -> list[str]:
    """Model inputs: FEATURE_COLUMNS + side + strategy one-hots + context columns (labels excluded)."""
    cols = [c for c in FEATURE_COLUMNS if c in data.columns] + ["side"]
    cols += sorted(c for c in data.columns if c.startswith("strat_"))
    cols += [c for c in CONTEXT_COLUMNS if c in data.columns]
    return cols
