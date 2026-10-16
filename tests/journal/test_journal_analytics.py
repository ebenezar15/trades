from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from indexedge.core.clock import IST
from indexedge.journal.analytics import (
    best_and_worst,
    discipline_score,
    monthly_summaries,
    performance,
    weekly_summaries,
)


def ist(d: int, h: int = 10, mi: int = 0, mo: int = 10) -> datetime:
    return datetime(2026, mo, d, h, mi, tzinfo=IST)


def tr(
    i: int,
    entry_ts: datetime,
    pnl: float,
    strategy: str = "orb15",
    qty: int = 75,
    exit_min: int = 30,
    regime: str = "trend_up",
    **kw,
) -> dict:
    sign = 1 if (pnl or 0) >= 0 else -1
    base = {
        "id": f"t{i}",
        "strategy_id": strategy,
        "underlying": "NIFTY",
        "side": "BUY",
        "qty": qty,
        "entry_ts": entry_ts,
        "entry_price": 25000.0,
        "planned_entry": 25000.0,
        "stop": 24950.0,
        "exit_ts": entry_ts + pd.Timedelta(minutes=exit_min),
        "exit_price": 25000.0 + sign * 50,
        "pnl": pnl,
        "costs": 0.0,
        "r_multiple": None if pnl is None else pnl / 3750.0,
        "status": "closed",
        "mode": "paper",
        "regime": regime,
        "rule_violations": [],
    }
    base.update(kw)
    return base


@pytest.fixture
def trades() -> pd.DataFrame:
    rows = [
        tr(1, ist(12, 10), 3750.0),
        tr(2, ist(12, 11), -3750.0, strategy="gap_fill", regime="range"),
        tr(3, ist(13, 10), 7500.0),
        tr(4, ist(14, 14), -3750.0, strategy="gap_fill", regime="range"),
        tr(5, ist(20, 10), 3750.0),
        tr(6, ist(20, 11), None, status="open"),
    ]
    return pd.DataFrame(rows)


def test_performance_core_metrics(trades):
    p = performance(trades, capital=200_000)
    assert p["n"] == 5 and p["wins"] == 3 and p["losses"] == 2
    assert p["win_rate"] == pytest.approx(0.6)
    assert p["net_pnl"] == pytest.approx(7500.0)
    assert p["profit_factor"] == pytest.approx(15000 / 7500)
    assert p["expectancy"] == pytest.approx(1500.0)
    assert p["avg_r"] == pytest.approx(0.4)
    assert p["insufficient_data"] is True
    assert [e["value"] for e in p["equity"]] == [203750, 200000, 207500, 203750, 207500]
    assert p["max_drawdown"] == pytest.approx(3750.0)
    assert p["max_drawdown_pct"] == pytest.approx(1.875)
    assert p["by_strategy"]["orb15"]["win_rate"] == 1.0
    assert p["by_strategy"]["gap_fill"]["net_pnl"] == -7500.0
    assert set(p["by_hour"]) == {"10:00", "11:00", "14:00"}
    assert p["by_weekday"]["Monday"]["n"] == 2
    assert p["by_regime"]["range"]["n"] == 2
    assert p["plan_vs_actual"]["avg_slippage_points"] == 0.0
    assert isinstance(p["equity"][0]["time"], int)


def test_performance_empty_and_no_losses():
    p = performance(pd.DataFrame())
    assert p["n"] == 0 and p["equity"] == [] and p["by_strategy"] == {}
    p2 = performance(pd.DataFrame([tr(1, ist(12), 100.0)]), min_trades=1)
    assert p2["profit_factor"] is None and p2["insufficient_data"] is False


def test_discipline_perfect_and_violations(trades):
    d = discipline_score(trades)
    assert d["score"] == 100.0 and d["issues"] == []
    bad = pd.concat(
        [
            trades,
            pd.DataFrame(
                [
                    # revenge: enters 5 min after t2's losing exit (11:30) with double size
                    tr(7, ist(12, 11, 35), 500.0, qty=150),
                    tr(8, ist(13, 12), 500.0, stop=None),
                    tr(9, ist(13, 13), 500.0, entry_price=25020.0, rule_violations=["moved stop"]),
                ]
            ),
        ],
        ignore_index=True,
    )
    d2 = discipline_score(bad)
    assert d2["score"] < 100
    assert d2["components"]["no_revenge_trading"] < 1
    assert d2["components"]["stop_defined"] < 1
    assert d2["components"]["entry_within_plan"] < 1
    assert d2["components"]["no_rule_violations"] < 1
    assert any("revenge" in i for i in d2["issues"])
    assert discipline_score(pd.DataFrame())["score"] is None


def test_discipline_overtrading():
    rows = [tr(i, ist(12, 10, i * 5), 100.0, exit_min=2) for i in range(7)]
    d = discipline_score(pd.DataFrame(rows), max_trades_per_day=5)
    assert d["components"]["no_overtrading"] == pytest.approx(5 / 7, rel=1e-3)


def test_best_and_worst_and_summaries(trades):
    bw = best_and_worst(trades, min_n=2)
    assert bw["strategy"]["best"] == "orb15" and bw["strategy"]["worst"] == "gap_fill"
    weeks = weekly_summaries(trades, min_n=2)
    assert [w["period"] for w in weeks] == ["2026-W42", "2026-W43"]
    assert weeks[0]["n"] == 4 and "What worked best for you" in weeks[0]["text"]
    assert "strategy orb15" in weeks[0]["text"]
    assert "Too few trades" in weeks[1]["text"]
    months = monthly_summaries(trades)
    assert len(months) == 1 and months[0]["period"] == "2026-10" and months[0]["n"] == 5
    assert "Discipline score" in months[0]["text"]
    with pytest.raises(ValueError):
        from indexedge.journal.analytics import period_summaries

        period_summaries(trades, "D")
    assert weekly_summaries(pd.DataFrame()) == []


def test_works_on_journal_frame(tmp_path):
    from indexedge.journal.db import Journal
    from indexedge.journal.models import TradeRecord

    j = Journal(":memory:")
    t = TradeRecord("s", "orb15", "NIFTY", "X", "BUY", 75, ist(12), 25000.0, 24950.0, [25100.0])
    j.open_trade(t)
    j.close_trade(t.id, ist(12, 11), 25100.0, "target", 7500.0, 50.0)
    p = performance(j.trades())
    assert p["net_pnl"] == 7450.0 and p["by_regime"]["unknown"]["n"] == 1
    assert discipline_score(j.trades())["score"] == 100.0
