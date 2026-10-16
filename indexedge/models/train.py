"""Per-strategy probability model: purged walk-forward CV, calibration on out-of-sample predictions only.

Honesty rules (BUILD_PROMPT §1.4):

* Folds are contiguous in time and only ever train on the *past*; training rows whose label window overlaps the
  test fold (``exit_ts >= test start``) or that fall within ``embargo_bars`` bars of it are purged. No shuffling.
* All reported metrics (AUC, Brier, reliability, bucket win rates) are computed from out-of-sample predictions.
  The calibrator used for those metrics is cross-fitted (fit on the other OOS blocks), the final calibrator is
  fit on all OOS predictions.
* ``ModelBundle.usable`` is False when there are fewer than ``min_samples`` signals, when the calibrated model
  does not beat quoting the base rate (``brier_skill <= 0``), or when ``leakage_check`` flags a feature. The app
  must then show :func:`fallback_stats` (historical win rate with Wilson CI) instead of a model probability.

LightGBM is used for ``n >= LGBM_MIN_ROWS`` signals, otherwise a regularised logistic regression (median
imputation + standardisation, because indicator warm-up periods leave NaNs).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from indexedge.core.clock import IST
from indexedge.models.calibration import (
    Calibrator,
    brier,
    brier_skill,
    bucket_win_rates,
    fit_calibrator,
    reliability_bins,
    wilson_ci,
)

log = logging.getLogger(__name__)

LGBM_MIN_ROWS = 300
LEAKAGE_TARGETS = ("win", "r_multiple", "fwd_ret_pct")
LGBM_PARAMS: dict[str, Any] = {
    "objective": "binary",
    "metric": "binary_logloss",
    "learning_rate": 0.05,
    "num_leaves": 15,
    "min_data_in_leaf": 30,
    "feature_fraction": 0.8,
    "lambda_l2": 1.0,
    "max_depth": -1,
    "deterministic": True,
    "force_row_wise": True,
    "num_threads": 1,
    "verbose": -1,
}
MAX_ROUNDS = 200
EARLY_STOP = 20


# --------------------------------------------------------------------------------------------- bundle
@dataclass
class ModelBundle:
    """Trained model + calibrator + out-of-sample metrics for one strategy."""

    strategy_id: str
    feature_cols: list[str]
    kind: str                         # "lightgbm" | "logistic" | "none"
    model: Any
    calibrator: Calibrator
    metrics: dict[str, Any]
    usable: bool
    base_rate: float
    num_rounds: int = 0
    trained_at: str = field(default_factory=lambda: datetime.now(IST).isoformat())

    def _frame(self, X: Any) -> pd.DataFrame:
        if isinstance(X, pd.Series):
            X = X.to_frame().T
        elif isinstance(X, dict):
            X = pd.DataFrame([X])
        elif not isinstance(X, pd.DataFrame):
            arr = np.asarray(X, float)
            X = pd.DataFrame(arr.reshape(-1, len(self.feature_cols)), columns=self.feature_cols)
        out = X.reindex(columns=self.feature_cols)
        return out.apply(pd.to_numeric, errors="coerce").astype(float)

    def predict_raw(self, X: Any) -> np.ndarray:
        Xf = self._frame(X)
        return _predict_raw(self.kind, self.model, Xf, self.base_rate)

    def predict_proba(self, X: Any) -> np.ndarray:
        """Calibrated P(win) for each row of ``X`` (DataFrame, Series/dict for one row, or 2-D array)."""
        return self.calibrator.predict(self.predict_raw(X))

    def top_features(self, x_row: Any, k: int = 5) -> list[tuple[str, float]]:
        from indexedge.models.explain import top_contributions

        return top_contributions(self, x_row, k=k)

    def save(self, path: str | Path) -> Path:
        import joblib

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self, path)
        return path

    @staticmethod
    def load(path: str | Path) -> ModelBundle:
        import joblib

        obj = joblib.load(Path(path))
        if not isinstance(obj, ModelBundle):
            raise TypeError(f"{path} does not contain a ModelBundle")
        return obj


# --------------------------------------------------------------------------------------------- helpers
def _predict_raw(kind: str, model: Any, X: pd.DataFrame, base_rate: float) -> np.ndarray:
    if kind == "lightgbm":
        return np.asarray(model.predict(X.to_numpy(float)), float)
    if kind == "logistic":
        return np.asarray(model.predict_proba(X.to_numpy(float))[:, 1], float)
    return np.full(len(X), base_rate, float)


def _make_logistic(seed: int):
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    return Pipeline([
        ("impute", SimpleImputer(strategy="median", keep_empty_features=True)),
        ("scale", StandardScaler()),
        ("clf", LogisticRegression(C=0.3, max_iter=2000, random_state=seed)),
    ])


def _fit_lgbm(X: np.ndarray, y: np.ndarray, ts: np.ndarray, exit_ts: np.ndarray, seed: int,
              rounds: int | None = None) -> tuple[Any, int]:
    """Fit LightGBM; with ``rounds=None`` early-stop on the last 20% (time-ordered, purged) of the rows."""
    import lightgbm as lgb

    params = {**LGBM_PARAMS, "seed": seed, "bagging_seed": seed, "feature_fraction_seed": seed,
              "data_random_seed": seed}
    if rounds is not None:
        return lgb.train(params, lgb.Dataset(X, y), num_boost_round=max(1, rounds)), max(1, rounds)
    cut = int(len(y) * 0.8)
    v0 = ts[cut] if cut < len(ts) else None
    tr = np.arange(cut)
    if v0 is not None:
        tr = tr[exit_ts[:cut] < v0]
    va = np.arange(cut, len(y))
    if len(tr) < 50 or len(va) < 10:
        return lgb.train(params, lgb.Dataset(X, y), num_boost_round=100), 100
    dtr = lgb.Dataset(X[tr], y[tr])
    dva = lgb.Dataset(X[va], y[va], reference=dtr)
    booster = lgb.train(params, dtr, num_boost_round=MAX_ROUNDS, valid_sets=[dva],
                        callbacks=[lgb.early_stopping(EARLY_STOP, verbose=False)])
    best = int(booster.best_iteration or MAX_ROUNDS)
    return booster, max(1, best)


def purged_walk_forward_splits(
    ts: pd.Series, exit_ts: pd.Series, n_splits: int = 5, embargo: timedelta = timedelta(0),
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Expanding-window walk-forward splits over time-sorted rows.

    Rows are cut into ``n_splits + 1`` contiguous blocks; fold *k* tests on block *k* and trains only on rows
    that start more than ``embargo`` before the test block and whose label window ended before it.
    """
    t = pd.to_datetime(ts).reset_index(drop=True)
    e = pd.to_datetime(exit_ts).reset_index(drop=True).fillna(t)
    if not t.is_monotonic_increasing:
        raise ValueError("rows must be sorted by ts (never shuffle time-series data)")
    blocks = [b for b in np.array_split(np.arange(len(t)), n_splits + 1) if len(b)]
    out = []
    for test in blocks[1:]:
        t0 = t.iloc[test[0]]
        cand = np.arange(test[0])
        keep = ((t.iloc[cand] < t0 - embargo) & (e.iloc[cand] < t0)).to_numpy()
        out.append((cand[keep], test))
    return out


