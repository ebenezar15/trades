import json
from datetime import date

import numpy as np
import pytest

from indexedge.backtest.replay import ReplaySession
from indexedge.config.settings import Settings
from indexedge.data.store import CandleStore
from indexedge.data.synthetic import generate_intraday
from indexedge.services.lab import SYNTHETIC_DISCLAIMER, StrategyLab
from indexedge.strategies import all_strategies


def test_replay_steps_reveal_signals_in_order(bt_raw):
    df = bt_raw.iloc[: 75 * 15]
    rs = ReplaySession(df, all_strategies())
    assert rs.index == -1 and rs.visible_signals() == [] and len(rs.candles()) == 0
    seen = []
    while not rs.done:
        got = rs.step()
        assert all(s.ts == rs.current_ts for s in got)
        seen.extend(got)
    assert len(seen) == len(rs.visible_signals()) > 0
    rs.seek(200)
    assert len(rs.candles()) == 201 and all(k <= 200 for k, _ in rs.visible_signals())
    d = rs.days()[3]
    a, b = rs.day_range(d)
    assert rs.feat.index[a].date() == d and rs.feat.index[b - 1].date() == d


def test_quiz_point(bt_raw):
    rs = ReplaySession(bt_raw.iloc[: 75 * 15], list(all_strategies().values()))
    k, sig = rs.quiz_point(np.random.default_rng(3))
    assert rs.feat.index[k] == sig.ts and sig.instrument_hint != "straddle"
    assert rs.outcome(sig).outcome in {"target", "stop"}
    assert rs.quiz_point(3) == rs.quiz_point(3)


def test_lab_synthetic_backtest_is_labelled():
    lab = StrategyLab(Settings())
    res = lab.backtest("triple_supertrend", "NIFTY", days=60, mc_sims=100)
    json.dumps(res)                                              # JSON-safe for the API
    assert res["data_source"] == "synthetic" and res["real_performance"] is False
    assert res["disclaimer"] == SYNTHETIC_DISCLAIMER and SYNTHETIC_DISCLAIMER in res["report"]["warnings"]
    for key in ("metrics", "report", "trades", "equity", "monte_carlo", "walk_forward"):
        assert key in res
    assert len(res["trades"]) == res["metrics"]["n"]
    if res["equity"]:
        assert res["equity"][-1]["value"] == pytest.approx(res["metrics"]["total_pnl"], abs=0.05)
        assert isinstance(res["equity"][0]["time"], int)
    assert res["report"]["status"] in {"validated", "high_win_rate", "insufficient", "rejected"}


def test_lab_paper_only_strategies_flagged():
    lab = StrategyLab(Settings())
    res = lab.backtest("oi_pcr", "NIFTY", days=30, mc_sims=10)
    assert res["metrics"]["n"] == 0 and res["report"]["status"] == "insufficient"
    assert any("OI" in w for w in res["report"]["warnings"])
    st = lab.backtest("straddle_920", "NIFTY", days=30, mc_sims=10)
    assert st["backtest_quality"] == "approximate"
    assert any("approximate" in w for w in st["report"]["warnings"])


def test_lab_prefers_stored_candles(tmp_path):
    store = CandleStore(tmp_path)
    store.save("NIFTY", "5m", generate_intraday("NIFTY", days=40, seed=9, start=date(2026, 2, 2)))
    lab = StrategyLab(Settings(), store=store)
    res = lab.backtest("cpr_pivot", "NIFTY", days=30, mc_sims=10)
    assert res["data_source"] == "broker" and res["real_performance"] is True and res["days"] == 30
    assert SYNTHETIC_DISCLAIMER not in res["report"]["warnings"]
    # too little stored history -> synthetic fallback
    lab2 = StrategyLab(Settings(), data_provider=lambda u, d: generate_intraday(u, days=5))
    assert lab2.data_source("NIFTY", 30) == "synthetic"


def test_lab_report_all_and_default():
    lab = StrategyLab(Settings())
    reps = lab.report_all("BANKNIFTY", days=30)
    assert len(reps) == len(all_strategies()) and all("status" in r and "metrics" in r for r in reps.values())
    assert isinstance(StrategyLab.default(), StrategyLab)
