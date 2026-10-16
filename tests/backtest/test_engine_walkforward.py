import json

import numpy as np
import pandas as pd
import pytest

from indexedge.backtest.costs import CostModel
from indexedge.backtest.engine import TRADE_COLUMNS, backtest_signals, run_backtest
from indexedge.backtest.walkforward import walk_forward
from indexedge.strategies import get_strategy
from indexedge.strategies.straddle import PremiumOutcome


def test_run_backtest_futures_pnl_and_costs(bt_feat):
    strat = get_strategy("triple_supertrend")
    res = run_backtest(strat, bt_feat, "NIFTY", lot_size=65)
    t = res.trades
    assert list(t.columns) == TRADE_COLUMNS and len(t) > 10
    assert res.metrics["n"] == len(t)
    np.testing.assert_allclose(t["pnl_gross"], t["points"] * 65)
    np.testing.assert_allclose(t["pnl"], t["pnl_gross"] - t["costs"])
    np.testing.assert_allclose(t["r"], t["pnl"] / (t["risk_points"] * 65))
    assert (t["costs"] > 0).all() and (t["kind"] == "future").all()
    assert (t["win"] == (t["pnl"] > 0)).all()
    # one position at a time: each entry is after the previous exit, entries after the signal bar
    assert (t["entry_ts"] > t["signal_ts"]).all()
    assert (t["signal_ts"].iloc[1:].to_numpy() >= t["exit_ts"].iloc[:-1].to_numpy()).all()
    assert res.skipped_overlap + len(t) <= len(res.signals)
    # costs make the net result worse than gross
    assert t["pnl"].sum() < t["pnl_gross"].sum()


def test_raw_candles_accepted_and_zero_cost_model(bt_raw):
    free = CostModel(brokerage_per_order=0, stt_future_sell=0, stt_option_sell=0, txn_future=0, txn_option=0,
                     sebi_per_crore=0, stamp_future_buy=0, stamp_option_buy=0, tick=0,
                     slippage_future_pct=0, slippage_option_pct=0)
    res = run_backtest(get_strategy("inside_bar_breakout"), bt_raw, "NIFTY", 65, free)
    t = res.trades
    assert len(t) > 0
    np.testing.assert_allclose(t["r"], t["r_gross"], atol=1e-9)


def test_straddle_backtest_uses_premium_model(bt_feat):
    strat = get_strategy("straddle_920")
    sigs = strat.scan(bt_feat, "NIFTY")
    out = strat.simulate(sigs[0], bt_feat.iloc[bt_feat.index.get_loc(pd.Timestamp(sigs[0].ts)) + 1:])
    assert isinstance(out, PremiumOutcome) and len(out.legs) == 2 and out.risk_points > 0
    assert out.outcome in {"stop", "time", "eod"}
    trades, _ = backtest_signals(strat, sigs, bt_feat, 65)
    assert (trades["kind"] == "option").all()
    assert trades["exit_ts"].map(lambda t: t.strftime("%H:%M") <= "15:15").all()
    # stopped trades lose roughly the per-leg stop budget, never a huge multiple of it
    assert trades["r_gross"].min() > -2.5
    ex = get_strategy("expiry_straddle")
    t2, _ = backtest_signals(ex, ex.scan(bt_feat, "NIFTY"), bt_feat, 65)
    assert len(t2) > 5 and (t2["entry_ts"].map(lambda t: t.weekday()) == 1).all()
    assert len(t2) and t2["r_gross"].max() <= 1.0 / 0.25 + 1e-9     # profit capped by the credit


def test_empty_backtest():
    res = run_backtest(get_strategy("orb15"), pd.DataFrame(), "NIFTY", 65)
    assert res.trades.empty and res.metrics["n"] == 0


def test_walk_forward_fixed_and_grid(bt_feat):
    wf = walk_forward(get_strategy("cpr_pivot"), bt_feat, "NIFTY", 65, train_days=40, test_days=20)
    assert len(wf["folds"]) == 4                                 # 120 sessions: (120-40)/20
    assert all(f["params"] == {} for f in wf["folds"])
    assert wf["oos_metrics"]["n"] == sum(f["oos_n"] for f in wf["folds"])
    assert any("insufficient sample" in w for w in wf["warnings"])
    json.dumps(wf, default=str)
    g = walk_forward(get_strategy("ema_supertrend_adx"), bt_feat, "NIFTY", 65, train_days=40, test_days=20)
    assert g["param_grid"] == {"adx_min": [20.0, 22.0, 25.0]}
    assert all(set(f["params"]) <= {"adx_min"} for f in g["folds"])
    short = walk_forward(get_strategy("orb15"), bt_feat.iloc[:500], "NIFTY", 65, train_days=40, test_days=20)
    assert short["folds"] == [] and any("not enough history" in w for w in short["warnings"])


def test_walk_forward_flags_overfitting(bt_feat, monkeypatch):
    import indexedge.backtest.walkforward as wfm

    real = wfm.backtest_signals

    def rigged(strategy, signals, feat, *a, **k):
        trades, skipped = real(strategy, signals, feat, *a, **k)
        days = sorted(set(feat.index.date))
        first = set(days[:40])
        trades["r"] = np.where(trades["entry_ts"].map(lambda t: t.date() in first), 1.0, -0.5)
        return trades, skipped

    monkeypatch.setattr(wfm, "backtest_signals", rigged)
    wf = wfm.walk_forward(get_strategy("triple_supertrend"), bt_feat, "NIFTY", 65, train_days=40, test_days=20)
    assert any("overfitting" in w for w in wf["warnings"])


@pytest.mark.parametrize("sid", ["orb15", "gap_fill"])
def test_trades_do_not_cross_sessions(sid, bt_feat):
    t = run_backtest(get_strategy(sid), bt_feat, "NIFTY", 65).trades
    assert (t["entry_ts"].map(lambda x: x.date()) == t["exit_ts"].map(lambda x: x.date())).all()
