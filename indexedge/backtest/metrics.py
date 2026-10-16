"""Backtest statistics: Wilson interval, trade metrics (R and ₹ after costs) and Monte Carlo drawdowns.

``compute_metrics`` expects a trades DataFrame with at least ``r`` (net R multiple after costs) and optionally
``pnl`` (net ₹), ``entry_ts`` (timestamps) and ``win`` (bool). All numbers come from the data; with no trades
every statistic is ``None`` / 0.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


def wilson_ci(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion; (0, 1) when ``n == 0``."""
    if n <= 0:
        return (0.0, 1.0)
    p = wins / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, centre - half), min(1.0, centre + half))


def _max_drawdown(cum: np.ndarray) -> float:
    if len(cum) == 0:
        return 0.0
    peak = np.maximum.accumulate(np.r_[0.0, cum])
    return float(np.max(peak - np.r_[0.0, cum]))


def _pf(x: np.ndarray) -> float | None:
    gains, losses = x[x > 0].sum(), -x[x < 0].sum()
    if losses <= 0:
        return None if gains <= 0 else float("inf")
    return float(gains / losses)


def _clean(v: Any) -> Any:
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def compute_metrics(trades: pd.DataFrame | None) -> dict[str, Any]:
    """Trade statistics. R figures are net of costs when the ``r`` column is (the engine's ``r`` is net)."""
    empty = {
        "n": 0, "wins": 0, "losses": 0, "win_rate": None, "win_ci": [0.0, 1.0], "avg_r": None,
        "expectancy_r": None, "expectancy_rs": None, "avg_win_r": None, "avg_loss_r": None, "avg_rr": None,
        "profit_factor": None, "max_dd_r": 0.0, "max_dd_rs": 0.0, "sharpe": None, "total_r": 0.0,
        "total_pnl": 0.0, "by_year": {}, "first": None, "last": None,
    }
    if trades is None or len(trades) == 0 or "r" not in trades:
        return empty
    r = trades["r"].to_numpy(dtype=float)
    pnl = trades["pnl"].to_numpy(dtype=float) if "pnl" in trades else r.copy()
    win = trades["win"].to_numpy(dtype=bool) if "win" in trades else pnl > 0
    n = len(r)
    wins = int(win.sum())
    wr = r[win]
    lr = r[~win]
    avg_win = float(wr.mean()) if len(wr) else None
    avg_loss = float(lr.mean()) if len(lr) else None
    avg_rr = abs(avg_win / avg_loss) if avg_win is not None and avg_loss not in (None, 0.0) else None
    pf = _pf(r)
    sharpe = None
    if "entry_ts" in trades and n > 1:
        ts = pd.to_datetime(trades["entry_ts"])
        daily = pd.Series(r, index=ts.to_numpy()).groupby(ts.dt.date.to_numpy()).sum()
        if len(daily) > 1 and daily.std(ddof=1) > 0:
            sharpe = float(daily.mean() / daily.std(ddof=1) * math.sqrt(252))
    by_year: dict[str, dict[str, Any]] = {}
    if "entry_ts" in trades:
        years = pd.to_datetime(trades["entry_ts"]).dt.year.to_numpy()
        for y in sorted(set(years.tolist())):
            m = years == y
            yr = r[m]
            ypf = _pf(yr)
            by_year[str(y)] = {"n": int(m.sum()), "win_rate": float(win[m].mean()),
                               "expectancy_r": float(yr.mean()), "profit_factor": _clean(ypf),
                               "pnl": float(pnl[m].sum())}
    lo, hi = wilson_ci(wins, n)
    out = {
        "n": n, "wins": wins, "losses": n - wins, "win_rate": wins / n, "win_ci": [lo, hi],
        "avg_r": float(r.mean()), "expectancy_r": float(r.mean()), "expectancy_rs": float(pnl.mean()),
        "avg_win_r": avg_win, "avg_loss_r": avg_loss, "avg_rr": avg_rr, "profit_factor": _clean(pf),
        "profit_factor_raw": pf, "max_dd_r": _max_drawdown(np.cumsum(r)),
        "max_dd_rs": _max_drawdown(np.cumsum(pnl)), "sharpe": sharpe, "total_r": float(r.sum()),
        "total_pnl": float(pnl.sum()), "by_year": by_year,
        "first": str(trades["entry_ts"].iloc[0]) if "entry_ts" in trades else None,
        "last": str(trades["entry_ts"].iloc[-1]) if "entry_ts" in trades else None,
    }
    if pf is not None and math.isinf(pf):
        out["profit_factor"] = None
    return out


def monte_carlo(r_list: list[float] | np.ndarray, n: int = 2000, seed: int = 0) -> dict[str, Any]:
    """Bootstrap the trade sequence (resample with replacement) ``n`` times.

    Returns drawdown percentiles (in R), final-R percentiles, the probability of finishing negative and the
    worst losing streak percentiles.
    """
    r = np.asarray(r_list, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) == 0:
        return {"n_sims": 0, "n_trades": 0, "dd_p50": None, "dd_p95": None, "dd_p99": None, "final_p5": None,
                "final_p50": None, "final_p95": None, "prob_loss": None, "streak_p95": None}
    rng = np.random.default_rng(seed)
    sims = rng.choice(r, size=(n, len(r)), replace=True)
    cum = np.cumsum(sims, axis=1)
    peak = np.maximum.accumulate(np.concatenate([np.zeros((n, 1)), cum], axis=1), axis=1)[:, 1:]
    dd = np.max(peak - cum, axis=1)
    final = cum[:, -1]
    losses = sims <= 0
    streak = np.zeros(n)
    cur = np.zeros(n)
    for k in range(losses.shape[1]):
        cur = np.where(losses[:, k], cur + 1, 0)
        streak = np.maximum(streak, cur)
    pct = np.percentile
    return {
        "n_sims": n, "n_trades": len(r),
        "dd_p50": float(pct(dd, 50)), "dd_p95": float(pct(dd, 95)), "dd_p99": float(pct(dd, 99)),
        "final_p5": float(pct(final, 5)), "final_p50": float(pct(final, 50)), "final_p95": float(pct(final, 95)),
        "prob_loss": float((final < 0).mean()), "streak_p50": float(pct(streak, 50)),
        "streak_p95": float(pct(streak, 95)),
    }
