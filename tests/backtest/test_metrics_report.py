import numpy as np
import pandas as pd
import pytest

from indexedge.backtest.metrics import compute_metrics, monte_carlo, wilson_ci
from indexedge.backtest.report import report_card
from indexedge.config.settings import StrategySettings


def _trades(rs, start="2025-12-29 10:00"):
    ts = pd.date_range(start, periods=len(rs), freq="D", tz="Asia/Kolkata")
    r = np.asarray(rs, float)
    return pd.DataFrame({"r": r, "pnl": r * 1000.0, "entry_ts": ts})


def test_wilson_ci_reference_values():
    lo, hi = wilson_ci(8, 10)
    assert lo == pytest.approx(0.4902, abs=1e-4) and hi == pytest.approx(0.9433, abs=1e-4)
    assert wilson_ci(0, 0) == (0.0, 1.0)


def test_compute_metrics_basic():
    m = compute_metrics(_trades([2, -1, -1, 2, -1]))
    assert m["n"] == 5 and m["wins"] == 2 and m["win_rate"] == pytest.approx(0.4)
    assert m["expectancy_r"] == pytest.approx(0.2) and m["expectancy_rs"] == pytest.approx(200)
    assert m["profit_factor"] == pytest.approx(4 / 3)
    assert m["avg_rr"] == pytest.approx(2.0)
    assert m["max_dd_r"] == pytest.approx(2.0)          # peak 2 -> 0
    assert set(m["by_year"]) == {"2025", "2026"}


def test_compute_metrics_empty_and_all_wins():
    e = compute_metrics(pd.DataFrame())
    assert e["n"] == 0 and e["win_rate"] is None and e["profit_factor"] is None
    m = compute_metrics(_trades([1, 1, 1]))
    assert m["profit_factor"] is None and m["profit_factor_raw"] == float("inf") and m["max_dd_r"] == 0


def test_monte_carlo_deterministic_and_ordered():
    r = [1.5, -1, -1, 2, -1, 0.5] * 10
    a, b = monte_carlo(r, n=500, seed=1), monte_carlo(r, n=500, seed=1)
    assert a == b
    assert 0 <= a["dd_p50"] <= a["dd_p95"] <= a["dd_p99"]
    assert a["final_p5"] <= a["final_p50"] <= a["final_p95"] and 0 <= a["prob_loss"] <= 1
    assert monte_carlo([], n=10)["n_sims"] == 0


def _metrics(n, win_rate, exp_r, pf, dd=5.0):
    return {"n": n, "win_rate": win_rate, "expectancy_r": exp_r, "profit_factor": pf, "max_dd_r": dd,
            "avg_rr": 1.2, "win_ci": [0, 1], "expectancy_rs": exp_r * 100}


def test_report_card_badges():
    s = StrategySettings()
    assert report_card(_metrics(30, 0.9, 0.5, 3.0), s)["status"] == "insufficient"
    assert report_card(_metrics(30, 0.9, 0.5, 3.0), s)["badges"] == ["insufficient_data"]
    v = report_card(_metrics(150, 0.55, 0.3, 1.6), s)
    assert v["status"] == "validated" and v["badges"] == ["validated"]
    hw = report_card(_metrics(80, 0.85, -0.1, 0.9), s)
    assert hw["status"] == "high_win_rate"
    assert hw["badges"] == ["high_win_rate", "negative_expectancy_warning"]
    assert hw["summary"]["rr"] == 1.2 and hw["summary"]["expectancy_r"] == -0.1
    both = report_card(_metrics(200, 0.82, 0.2, 1.5), s)
    assert both["badges"] == ["validated", "high_win_rate"] and both["status"] == "validated"
    rej = report_card(_metrics(150, 0.5, 0.3, 1.6, dd=30.0), s)
    assert rej["status"] == "rejected" and "rejected" in rej["badges"]
    assert any("drawdown" in w for w in rej["warnings"])
    assert report_card(_metrics(90, 0.5, 0.3, 1.6), s)["status"] == "rejected"   # n < 100
    strict = StrategySettings(min_profit_factor=2.0)
    assert report_card(_metrics(150, 0.55, 0.3, 1.6), strict)["status"] == "rejected"
