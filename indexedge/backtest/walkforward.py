"""Rolling walk-forward evaluation.

Sessions are split into consecutive folds of ``train_days`` (in-sample) followed by ``test_days``
(out-of-sample); the window then rolls forward by ``test_days``. Strategies without a ``param_grid`` keep
their parameters fixed, so "training" only measures in-sample performance. Strategies that declare a
``param_grid`` pick, per fold, the combination with the best in-sample expectancy (min trades required) and
are judged only on the following test window.

Because features and signals are causal, the full history is scanned once per parameter set and trades are
assigned to folds by entry date (equivalent to re-running each window, without the warm-up loss).
"""
from __future__ import annotations

import itertools
from typing import Any

import numpy as np
import pandas as pd

from indexedge.backtest.costs import CostModel
from indexedge.backtest.engine import backtest_signals, ensure_features
from indexedge.backtest.metrics import compute_metrics
from indexedge.strategies.base import Strategy


def _combos(grid: dict[str, list[Any]] | None) -> list[dict[str, Any]]:
    if not grid:
        return [{}]
    keys = list(grid)
    return [dict(zip(keys, vals)) for vals in itertools.product(*(grid[k] for k in keys))]


def walk_forward(strategy: Strategy, df: pd.DataFrame, underlying: str, lot_size: int,
                 cost_model: CostModel | None = None, train_days: int = 60, test_days: int = 20,
                 point_value: float = 1.0, max_bars: int = 75, extra: dict[str, Any] | None = None,
                 min_train_trades: int = 5, oos_min_trades: int = 100) -> dict[str, Any]:
    feat = ensure_features(df)
    warnings: list[str] = []
    if len(feat) == 0:
        return {"folds": [], "oos_metrics": compute_metrics(None), "is_metrics": compute_metrics(None),
                "stability": {}, "warnings": ["no data"], "param_grid": strategy.param_grid}
    days = sorted(set(feat.index.date))
    combos = _combos(strategy.param_grid)
    runs: list[pd.DataFrame] = []
    for combo in combos:
        strat = type(strategy)({**strategy.params, **combo})
        sigs = strat.scan(feat, underlying, extra)
        trades, _ = backtest_signals(strat, sigs, feat, lot_size, cost_model, point_value, max_bars)
        trades["day"] = trades["entry_ts"].map(lambda t: t.date()) if len(trades) else pd.Series(dtype=object)
        runs.append(trades)

    folds: list[dict[str, Any]] = []
    oos_parts: list[pd.DataFrame] = []
    is_parts: list[pd.DataFrame] = []
    start = 0
    while start + train_days + test_days <= len(days):
        tr = set(days[start:start + train_days])
        te = set(days[start + train_days:start + train_days + test_days])
        best_k, best_score = 0, -np.inf
        for k, trades in enumerate(runs):
            t_in = trades[trades["day"].isin(tr)] if len(trades) else trades
            if len(combos) == 1:
                break
            if len(t_in) < min_train_trades:
                continue
            score = float(t_in["r"].mean())
            if score > best_score:
                best_k, best_score = k, score
        t_in = runs[best_k][runs[best_k]["day"].isin(tr)] if len(runs[best_k]) else runs[best_k]
        t_out = runs[best_k][runs[best_k]["day"].isin(te)] if len(runs[best_k]) else runs[best_k]
        m_in, m_out = compute_metrics(t_in), compute_metrics(t_out)
        folds.append({
            "train_start": min(tr).isoformat(), "train_end": max(tr).isoformat(),
            "test_start": min(te).isoformat(), "test_end": max(te).isoformat(), "params": combos[best_k],
            "is_n": m_in["n"], "is_expectancy_r": m_in["expectancy_r"], "is_win_rate": m_in["win_rate"],
            "oos_n": m_out["n"], "oos_expectancy_r": m_out["expectancy_r"], "oos_win_rate": m_out["win_rate"],
            "oos_profit_factor": m_out["profit_factor"],
        })
        oos_parts.append(t_out)
        is_parts.append(t_in)
        start += test_days

    if not folds:
        warnings.append(f"not enough history for one fold ({len(days)} sessions < {train_days + test_days})")
    oos = pd.concat(oos_parts) if oos_parts else None
    ins = pd.concat(is_parts) if is_parts else None
    oos_m, is_m = compute_metrics(oos), compute_metrics(ins)
    fold_exp = [f["oos_expectancy_r"] for f in folds if f["oos_n"] > 0]
    stability = {
        "folds": len(folds),
        "folds_with_trades": len(fold_exp),
        "positive_fold_share": float(np.mean([e > 0 for e in fold_exp])) if fold_exp else None,
        "oos_expectancy_std": float(np.std(fold_exp, ddof=1)) if len(fold_exp) > 1 else None,
    }
    if 0 < len(folds) < 3:
        warnings.append(f"only {len(folds)} walk-forward fold(s): stability cannot be judged")
    if oos_m["n"] < oos_min_trades:
        warnings.append(f"insufficient sample: {oos_m['n']} out-of-sample trades (< {oos_min_trades})")
    ie, oe = is_m["expectancy_r"], oos_m["expectancy_r"]
    if ie is not None and oe is not None and ie > 0 and (oe <= 0 or oe < 0.5 * ie):
        warnings.append(f"possible overfitting: in-sample expectancy {ie:+.2f}R vs out-of-sample {oe:+.2f}R")
    n_params = len(strategy.param_grid or {})
    if n_params and oos_m["n"] and n_params / oos_m["n"] > 0.05:
        warnings.append(f"too many tuned parameters per trade ({n_params} params / {oos_m['n']} OOS trades)")
    if stability["positive_fold_share"] is not None and stability["positive_fold_share"] < 0.5 and \
            oe is not None and oe > 0:
        warnings.append("edge concentrated in few folds (fewer than half of the folds are profitable)")
    return {"folds": folds, "oos_metrics": oos_m, "is_metrics": is_m, "stability": stability,
            "warnings": warnings, "param_grid": strategy.param_grid,
            "train_days": train_days, "test_days": test_days}