def leakage_check(dataset: pd.DataFrame, feature_cols: list[str] | None = None, threshold: float = 0.9,
                  targets: tuple[str, ...] = LEAKAGE_TARGETS, min_obs: int = 20) -> list[dict[str, Any]]:
    """Flag features whose |Pearson| or |Spearman| correlation with a label/forward-return exceeds threshold.

    A causal feature should never be (almost) a deterministic function of the future; any hit means look-ahead.
    """
    from indexedge.models.labels import default_feature_cols

    cols = feature_cols or default_feature_cols(dataset)
    flags: list[dict[str, Any]] = []
    for tgt in targets:
        if tgt not in dataset.columns:
            continue
        yv = pd.to_numeric(dataset[tgt], errors="coerce")
        for c in cols:
            if c not in dataset.columns or c == tgt:
                continue
            xv = pd.to_numeric(dataset[c], errors="coerce")
            m = xv.notna() & yv.notna()
            if m.sum() < min_obs or xv[m].nunique() < 2 or yv[m].nunique() < 2:
                continue
            corr = max(abs(xv[m].corr(yv[m])), abs(xv[m].corr(yv[m], method="spearman")))
            if np.isfinite(corr) and corr > threshold:
                flags.append({"feature": c, "target": tgt, "corr": float(corr)})
    return flags


