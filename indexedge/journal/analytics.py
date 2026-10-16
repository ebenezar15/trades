"""Performance analytics over journal trades (closed trades only).

All money figures are net of costs (``pnl - costs``). Groups or totals with fewer than ``min_trades``
samples carry ``insufficient_data: True`` — the numbers are shown for the user's own record-keeping but must
not be presented as validated accuracy.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from indexedge.core.clock import IST

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DIMENSIONS = {
    "strategy": "strategy_id",
    "regime": "regime",
    "hour": "_hour",
    "weekday": "_weekday",
    "underlying": "underlying",
    "mode": "mode",
}


def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else round(f, 4)


def _closed(trades: pd.DataFrame) -> pd.DataFrame:
    if trades is None or trades.empty:
        return pd.DataFrame()
    df = trades.copy()
    if "status" in df.columns:
        df = df[df["status"] == "closed"]
    df = df[df["pnl"].notna()]
    if df.empty:
        return df
    costs = df["costs"].fillna(0.0).astype(float) if "costs" in df.columns else 0.0
    df["net_pnl"] = df["pnl"].astype(float) - costs
    df["entry_ts"] = pd.to_datetime(df["entry_ts"])
    if df["entry_ts"].dt.tz is None:
        df["entry_ts"] = df["entry_ts"].dt.tz_localize(IST)
    df["entry_ts"] = df["entry_ts"].dt.tz_convert(IST)
    if "exit_ts" in df.columns:
        df["exit_ts"] = pd.to_datetime(df["exit_ts"])
        if df["exit_ts"].dt.tz is None:
            df["exit_ts"] = df["exit_ts"].dt.tz_localize(IST)
        df["exit_ts"] = df["exit_ts"].dt.tz_convert(IST).fillna(df["entry_ts"])
    else:
        df["exit_ts"] = df["entry_ts"]
    df["_hour"] = df["entry_ts"].dt.strftime("%H:00")
    df["_weekday"] = df["entry_ts"].dt.dayofweek.map(lambda d: WEEKDAYS[int(d)])
    for col in ("strategy_id", "regime", "underlying", "mode"):
        if col not in df.columns:
            df[col] = "unknown"
        df[col] = df[col].astype(object).where(df[col].notna(), "unknown")
    return df.sort_values(["exit_ts", "entry_ts"]).reset_index(drop=True)


def _stats(df: pd.DataFrame, min_trades: int) -> dict[str, Any]:
    net = df["net_pnl"].astype(float)
    n = len(net)
    wins, losses = net[net > 0], net[net < 0]
    gross_loss = float(-losses.sum())
    pf = float(wins.sum()) / gross_loss if gross_loss > 0 else None
    r = df["r_multiple"].astype(float).dropna() if "r_multiple" in df.columns else pd.Series(dtype=float)
    return {
        "n": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": _f(len(wins) / n) if n else None,
        "net_pnl": _f(net.sum()),
        "expectancy": _f(net.mean()) if n else None,
        "avg_win": _f(wins.mean()) if len(wins) else None,
        "avg_loss": _f(losses.mean()) if len(losses) else None,
        "profit_factor": _f(pf),
        "avg_r": _f(r.mean()) if len(r) else None,
        "insufficient_data": n < min_trades,
    }


def breakdown(df: pd.DataFrame, column: str, min_trades: int = 50) -> dict[str, dict[str, Any]]:
    if df.empty or column not in df.columns:
        return {}
    return {str(k): _stats(g, min_trades) for k, g in df.groupby(column, sort=True)}


def performance(trades: pd.DataFrame, capital: float | None = None, min_trades: int = 50) -> dict[str, Any]:
    """Equity curve, drawdown, win rate, PF, expectancy, avg R and breakdowns of closed trades."""
    df = _closed(trades)
    if df.empty:
        empty = _stats(pd.DataFrame({"net_pnl": pd.Series(dtype=float)}), min_trades)
        return {
            **empty,
            "equity": [],
            "drawdown": [],
            "max_drawdown": 0.0,
            "max_drawdown_pct": None,
            "plan_vs_actual": {},
            **{f"by_{k}": {} for k in DIMENSIONS},
        }
    base = float(capital or 0.0)
    equity = base + df["net_pnl"].cumsum()
    peak = np.maximum.accumulate(np.concatenate([[base], equity.to_numpy()]))[1:]
    dd = equity.to_numpy() - peak
    times = [int(t.timestamp()) for t in df["exit_ts"]]
    max_dd = float(-dd.min()) if len(dd) else 0.0
    out = _stats(df, min_trades)
    out.update(
        {
            "equity": [{"time": t, "value": round(float(v), 2)} for t, v in zip(times, equity)],
            "drawdown": [{"time": t, "value": round(float(v), 2)} for t, v in zip(times, dd)],
            "max_drawdown": round(max_dd, 2),
            "max_drawdown_pct": _f(100 * max_dd / capital) if capital else None,
            "plan_vs_actual": _plan_vs_actual(df),
        }
    )
    for name, col in DIMENSIONS.items():
        out[f"by_{name}"] = breakdown(df, col, min_trades)
    return out


def _plan_vs_actual(df: pd.DataFrame) -> dict[str, Any]:
    if "planned_entry" not in df.columns:
        return {}
    d = df[df["planned_entry"].notna()]
    if d.empty:
        return {"n": 0}
    sign = np.where(d["side"] == "BUY", 1.0, -1.0)
    slip = (d["entry_price"].astype(float) - d["planned_entry"].astype(float)) * sign
    risk = (d["planned_entry"].astype(float) - d["stop"].astype(float)).abs().replace(0, np.nan)
    slip_r = (slip / risk).dropna()
    return {
        "n": len(d),
        "avg_slippage_points": _f(slip.mean()),
        "avg_slippage_r": _f(slip_r.mean()) if len(slip_r) else None,
    }


# ---------------------------------------------------------------------------------------- discipline
def discipline_score(
    trades: pd.DataFrame,
    max_trades_per_day: int = 5,
    cooldown_min: int = 10,
    revenge_multiplier: float = 1.0,
    entry_tolerance_r: float = 0.1,
    stop_tolerance_r: float = 0.2,
) -> dict[str, Any]:
    """0-100 score: equal-weight share of trades that respected each process rule."""
    df = _closed(trades)
    if df.empty:
        return {"score": None, "n": 0, "components": {}, "issues": []}
    df = df.sort_values("entry_ts").reset_index(drop=True)
    sign = np.where(df["side"] == "BUY", 1.0, -1.0)
    stop = df["stop"].astype(float) if "stop" in df.columns else pd.Series(np.nan, index=df.index)
    planned = (
        df["planned_entry"].astype(float)
        if "planned_entry" in df.columns
        else pd.Series(np.nan, index=df.index)
    ).fillna(df["entry_price"].astype(float))
    risk = (planned - stop).abs().replace(0, np.nan)

    has_stop = stop.notna()
    viol = df["rule_violations"] if "rule_violations" in df.columns else pd.Series([[]] * len(df))
    no_viol = viol.map(lambda v: not v if isinstance(v, (list, tuple)) else pd.isna(v) or not v)
    slip_r = ((df["entry_price"].astype(float) - planned) * sign / risk).fillna(0.0)
    in_plan = slip_r <= entry_tolerance_r
    overrun = ((stop - df["exit_price"].astype(float)) * sign / risk).fillna(0.0)
    stop_ok = overrun <= stop_tolerance_r
    day = df["entry_ts"].dt.date
    no_overtrade = df.groupby(day).cumcount() < max_trades_per_day

    no_revenge = []
    for i, row in df.iterrows():
        prior = df[(day == day[i]) & (df["exit_ts"] <= row["entry_ts"]) & (df.index != i)]
        ok = True
        if not prior.empty:
            last = prior.sort_values("exit_ts").iloc[-1]
            if last["net_pnl"] < 0:
                mins = (row["entry_ts"] - last["exit_ts"]).total_seconds() / 60
                if mins < cooldown_min or row["qty"] > last["qty"] * revenge_multiplier:
                    ok = False
        no_revenge.append(ok)
    no_revenge_s = pd.Series(no_revenge, index=df.index)

    comps = {
        "stop_defined": has_stop,
        "no_rule_violations": no_viol,
        "entry_within_plan": in_plan,
        "stop_honoured": stop_ok,
        "no_overtrading": no_overtrade,
        "no_revenge_trading": no_revenge_s,
    }
    fractions = {k: round(float(v.mean()), 4) for k, v in comps.items()}
    labels = {
        "stop_defined": "trades without a stop",
        "no_rule_violations": "trades with rule violations",
        "entry_within_plan": "chased entries",
        "stop_honoured": "stops not honoured",
        "no_overtrading": "trades beyond the daily limit",
        "no_revenge_trading": "revenge trades after a loss",
    }
    issues = [
        f"{int((~v.astype(bool)).sum())} {labels[k]}" for k, v in comps.items() if (~v.astype(bool)).any()
    ]
    return {
        "score": round(100 * float(np.mean(list(fractions.values()))), 1),
        "n": len(df),
        "components": fractions,
        "issues": issues,
    }


# ------------------------------------------------------------------------------------- summaries
def best_and_worst(trades: pd.DataFrame, min_n: int = 3) -> dict[str, dict[str, Any]]:
    """Per dimension: highest / lowest expectancy group among groups with >= ``min_n`` trades."""
    df = _closed(trades)
    out: dict[str, dict[str, Any]] = {}
    for name, col in DIMENSIONS.items():
        if name == "mode" or df.empty:
            continue
        groups = {k: v for k, v in breakdown(df, col, min_trades=min_n).items() if v["n"] >= min_n}
        if not groups:
            continue
        ranked = sorted(groups.items(), key=lambda kv: kv[1]["expectancy"] or 0.0)
        out[name] = {
            "best": ranked[-1][0],
            "best_stats": ranked[-1][1],
            "worst": ranked[0][0],
            "worst_stats": ranked[0][1],
        }
    return out


def period_summaries(
    trades: pd.DataFrame, freq: str = "W", min_n: int = 3, **discipline_kw: Any
) -> list[dict[str, Any]]:
    """Weekly (``freq='W'``) or monthly (``'M'``) summaries: totals, what works best, discipline and text."""
    if freq not in ("W", "M"):
        raise ValueError("freq must be 'W' or 'M'")
    df = _closed(trades)
    if df.empty:
        return []
    if freq == "W":
        iso = df["exit_ts"].dt.isocalendar()
        keys = iso["year"].astype(str) + "-W" + iso["week"].astype(int).map("{:02d}".format)
    else:
        keys = df["exit_ts"].dt.strftime("%Y-%m")
    out = []
    for key, g in df.groupby(keys, sort=True):
        st = _stats(g, min_trades=50)
        bw = best_and_worst(g, min_n=min_n)
        disc = discipline_score(g, **discipline_kw)
        out.append(
            {
                "period": str(key),
                "start": g["exit_ts"].min().date().isoformat(),
                "end": g["exit_ts"].max().date().isoformat(),
                **st,
                "best_worst": bw,
                "discipline": disc,
                "text": _summary_text(str(key), freq, st, bw, disc),
            }
        )
    return out


def weekly_summaries(trades: pd.DataFrame, **kw: Any) -> list[dict[str, Any]]:
    return period_summaries(trades, "W", **kw)


def monthly_summaries(trades: pd.DataFrame, **kw: Any) -> list[dict[str, Any]]:
    return period_summaries(trades, "M", **kw)


def _summary_text(key: str, freq: str, st: dict, bw: dict, disc: dict) -> str:
    label = "Week" if freq == "W" else "Month"
    wr = f"{st['win_rate']:.0%}" if st["win_rate"] is not None else "n/a"
    parts = [f"{label} {key}: {st['n']} trades, net ₹{st['net_pnl']:,.0f}, win rate {wr}."]
    best = [
        f"{dim} {v['best']} (₹{v['best_stats']['expectancy']:+,.0f}/trade over {v['best_stats']['n']})"
        for dim, v in bw.items()
    ]
    worst = [
        f"{dim} {v['worst']} (₹{v['worst_stats']['expectancy']:+,.0f}/trade)"
        for dim, v in bw.items()
        if v["worst"] != v["best"]
    ]
    parts.append(
        "What worked best for you: " + "; ".join(best) + "."
        if best
        else "Too few trades per group to say what works best yet."
    )
    if worst:
        parts.append("Weakest: " + "; ".join(worst) + ".")
    if disc.get("score") is not None:
        parts.append(
            f"Discipline score {disc['score']:.0f}/100"
            + (f" ({', '.join(disc['issues'])})." if disc["issues"] else ".")
        )
    if st["insufficient_data"]:
        parts.append("Sample is small — treat these as observations, not proven edges.")
    return " ".join(parts)
