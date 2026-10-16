from datetime import timedelta

import numpy as np
import pandas as pd

from indexedge.models.train import (
    ModelBundle,
    fallback_stats,
    leakage_check,
    purged_walk_forward_splits,
    train_model,
)

FEATS = [f"x{i}" for i in range(5)]
METRIC_KEYS = {"n", "base_rate", "auc", "brier", "brier_skill", "reliability", "buckets", "n_oos", "usable"}


def test_learnable_lightgbm_is_usable_and_calibrated(tabular):
    ds = tabular(1200, seed=0)
    b = train_model(ds, FEATS, embargo_bars=24)
    assert b.kind == "lightgbm"
    assert METRIC_KEYS <= set(b.metrics)
    assert b.metrics["auc"] > 0.7
    assert b.metrics["brier_skill"] > 0
    assert b.metrics["calibration"] == "isotonic"
    assert b.usable
    p = b.predict_proba(ds[FEATS])
    assert p.shape == (len(ds),) and p.min() >= 0 and p.max() <= 1
    # calibration: predicted bucket means close to observed in OOS reliability bins with decent n
    for rb in b.metrics["reliability"]:
        if rb["n"] >= 80:
            assert abs(rb["p_pred"] - rb["p_obs"]) < 0.15
    hi = b.predict_proba({"x0": 2.5, "x1": 0, "x2": np.nan, "x3": 0, "x4": 0})[0]
    lo = b.predict_proba(pd.Series({"x0": -2.5, "x1": 0, "x2": 0, "x3": 0, "x4": 0}))[0]
    assert hi > 0.7
    assert lo < 0.3


def test_small_sample_uses_logistic_with_platt_and_handles_nan(tabular):
    ds = tabular(220, seed=1, nan_frac=0.3)
    ds["x4"] = np.nan                                           # warm-up style all-NaN column
    b = train_model(ds, FEATS)
    assert b.kind == "logistic"
    assert b.metrics["calibration"] == "platt"                 # n_oos < 200
    assert np.isfinite(b.predict_proba(ds[FEATS])).all()


def test_insufficient_data_is_unusable_with_fallback(tabular):
    ds = tabular(40, seed=2)
    b = train_model(ds, FEATS, min_samples=50)
    assert not b.usable and b.kind == "none"
    assert "insufficient" in b.metrics["reason"]
    assert np.allclose(b.predict_proba(ds[FEATS]), ds["win"].mean())
    fb = fallback_stats(ds, min_samples=50)
    assert fb["win_rate"] is None and fb["n"] == 40
    fb = fallback_stats(tabular(300, seed=2))
    lo, hi = fb["ci"]
    assert lo < fb["win_rate"] < hi and fb["n"] == 300


def test_label_shuffle_gives_chance_auc(tabular):
    """Leakage test 1: with shuffled labels the OOS AUC must be ~0.5 and the model unusable."""
    ds = tabular(1200, seed=3)
    rng = np.random.default_rng(0)
    ds["win"] = rng.permutation(ds["win"].to_numpy())
    ds = ds.drop(columns=["r_multiple", "fwd_ret_pct"])
    b = train_model(ds, FEATS)
    assert abs(b.metrics["auc"] - 0.5) < 0.07
    assert not b.usable


def test_label_shuffle_on_real_pipeline(labelled):
    _, ds = labelled
    ds = ds.copy()
    ds["win"] = np.random.default_rng(1).permutation(ds["win"].to_numpy())
    ds = ds.drop(columns=["r_multiple", "fwd_ret_pct"])
    b = train_model(ds)
    assert abs(b.metrics["auc"] - 0.5) < 0.12
    assert not b.usable


def test_leaked_future_feature_is_detected(tabular, labelled):
    """Leakage test 2: a feature built from the future return is flagged and makes the model unusable."""
    ds = tabular(400, seed=4)
    ds["leak"] = ds["fwd_ret_pct"] + np.random.default_rng(0).normal(0, 0.01, len(ds))
    flags = leakage_check(ds, FEATS + ["leak"])
    assert {f["feature"] for f in flags} == {"leak"}
    b = train_model(ds, FEATS + ["leak"])
    assert not b.usable and "leak" in b.metrics["reason"]

    _, real = labelled
    assert leakage_check(real) == []                           # genuine causal features pass
    real = real.copy()
    real["dist_vwap_atr"] = real["r_multiple"] * 2.0           # inject leak into a real feature column
    assert any(f["feature"] == "dist_vwap_atr" for f in leakage_check(real))


def test_purged_splits_are_time_ordered_and_purged(tabular):
    ds = tabular(300, seed=5)
    ds["exit_ts"] = ds["ts"] + pd.Timedelta(hours=10)          # labels overlap next rows
    emb = timedelta(hours=2)
    splits = purged_walk_forward_splits(ds["ts"], ds["exit_ts"], 5, emb)
    assert len(splits) == 5
    for tr, te in splits:
        t0 = ds["ts"].iloc[te[0]]
        assert tr.max() < te.min()
        assert (ds["exit_ts"].iloc[tr] < t0).all()
        assert (ds["ts"].iloc[tr] < t0 - emb).all()
    shuffled = ds.sample(frac=1, random_state=0)
    try:
        purged_walk_forward_splits(shuffled["ts"], shuffled["exit_ts"])
    except ValueError:
        pass
    else:
        raise AssertionError("unsorted input must be rejected")


def test_deterministic_and_save_load(tabular, tmp_path):
    ds = tabular(600, seed=6)
    a = train_model(ds, FEATS)
    b = train_model(ds, FEATS)
    assert np.allclose(a.predict_proba(ds[FEATS]), b.predict_proba(ds[FEATS]))
    path = a.save(tmp_path / "m.joblib")
    c = ModelBundle.load(path)
    assert np.allclose(a.predict_proba(ds[FEATS]), c.predict_proba(ds[FEATS]))
    assert c.metrics["auc"] == a.metrics["auc"]