def fallback_stats(dataset: pd.DataFrame, min_samples: int = 50, label: str = "win") -> dict[str, Any]:
    """Historical win rate with Wilson 95% CI. ``win_rate``/``ci`` are None below ``min_samples``."""
    if dataset is None or dataset.empty or label not in dataset.columns:
        return {"win_rate": None, "ci": None, "n": 0, "insufficient": True}
    y = pd.to_numeric(dataset[label], errors="coerce").dropna()
    n = len(y)
    if n < min_samples:
        return {"win_rate": None, "ci": None, "n": n, "insufficient": True}
    wins = float(y.sum())
    return {"win_rate": wins / n, "ci": wilson_ci(wins, n), "n": n, "insufficient": False}


def _cross_fit_calibrated(p: np.ndarray, y: np.ndarray, n_blocks: int = 5) -> np.ndarray:
    """Calibrated OOS probabilities where each block's calibrator never saw that block."""
    out = np.empty_like(p)
    blocks = [b for b in np.array_split(np.arange(len(p)), min(n_blocks, max(len(p), 1))) if len(b)]
    if len(blocks) < 2:
        return fit_calibrator(p, y).predict(p)
    for b in blocks:
        mask = np.ones(len(p), bool)
        mask[b] = False
        out[b] = fit_calibrator(p[mask], y[mask]).predict(p[b])
    return out


