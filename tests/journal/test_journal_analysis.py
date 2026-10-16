from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from indexedge.core.clock import IST
from indexedge.journal.analysis import post_trade_analysis


def ist(h: int, mi: int = 0) -> datetime:
    return datetime(2026, 10, 15, h, mi, tzinfo=IST)


def row(**kw) -> dict:
    base = {
        "id": "t1",
        "strategy_id": "orb15",
        "instrument_symbol": "NIFTYFUT",
        "side": "BUY",
        "qty": 75,
        "entry_price": 25000.0,
        "planned_entry": 25000.0,
        "stop": 24950.0,
        "targets": [25100.0],
        "exit_price": 25100.0,
        "pnl": 7500.0,
        "costs": 60.0,
        "r_multiple": 2.0,
        "rule_violations": [],
        "reasons": ["Broke opening range high", "Above VWAP"],
    }
    base.update(kw)
    return base


def bars(highs, lows) -> pd.DataFrame:
    idx = pd.date_range(ist(10), periods=len(highs), freq="5min")
    return pd.DataFrame({"open": lows, "high": highs, "low": lows, "close": highs, "volume": 1.0}, index=idx)


def test_good_trade_good_result():
    a = post_trade_analysis(row(), bars([25020, 25060, 25110, 25105], [24990, 25010, 25040, 25080]))
    assert a["quadrant"] == "good_trade_good_result"
    assert a["good_trade"] and a["good_result"] and a["rules_followed"]
    assert a["mfe_points"] == 110 and a["mae_points"] == 10
    assert a["mfe_r"] == pytest.approx(2.2) and a["mae_r"] == pytest.approx(0.2)
    assert a["entry_quality"] == "A" and a["reached_target1"]
    assert a["plan_vs_actual"].startswith("exited at or beyond the first target")
    assert "Good trade, good result" in a["summary"] and "Broke opening range high" in a["summary"]
    assert a["capture_ratio"] == pytest.approx(100 / 110, rel=1e-3)


def test_good_trade_bad_result_stopped_at_plan():
    a = post_trade_analysis(
        row(exit_price=24950.0, pnl=-3750.0, r_multiple=-1.0), bars([25010, 25005], [24960, 24948])
    )
    assert a["quadrant"] == "good_trade_bad_result"
    assert a["plan_vs_actual"].startswith("stopped out at the planned stop")
    assert a["rule_violations"] == []


def test_chased_entry_is_bad_trade_boundary():
    # 5 pts on 50-pt risk = exactly 0.1R tolerance -> still within plan
    ok = post_trade_analysis(row(entry_price=25005.0))
    assert ok["entry_within_plan"] and ok["slippage_r"] == pytest.approx(0.1)
    bad = post_trade_analysis(row(entry_price=25006.0))
    assert not bad["entry_within_plan"]
    assert bad["quadrant"] == "bad_trade_good_result"
    assert any("chased entry" in v for v in bad["rule_violations"])


def test_stop_not_honoured_and_explicit_violation_bad_bad():
    a = post_trade_analysis(row(exit_price=24920.0, pnl=-6000.0, rule_violations=["moved stop"]))
    assert a["quadrant"] == "bad_trade_bad_result"
    assert "moved stop" in a["rule_violations"]
    assert any("stop not honoured" in v for v in a["rule_violations"])
    assert a["stop_overrun_r"] == pytest.approx(0.6)


def test_sell_side_and_fallbacks_without_bars():
    a = post_trade_analysis(
        row(
            side="SELL",
            entry_price=25000.0,
            planned_entry=None,
            stop=25050.0,
            targets=[24900.0],
            exit_price=24980.0,
            pnl=1500.0,
            mfe=40.0,
            mae=15.0,
        ),
        None,
    )
    assert a["mfe_points"] == 40 and a["mae_points"] == 15
    assert a["plan_vs_actual"] == "exited early in profit before the first target"
    assert a["volatility_change"].startswith("not enough bars")
    assert a["quadrant"] == "good_trade_good_result"


def test_missing_stop_is_violation_and_costs_can_flip_result():
    a = post_trade_analysis(row(stop=None, exit_price=25001.0, pnl=75.0, costs=80.0))
    assert "no protective stop defined" in a["rule_violations"]
    assert a["net_pnl"] == -5.0 and a["quadrant"] == "bad_trade_bad_result"
    assert a["entry_quality"] == "n/a"


def test_volatility_expansion_detected():
    a = post_trade_analysis(row(), bars([25005, 25005, 25100, 25120], [25000, 25000, 25000, 24990]))
    assert "expanded" in a["volatility_change"]


def test_accepts_series_row():
    a = post_trade_analysis(pd.Series(row()), None)
    assert a["trade_id"] == "t1"
