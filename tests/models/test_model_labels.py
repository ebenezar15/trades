import numpy as np
import pandas as pd

from indexedge.backtest.simulate import simulate_exit
from indexedge.features.engine import FEATURE_COLUMNS, build_features
from indexedge.models.labels import CONTEXT_COLUMNS, default_feature_cols, label_signals


def test_dataset_shape_and_labels_come_from_simulator(labelled, candles):
    sigs, ds = labelled
    assert len(ds) > 100
    for c in ["ts", "strategy_id", "side", "win", "r_multiple", "strat_rsi_rule", *FEATURE_COLUMNS,
              *CONTEXT_COLUMNS]:
        assert c in ds.columns
    assert ds["ts"].is_monotonic_increasing
    assert ds[CONTEXT_COLUMNS].isna().all().all()             # missing context accepted
    by_ts = {s.ts: s for s in sigs}
    for _, row in ds.sample(15, random_state=0).iterrows():
        s = by_ts[row["ts"]]
        i = candles.index.get_loc(row["ts"])
        out = simulate_exit(s, candles.iloc[i + 1:], max_bars=24)
        assert row["win"] == int(out.outcome == "target")
        assert np.isclose(row["r_multiple"], out.r_multiple)
    fc = default_feature_cols(ds)
    assert "win" not in fc and "r_multiple" not in fc and "fwd_ret_pct" not in fc and "side" in fc


def test_truncation_features_unchanged_when_future_appended(labelled, candles):
    """Leakage test 3: features at a signal bar are identical when computed on data ending at that bar."""
    _, ds = labelled
    for _, row in ds.iloc[[5, len(ds) // 2, -3]].iterrows():
        i = candles.index.get_loc(row["ts"])
        trunc = build_features(candles.iloc[: i + 1]).iloc[-1]
        a = trunc[FEATURE_COLUMNS].astype(float).to_numpy()
        b = row[FEATURE_COLUMNS].astype(float).to_numpy()
        assert np.allclose(a, b, equal_nan=True, rtol=1e-7, atol=1e-9)


def test_context_asof_and_unknown_signals(candles, feat, rule):
    sigs = rule.scan(feat, "NIFTY")[:20]
    ctx = pd.DataFrame({"ctx_macro_bias": [0.3, -0.4], "ctx_vix": [13.0, 18.0]},
                       index=[candles.index[0], sigs[10].ts])
    ds = label_signals(sigs, candles, feat=feat, context=ctx)
    assert (ds.loc[ds["ts"] < sigs[10].ts, "ctx_macro_bias"] == 0.3).all()
    assert (ds.loc[ds["ts"] >= sigs[10].ts, "ctx_vix"] == 18.0).all()
    assert ds["ctx_event_risk"].isna().all()
    ds2 = label_signals(sigs[:3], candles, feat=feat, context={"macro_bias": 0.1, "event_risk": 0.5})
    assert (ds2["ctx_macro_bias"] == 0.1).all() and (ds2["ctx_event_risk"] == 0.5).all()

    bogus = sigs[0].__class__(**{**sigs[0].__dict__, "ts": candles.index[0] - pd.Timedelta(days=3)})
    assert len(label_signals([bogus], candles, feat=feat)) == 0
    assert label_signals([], candles).empty