# --------------------------------------------------------------------------------------------- training
def train_model(
    dataset: pd.DataFrame,
    feature_cols: list[str] | None = None,
    n_splits: int = 5,
    embargo_bars: int = 24,
    bar_minutes: int = 5,
    min_samples: int = 50,
    strategy_id: str | None = None,
    label: str = "win",
    seed: int = 42,
) -> ModelBundle:
    """Train + calibrate a model for one strategy's labelled signals (see module docstring)."""
    from indexedge.models.labels import default_feature_cols

    data = dataset.dropna(subset=[label]) if label in dataset.columns else dataset.iloc[:0]
    data = data.sort_values("ts", kind="stable").reset_index(drop=True)
    cols = list(feature_cols) if feature_cols else default_feature_cols(data)
    sid = strategy_id or (str(data["strategy_id"].iloc[0]) if "strategy_id" in data and len(data) else "")
    n = len(data)
    y = data[label].astype(int).to_numpy() if n else np.zeros(0, int)
    base = float(y.mean()) if n else 0.5
    metrics: dict[str, Any] = {"n": n, "base_rate": base, "feature_count": len(cols), "label": label,
                               "n_oos": 0, "auc": float("nan"), "brier": float("nan"),
                               "brier_skill": float("nan"), "reliability": [], "buckets": []}

    def _unusable(reason: str) -> ModelBundle:
        metrics.update({"model_kind": "none", "calibration": "constant", "usable": False, "reason": reason,
                        "fallback": fallback_stats(data, min_samples, label)})
        return ModelBundle(sid, cols, "none", None, Calibrator("constant", constant=base), metrics, False, base)

    if n < min_samples:
        return _unusable(f"insufficient data: {n} signals < {min_samples}")
    if len(np.unique(y)) < 2:
        return _unusable("only one outcome class in history")

    X = data.reindex(columns=cols).apply(pd.to_numeric, errors="coerce").astype(float).to_numpy()
    ts = pd.to_datetime(data["ts"])
    exit_ts = pd.to_datetime(data["exit_ts"]) if "exit_ts" in data else ts
    kind = "lightgbm" if n >= LGBM_MIN_ROWS else "logistic"
    embargo = timedelta(minutes=embargo_bars * bar_minutes)
    ts_np, ex_np = ts.to_numpy(), exit_ts.fillna(ts).to_numpy()

    oos_idx: list[np.ndarray] = []
    oos_raw: list[np.ndarray] = []
    best_iters: list[int] = []
    min_train = max(30, min_samples // 2)
    for tr, te in purged_walk_forward_splits(ts, exit_ts, n_splits, embargo):
        if len(tr) < min_train or len(np.unique(y[tr])) < 2:
            continue
        if kind == "lightgbm":
            model, best = _fit_lgbm(X[tr], y[tr], ts_np[tr], ex_np[tr], seed)
            best_iters.append(best)
        else:
            model = _make_logistic(seed).fit(X[tr], y[tr])
        oos_idx.append(te)
        oos_raw.append(_predict_raw(kind, model, pd.DataFrame(X[te], columns=cols), base))

    if not oos_idx:
        return _unusable("not enough history for walk-forward validation")
    idx = np.concatenate(oos_idx)
    p_raw = np.concatenate(oos_raw)
    y_oos = y[idx]
    p_cal = _cross_fit_calibrated(p_raw, y_oos)
    calibrator = fit_calibrator(p_raw, y_oos)

    from sklearn.metrics import roc_auc_score

    oos_base = float(y_oos.mean())
    auc = float(roc_auc_score(y_oos, p_raw)) if len(np.unique(y_oos)) == 2 else float("nan")
    sq = (p_cal - y_oos) ** 2
    skill = brier_skill(p_cal, y_oos, oos_base)
    leaks = leakage_check(data, cols)

    rounds = int(np.median(best_iters)) if best_iters else 100
    if kind == "lightgbm":
        final, _ = _fit_lgbm(X, y, ts_np, ex_np, seed, rounds=rounds)
    else:
        final = _make_logistic(seed).fit(X, y)

    usable, reason = True, "ok"
    if leaks:
        usable, reason = False, "possible look-ahead leakage: " + ", ".join(sorted({f["feature"] for f in leaks}))
    elif not np.isfinite(skill) or skill <= 0:
        usable, reason = False, "model does not beat the historical win rate out-of-sample (Brier skill <= 0)"

    metrics.update({
        "model_kind": kind, "calibration": calibrator.kind, "n_oos": len(idx), "n_folds": len(oos_idx),
        "oos_base_rate": oos_base, "auc": auc, "brier": brier(p_cal, y_oos),
        "brier_ref": float(np.mean((oos_base - y_oos) ** 2)), "brier_skill": float(skill),
        "brier_sq_err_std": float(sq.std(ddof=1)) if len(sq) > 1 else 0.0,
        "reliability": reliability_bins(p_cal, y_oos, 10), "buckets": bucket_win_rates(p_cal, y_oos, 5),
        "num_rounds": rounds if kind == "lightgbm" else 0, "embargo_bars": embargo_bars,
        "leakage_flags": leaks, "usable": usable, "reason": reason,
        "fallback": fallback_stats(data, min_samples, label),
        "train_start": str(ts.iloc[0]), "train_end": str(ts.iloc[-1]),
    })
    return ModelBundle(sid, cols, kind, final, calibrator, metrics, usable, base,
                       num_rounds=rounds if kind == "lightgbm" else 0)
