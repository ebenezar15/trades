"""Probability calibration and calibration diagnostics.

* ``fit_calibrator(p, y)`` maps raw model scores to calibrated probabilities: isotonic regression when there are
  at least ``ISOTONIC_MIN`` out-of-sample points, otherwise Platt scaling (logistic on the logit of the score),
  which is far less prone to over-fitting small samples.
* ``reliability_bins`` / ``bucket_win_rates`` back the reliability diagram in the Strategy Lab.
* ``brier``, ``brier_skill`` and ``wilson_ci`` are the honesty metrics shown next to every probability.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

ISOTONIC_MIN = 200
_EPS = 1e-6


def wilson_ci(wins: float, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion. ``(0.0, 1.0)`` when ``n == 0``."""
    if n <= 0:
        return (0.0, 1.0)
    p = wins / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def brier(p: np.ndarray, y: np.ndarray) -> float:
    p, y = np.asarray(p, float), np.asarray(y, float)
    if p.size == 0:
        return float("nan")
    return float(np.mean((p - y) ** 2))


def brier_skill(p: np.ndarray, y: np.ndarray, base_rate: float | None = None) -> float:
    """1 - Brier / Brier(constant base-rate forecast). > 0 means better than just quoting the win rate."""
    y = np.asarray(y, float)
    if y.size == 0:
        return float("nan")
    b = float(y.mean()) if base_rate is None else float(base_rate)
    ref = float(np.mean((b - y) ** 2))
    if ref <= 0:
        return float("nan")
    return 1.0 - brier(p, y) / ref


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(p, float), _EPS, 1 - _EPS)
    return np.log(p / (1 - p))


@dataclass
class Calibrator:
    """Monotone map raw score -> calibrated probability. ``kind`` in {isotonic, platt, identity, constant}."""

    kind: str
    model: Any = None
    constant: float | None = None
    params: dict[str, float] = field(default_factory=dict)

    def predict(self, p: np.ndarray) -> np.ndarray:
        p = np.asarray(p, float).ravel()
        if self.kind == "constant":
            return np.full(p.shape, float(self.constant if self.constant is not None else 0.5))
        if self.kind == "isotonic":
            out = self.model.predict(p)
        elif self.kind == "platt":
            out = self.model.predict_proba(_logit(p).reshape(-1, 1))[:, 1]
        else:
            out = p
        return np.clip(np.nan_to_num(out, nan=0.5), 0.0, 1.0)


def fit_isotonic(p: np.ndarray, y: np.ndarray) -> Calibrator:
    iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
    iso.fit(np.asarray(p, float), np.asarray(y, float))
    return Calibrator("isotonic", iso)


def fit_platt(p: np.ndarray, y: np.ndarray) -> Calibrator:
    lr = LogisticRegression(C=1.0)
    lr.fit(_logit(p).reshape(-1, 1), np.asarray(y, int))
    return Calibrator("platt", lr, params={"a": float(lr.coef_[0, 0]), "b": float(lr.intercept_[0])})


def fit_calibrator(p: np.ndarray, y: np.ndarray, isotonic_min: int = ISOTONIC_MIN) -> Calibrator:
    """Isotonic if ``len(p) >= isotonic_min`` else Platt; constant base rate if only one class is present."""
    p, y = np.asarray(p, float), np.asarray(y, int)
    if p.size == 0:
        return Calibrator("constant", constant=0.5)
    if len(np.unique(y)) < 2:
        return Calibrator("constant", constant=float(y.mean()))
    return fit_isotonic(p, y) if p.size >= isotonic_min else fit_platt(p, y)


def reliability_bins(p: np.ndarray, y: np.ndarray, n_bins: int = 10) -> list[dict[str, float]]:
    """Equal-width bins on [0, 1]; only non-empty bins are returned as ``{p_pred, p_obs, n}``."""
    p, y = np.asarray(p, float), np.asarray(y, float)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1], right=False), 0, n_bins - 1)
    out: list[dict[str, float]] = []
    for b in range(n_bins):
        m = idx == b
        n = int(m.sum())
        if n:
            out.append({"p_pred": float(p[m].mean()), "p_obs": float(y[m].mean()), "n": n})
    return out


def bucket_win_rates(p: np.ndarray, y: np.ndarray, n_bins: int = 5) -> list[dict[str, float]]:
    """Observed win rate with a Wilson 95% CI per predicted-probability bucket (non-empty buckets only)."""
    p, y = np.asarray(p, float), np.asarray(y, float)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1], right=False), 0, n_bins - 1)
    out: list[dict[str, float]] = []
    for b in range(n_bins):
        m = idx == b
        n = int(m.sum())
        if not n:
            continue
        wins = float(y[m].sum())
        lo, hi = wilson_ci(wins, n)
        out.append({"p_lo": float(edges[b]), "p_hi": float(edges[b + 1]), "n": n,
                    "win_rate": wins / n, "ci_low": lo, "ci_high": hi})
    return out
