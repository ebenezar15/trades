import json

import numpy as np

from indexedge.models.drift import DriftMonitor
from indexedge.models.explain import contributions, top_contributions
from indexedge.models.registry import ModelRegistry
from indexedge.models.train import train_model

FEATS = [f"x{i}" for i in range(5)]


def test_lightgbm_contributions_sum_to_raw_logit(tabular):
    ds = tabular(800, seed=7)
    b = train_model(ds, FEATS)
    row = ds[FEATS].iloc[3]
    top = b.top_features(row, k=3)
    assert len(top) <= 3
    assert top[0][0] == "x0" or abs(row["x0"]) < 0.2
    contrib = np.asarray(b.model.predict(row.to_numpy(float).reshape(1, -1), pred_contrib=True))[0]
    raw = b.predict_raw(row)[0]
    assert np.isclose(1 / (1 + np.exp(-contrib.sum())), raw, atol=1e-6)
    assert set(contributions(b, row)) == set(FEATS)


def test_logistic_contributions_rank_informative_feature(tabular):
    ds = tabular(200, seed=8)
    b = train_model(ds, FEATS)
    assert b.kind == "logistic"
    top = top_contributions(b, {"x0": 3.0, "x1": 0.1, "x2": np.nan, "x3": 0.0, "x4": 0.0}, k=5)
    assert top[0][0] == "x0" and top[0][1] > 0


def test_registry_roundtrip(tabular, tmp_path):
    reg = ModelRegistry(tmp_path / "models")
    assert reg.get("orb15") is None
    b = train_model(tabular(300, seed=9), FEATS, strategy_id="orb15")
    reg.put("orb15", b)
    meta = json.loads((tmp_path / "models" / "orb15.json").read_text())
    assert meta["strategy_id"] == "orb15" and "reliability" in meta["metrics"]
    fresh = ModelRegistry(tmp_path / "models")
    got = fresh.get("orb15")
    assert got is not None and got.kind == b.kind
    assert fresh.ids() == ["orb15"] and fresh.metadata("orb15")["usable"] == b.usable
    (tmp_path / "models" / "bad.joblib").write_bytes(b"not a model")
    assert fresh.get("bad") is None
    fresh.delete("orb15")
    assert fresh.get("orb15") is None


def test_drift_monitor():
    m = DriftMonitor(window=30, expected_brier=0.20, sq_err_std=0.15)
    lo, hi = m.expected_band
    assert lo < 0.20 < hi
    for _ in range(10):
        m.update(0.7, 1)
    st = m.status()
    assert st["ok"] and "warming" in st["reason"]
    for _ in range(30):
        m.update(0.9, 0)                                        # confident and wrong
    st = m.status()
    assert not st["ok"] and st["live_brier"] > hi and st["n"] == 30
    good = DriftMonitor(window=30, expected_brier=0.20, sq_err_std=0.15)
    for i in range(30):
        good.update(0.6, i % 5 != 0)
    assert good.status()["ok"]
    assert DriftMonitor(10).status()["expected_band"] is None


def test_drift_from_bundle(tabular):
    b = train_model(tabular(600, seed=10), FEATS)
    m = DriftMonitor.from_bundle(b, window=20)
    assert m.expected_band is not None and m.expected_band[0] <= b.metrics["brier"] <= m.expected_band[1]
