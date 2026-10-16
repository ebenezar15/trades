"""Per-prediction feature contributions ("top SHAP features" on a signal) without a ``shap`` dependency.

* LightGBM: ``Booster.predict(..., pred_contrib=True)`` = exact TreeSHAP values in log-odds space.
* Logistic fallback: ``coef_j * standardised(x_j)`` (after the pipeline's median imputation) in log-odds space.

Contributions explain the *raw* model score; calibration is monotone so the ranking is unchanged.
"""
from __future__ import annotations

from typing import Any

import numpy as np


def contributions(bundle: Any, x_row: Any) -> dict[str, float]:
    """All feature contributions for one row (empty dict when the bundle has no model)."""
    if bundle is None or bundle.model is None or bundle.kind == "none":
        return {}
    X = bundle._frame(x_row).iloc[:1]
    arr = X.to_numpy(float)
    if bundle.kind == "lightgbm":
        vals = np.asarray(bundle.model.predict(arr, pred_contrib=True), float)[0, :-1]   # last = bias
    elif bundle.kind == "logistic":
        pipe = bundle.model
        z = pipe.named_steps["scale"].transform(pipe.named_steps["impute"].transform(arr))[0]
        vals = pipe.named_steps["clf"].coef_[0] * z
    else:
        return {}
    return {c: float(v) for c, v in zip(bundle.feature_cols, vals)}


def top_contributions(bundle: Any, x_row: Any, k: int = 5) -> list[tuple[str, float]]:
    """``k`` features with the largest |contribution|, as ``(feature, contribution)`` (log-odds units)."""
    c = contributions(bundle, x_row)
    ranked = sorted(((f, v) for f, v in c.items() if np.isfinite(v) and v != 0.0),
                    key=lambda t: abs(t[1]), reverse=True)
    return ranked[:k]
