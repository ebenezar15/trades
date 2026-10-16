"""Strategy report card: status + badges from metrics, using the thresholds in ``StrategySettings``.

* ``insufficient_data`` — fewer than ``min_trades_any`` trades (status ``insufficient``); nothing else is
  claimed.
* ``validated`` — PF > ``min_profit_factor``, n ≥ ``min_trades_validated``, expectancy > 0 and max drawdown
  ≤ ``max_drawdown_r``. The only badge eligible for live signals.
* ``high_win_rate`` — raw win rate ≥ ``high_win_rate_threshold``; always reported together with the
  reward:risk and expectancy, plus ``negative_expectancy_warning`` when expectancy ≤ 0.
* ``rejected`` — enough data but neither of the above.
"""
from __future__ import annotations

from typing import Any

from indexedge.config.settings import StrategySettings


def report_card(metrics: dict[str, Any], settings: StrategySettings | None = None) -> dict[str, Any]:
    s = settings or StrategySettings()
    n = int(metrics.get("n") or 0)
    wr = metrics.get("win_rate")
    exp_r = metrics.get("expectancy_r")
    pf = metrics.get("profit_factor_raw", metrics.get("profit_factor"))
    dd = metrics.get("max_dd_r") or 0.0
    summary = {
        "n": n, "win_rate": wr, "win_ci": metrics.get("win_ci"), "rr": metrics.get("avg_rr"),
        "expectancy_r": exp_r, "expectancy_rs": metrics.get("expectancy_rs"),
        "profit_factor": metrics.get("profit_factor"), "max_dd_r": dd,
    }
    warnings: list[str] = []
    if n < s.min_trades_any:
        return {"status": "insufficient", "badges": ["insufficient_data"], "summary": summary,
                "warnings": [f"only {n} trades (< {s.min_trades_any}): no statistics are claimed"]}
    badges: list[str] = []
    checks = {
        "profit_factor": pf is not None and pf > s.min_profit_factor,
        "trades": n >= s.min_trades_validated,
        "expectancy": exp_r is not None and exp_r > 0,
        "drawdown": dd <= s.max_drawdown_r,
    }
    validated = all(checks.values())
    high_wr = wr is not None and wr >= s.high_win_rate_threshold
    if validated:
        badges.append("validated")
    if high_wr:
        badges.append("high_win_rate")
        if exp_r is None or exp_r <= 0:
            badges.append("negative_expectancy_warning")
            warnings.append(f"win rate {wr:.0%} but expectancy {exp_r:+.2f}R: the losses outweigh the wins")
    if not validated:
        failed = [k for k, ok in checks.items() if not ok]
        warnings.append("not validated: " + ", ".join(
            {"profit_factor": f"profit factor ≤ {s.min_profit_factor}",
             "trades": f"fewer than {s.min_trades_validated} trades",
             "expectancy": "expectancy ≤ 0 after costs",
             "drawdown": f"max drawdown > {s.max_drawdown_r}R"}[k] for k in failed))
    if validated:
        status = "validated"
    elif high_wr:
        status = "high_win_rate"
    else:
        status = "rejected"
        badges.append("rejected")
    return {"status": status, "badges": badges, "summary": summary, "warnings": warnings, "checks": checks}
