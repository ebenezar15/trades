import numpy as np

from indexedge.models.calibration import (
    brier,
    brier_skill,
    bucket_win_rates,
    fit_calibrator,
    reliability_bins,
    wilson_ci,
)


def test_wilson_ci_known_values():
    lo, hi = wilson_ci(50, 100)
    assert abs(lo - 0.4038) < 1e-3 and abs(hi - 0.5962) < 1e-3
    assert wilson_ci(0, 0) == (0.0, 1.0)
    lo, hi = wilson_ci(0, 10)
    assert lo == 0.0 and 0 < hi < 0.35


def test_brier_and_skill():
    y = np.array([1, 0, 1, 0])
    assert brier(y.astype(float), y) == 0.0
    assert abs(brier_skill(np.full(4, 0.5), y)) < 1e-12        # base-rate forecast has zero skill
    assert brier_skill(y.astype(float), y) == 1.0


def test_reliability_bins_cover_all_points():
    rng = np.random.default_rng(0)
    p = rng.random(500)
    y = (rng.random(500) < p).astype(int)
    bins = reliability_bins(p, y, 10)
    assert sum(b["n"] for b in bins) == 500
    assert all(abs(b["p_pred"] - b["p_obs"]) < 0.2 for b in bins)
    for b in bucket_win_rates(p, y, 5):
        assert b["ci_low"] <= b["win_rate"] <= b["ci_high"]


def test_calibrator_kind_and_improvement():
    rng = np.random.default_rng(1)
    true_p = rng.random(1000)
    y = (rng.random(1000) < true_p).astype(int)
    raw = true_p ** 3                                           # badly miscalibrated but monotone
    iso = fit_calibrator(raw, y)
    assert iso.kind == "isotonic"
    assert brier(iso.predict(raw), y) < brier(raw, y)
    platt = fit_calibrator(raw[:150], y[:150])
    assert platt.kind == "platt"
    out = platt.predict(raw)
    assert out.min() >= 0 and out.max() <= 1
    assert fit_calibrator(raw[:10], np.ones(10)).kind == "constant"
