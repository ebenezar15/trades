"""Template-based post-trade analysis (no LLM).

``post_trade_analysis(trade_row, bars_during)`` returns structured fields plus a readable ``summary``
paragraph: entry quality, MFE/MAE, plan vs actual, slippage, rule violations, volatility change and the
outcome quadrant.

Quadrant definitions
--------------------
* **good trade** = no rule violations AND entry within ``entry_tolerance_r`` (default 0.1R) of the planned
  entry.
* **good result** = net P&L (pnl - costs) > 0.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import pandas as pd

QUADRANT_LABELS = {
    "good_trade_good_result": "Good trade, good result — process and outcome aligned. Repeat this.",
    "good_trade_bad_result": "Good trade, bad result — the plan was followed; losses like this are the "
    "cost of doing business. Do not change the rules because of one outcome.",
    "bad_trade_good_result": "Bad trade, good result — it made money despite breaking the plan. Dangerous: "
    "luck reinforces bad habits.",
    "bad_trade_bad_result": "Bad trade, bad result — the plan was not followed and it cost money. Review the "
    "violations below.",
}


def _num(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _list(v: Any) -> list:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return []
    return list(v) if isinstance(v, (list, tuple)) else [v]


def _excursions(side_sign: int, entry: float, bars: pd.DataFrame | None, row: Mapping) -> tuple[float, float]:
    if bars is not None and not bars.empty and {"high", "low"} <= set(bars.columns):
        hi, lo = float(bars["high"].max()), float(bars["low"].min())
        if side_sign > 0:
            return max(0.0, hi - entry), max(0.0, entry - lo)
        return max(0.0, entry - lo), max(0.0, hi - entry)
    return _num(row.get("mfe")) or 0.0, _num(row.get("mae")) or 0.0


def _volatility_note(bars: pd.DataFrame | None) -> tuple[str, float | None]:
    if bars is None or len(bars) < 4 or not {"high", "low"} <= set(bars.columns):
        return "not enough bars to judge volatility", None
    rng = (bars["high"] - bars["low"]).astype(float)
    half = len(rng) // 2
    first, second = rng.iloc[:half].mean(), rng.iloc[half:].mean()
    if first <= 0:
        return "volatility could not be measured", None
    ratio = float(second / first)
    if ratio >= 1.5:
        return f"volatility expanded during the trade (bar range x{ratio:.1f})", ratio
    if ratio <= 0.67:
        return f"volatility contracted during the trade (bar range x{ratio:.1f})", ratio
    return "volatility was roughly stable", ratio


def post_trade_analysis(
    trade_row: Mapping[str, Any],
    bars_during: pd.DataFrame | None = None,
    entry_tolerance_r: float = 0.1,
    stop_tolerance_r: float = 0.2,
) -> dict[str, Any]:
    """Analyse a closed trade. ``trade_row`` is a journal trades row (dict or Series)."""
    row = dict(trade_row)
    sign = 1 if str(row.get("side", "BUY")).upper() == "BUY" else -1
    entry = _num(row.get("entry_price")) or 0.0
    planned = _num(row.get("planned_entry")) or entry
    stop = _num(row.get("stop"))
    exit_price = _num(row.get("exit_price"))
    targets = [t for t in (_num(x) for x in _list(row.get("targets"))) if t is not None]
    pnl = _num(row.get("pnl")) or 0.0
    costs = _num(row.get("costs")) or 0.0
    net = pnl - costs
    qty = int(_num(row.get("qty")) or 0)

    risk = abs(planned - stop) if stop is not None else None
    risk_ok = bool(risk and risk > 0)
    slip_pts = (entry - planned) * sign  # positive = paid worse than planned
    slip_r = slip_pts / risk if risk_ok else None
    entry_ok = slip_r is None or slip_r <= entry_tolerance_r
    mfe, mae = _excursions(sign, entry, bars_during, row)
    mfe_r = mfe / risk if risk_ok else None
    mae_r = mae / risk if risk_ok else None
    realised_pts = (exit_price - entry) * sign if exit_price is not None else None
    capture = realised_pts / mfe if (realised_pts is not None and mfe > 0) else None

    violations = [str(v) for v in _list(row.get("rule_violations"))]
    if stop is None:
        violations.append("no protective stop defined")
    if not entry_ok:
        violations.append(f"chased entry: filled {slip_pts:.2f} pts ({slip_r:.2f}R) worse than plan")
    stop_overrun = None
    if stop is not None and exit_price is not None and risk_ok:
        stop_overrun = (stop - exit_price) * sign / risk  # >0 = exited beyond the stop
        if stop_overrun > stop_tolerance_r:
            violations.append(f"stop not honoured: exited {stop_overrun:.2f}R beyond the stop")
    violations = list(dict.fromkeys(violations))

    # plan vs actual
    t1 = targets[0] if targets else None
    reached_t1 = t1 is not None and mfe >= (t1 - entry) * sign
    if exit_price is None:
        plan_vs_actual = "trade still open"
    elif t1 is not None and (exit_price - t1) * sign >= 0:
        plan_vs_actual = "exited at or beyond the first target, as planned"
    elif stop is not None and (stop - exit_price) * sign >= 0:
        plan_vs_actual = "stopped out at the planned stop" + (
            " with extra slippage" if stop_overrun and stop_overrun > 0.05 else ""
        )
    elif realised_pts is not None and realised_pts > 0:
        plan_vs_actual = "exited early in profit before the first target"
    else:
        plan_vs_actual = "exited early at a loss before the stop"

    # entry quality grade
    if slip_r is None or mae_r is None:
        grade = "n/a"
    elif slip_r <= 0.05 and mae_r < 0.5:
        grade = "A"
    elif slip_r <= entry_tolerance_r and mae_r < 1.0:
        grade = "B"
    else:
        grade = "C"

    good_trade = not violations and entry_ok
    good_result = net > 0
    quadrant = f"{'good' if good_trade else 'bad'}_trade_{'good' if good_result else 'bad'}_result"
    vol_note, vol_ratio = _volatility_note(bars_during)
    reasons = [str(r) for r in _list(row.get("reasons"))]

    def r_txt(x: float | None) -> str:
        return f"{x:.2f}R" if x is not None else "n/a"

    parts = [
        (
            f"{row.get('side', '')} {qty} {row.get('instrument_symbol', '')} "
            f"via {row.get('strategy_id', '?')}: "
            f"entry {entry:.2f} (plan {planned:.2f}, slippage {slip_pts:+.2f} pts / {r_txt(slip_r)}), "
            f"entry quality {grade}."
        ),
        (
            f"Price moved up to {mfe:.2f} pts in favour (MFE {r_txt(mfe_r)}) and {mae:.2f} pts against "
            f"(MAE {r_txt(mae_r)}); {vol_note}."
        ),
        f"Plan vs actual: {plan_vs_actual}"
        + (f"; captured {capture:.0%} of the best move" if capture is not None else "")
        + ".",
        f"Net result ₹{net:,.2f} after ₹{costs:,.2f} costs"
        + (f" ({r_txt(_num(row.get('r_multiple')))})" if _num(row.get("r_multiple")) is not None else "")
        + ".",
    ]
    if reasons:
        parts.append("Entry conditions: " + "; ".join(reasons) + ".")
    parts.append("Rule violations: " + ("; ".join(violations) if violations else "none") + ".")
    parts.append(QUADRANT_LABELS[quadrant])

    return {
        "trade_id": row.get("id"),
        "entry_quality": grade,
        "slippage_points": round(slip_pts, 4),
        "slippage_r": None if slip_r is None else round(slip_r, 4),
        "entry_within_plan": entry_ok,
        "mfe_points": round(mfe, 4),
        "mae_points": round(mae, 4),
        "mfe_r": None if mfe_r is None else round(mfe_r, 4),
        "mae_r": None if mae_r is None else round(mae_r, 4),
        "reached_target1": bool(reached_t1),
        "capture_ratio": None if capture is None else round(capture, 4),
        "plan_vs_actual": plan_vs_actual,
        "stop_overrun_r": None if stop_overrun is None else round(stop_overrun, 4),
        "volatility_change": vol_note,
        "volatility_ratio": vol_ratio,
        "rule_violations": violations,
        "rules_followed": not violations,
        "net_pnl": net,
        "good_trade": good_trade,
        "good_result": good_result,
        "quadrant": quadrant,
        "quadrant_label": QUADRANT_LABELS[quadrant],
        "conditions": reasons,
        "summary": " ".join(parts),
    }
