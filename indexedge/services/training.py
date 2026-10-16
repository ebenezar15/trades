"""Batch (re)training of every strategy's probability model from historical candles.

``train_all`` is decoupled from concrete strategies: it accepts any mapping ``id -> obj`` exposing
``scan(feat, underlying) -> list[StrategySignal]``. For each underlying it builds features once, runs every
strategy's scan, labels the signals with the shared exit simulator, trains a calibrated model per strategy and
stores it (usable or not) in the registry so the app can show either the model or the historical fallback.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import pandas as pd

from indexedge.features.engine import build_features
from indexedge.models.labels import label_signals
from indexedge.models.registry import ModelRegistry
from indexedge.models.train import train_model

log = logging.getLogger(__name__)


def _candles_by_underlying(lab_or_data: Any) -> dict[str, pd.DataFrame]:
    """Accept a DataFrame, ``{underlying: DataFrame}``, or an object with a ``data``/``candles`` mapping."""
    if isinstance(lab_or_data, pd.DataFrame):
        return {str(lab_or_data.attrs.get("underlying", "NIFTY")): lab_or_data}
    if isinstance(lab_or_data, Mapping):
        return {str(k): v for k, v in lab_or_data.items() if isinstance(v, pd.DataFrame)}
    for attr in ("data", "candles"):
        got = getattr(lab_or_data, attr, None)
        if callable(got):
            got = got()
        if isinstance(got, Mapping):
            return {str(k): v for k, v in got.items() if isinstance(v, pd.DataFrame)}
    raise TypeError("train_all needs a DataFrame, a {underlying: DataFrame} mapping or an object with .data")


def build_datasets(lab_or_data: Any, strategies: Mapping[str, Any], max_bars: int,
                   context: Any = None) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    """Labelled dataset per strategy id (all underlyings concatenated) and per-strategy scan errors."""
    parts: dict[str, list[pd.DataFrame]] = {sid: [] for sid in strategies}
    errors: dict[str, str] = {}
    for underlying, df in _candles_by_underlying(lab_or_data).items():
        if df is None or df.empty:
            continue
        feat = build_features(df)
        for sid, strat in strategies.items():
            try:
                signals = strat.scan(feat, underlying) or []
            except Exception as exc:  # one broken plug-in must not stop the others
                log.exception("strategy %s scan failed on %s", sid, underlying)
                errors[sid] = f"scan failed: {exc}"
                continue
            ds = label_signals(signals, df, max_bars=max_bars, feat=feat, context=context)
            if not ds.empty:
                parts[sid].append(ds)
    out = {}
    for sid, frames in parts.items():
        out[sid] = (pd.concat(frames, ignore_index=True).sort_values("ts", kind="stable").reset_index(drop=True)
                    if frames else pd.DataFrame())
    return out, errors


def train_all(lab_or_data: Any, strategies: Mapping[str, Any], settings: Any, registry: ModelRegistry,
              context: Any = None, n_splits: int = 5) -> dict[str, dict[str, Any]]:
    """Train and register a model per strategy; returns ``{strategy_id: metrics}``."""
    ms = settings.models
    datasets, errors = build_datasets(lab_or_data, strategies, ms.horizon_bars, context)
    results: dict[str, dict[str, Any]] = {}
    for sid, ds in datasets.items():
        if sid in errors and ds.empty:
            results[sid] = {"n": 0, "usable": False, "reason": errors[sid]}
            continue
        if ds.empty:
            ds = pd.DataFrame(columns=["ts", "strategy_id", "win", "exit_ts"])
        bundle = train_model(ds, n_splits=n_splits, embargo_bars=ms.horizon_bars, min_samples=ms.min_samples,
                             strategy_id=sid)
        registry.put(sid, bundle)
        results[sid] = dict(bundle.metrics)
        if sid in errors:
            results[sid]["scan_error"] = errors[sid]
    return results
