"""Strategy Lab service: backtests, walk-forward, Monte Carlo and report cards for the API / UI.

Data source: ``data_provider(underlying, days)`` if given, else the 5-minute candles in the ``CandleStore``
(``<symbol>/5m``, or 1-minute data resampled), else deterministic **synthetic** data. Every result carries
``data_source`` ('synthetic' | 'broker'); synthetic results are labelled as NOT real performance.
"""
from __future__ import annotations

import logging
import math
from collections.abc import Callable
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from indexedge.backtest.costs import CostModel
from indexedge.backtest.engine import backtest_signals
from indexedge.backtest.metrics import compute_metrics, monte_carlo
from indexedge.backtest.report import report_card
from indexedge.backtest.walkforward import walk_forward
from indexedge.config.settings import Settings, load_settings
from indexedge.data.synthetic import SPECS, generate_intraday
from indexedge.features.engine import build_features
from indexedge.strategies.registry import STRATEGY_IDS, get_strategy

log = logging.getLogger(__name__)

SYNTHETIC_DISCLAIMER = (
    "SYNTHETIC DATA: these numbers come from simulated prices, NOT real market history. "
    "They show how the strategy logic behaves, not real performance."
)
MIN_SESSIONS = 20
DEFAULT_LOT_SIZES = {name: spec.lot_size for name, spec in SPECS.items()}

DataProvider = Callable[[str, int], pd.DataFrame | None]


def _json(v: Any) -> Any:
    """Recursively make values JSON-safe (NaN/inf -> None, timestamps -> ISO, numpy -> python)."""
    if isinstance(v, dict):
        return {str(k): _json(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_json(x) for x in v]
    if isinstance(v, (pd.Timestamp, date)):
        return v.isoformat()
    if isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


class StrategyLab:
    def __init__(self, settings: Settings, store: Any | None = None, data_provider: DataProvider | None = None,
                 cost_model: CostModel | None = None, lot_sizes: dict[str, int] | None = None,
                 synthetic_seed: int = 0) -> None:
        self.settings = settings
        self.store = store
        self.data_provider = data_provider
        self.cost_model = cost_model or CostModel()
        self.lot_sizes = {**DEFAULT_LOT_SIZES, **(lot_sizes or {})}
        self.seed = synthetic_seed
        self._cache: dict[tuple[str, int], tuple[pd.DataFrame, str]] = {}

    @classmethod
    def default(cls) -> StrategyLab:
        settings = load_settings()
        store = None
        try:
            from indexedge.data.store import CandleStore

            store = CandleStore(settings.app.data_dir / "candles")
        except Exception:  # noqa: BLE001 - store is optional
            log.warning("candle store unavailable; the lab will use synthetic data")
        return cls(settings, store=store)

    # ------------------------------------------------------------------ data
    def _real_data(self, underlying: str, days: int) -> pd.DataFrame | None:
        df = None
        if self.data_provider is not None:
            try:
                df = self.data_provider(underlying, days)
            except Exception as exc:  # noqa: BLE001 - provider failures fall back to other sources
                log.warning("data provider failed for %s: %s", underlying, exc)
        if (df is None or df.empty) and self.store is not None:
            try:
                df = self.store.load(underlying, "5m")
                if df.empty:
                    one = self.store.load(underlying, "1m")
                    df = self.store.resample(one, "5m") if not one.empty else df
            except Exception as exc:  # noqa: BLE001
                log.warning("candle store read failed for %s: %s", underlying, exc)
                df = None
        if df is None or df.empty:
            return None
        sessions = sorted(set(df.index.date))
        if len(sessions) < MIN_SESSIONS:
            return None
        keep = set(sessions[-days:])
        return df[np.isin(df.index.date, list(keep))]

    def features(self, underlying: str, days: int) -> tuple[pd.DataFrame, str]:
        key = (underlying.upper(), int(days))
        if key not in self._cache:
            raw = self._real_data(key[0], key[1])
            source = "broker"
            if raw is None:
                raw = generate_intraday(key[0], days=key[1], seed=self.seed,
                                        holidays=[date.fromisoformat(h) for h in self.settings.holidays])
                source = "synthetic"
            self._cache[key] = (build_features(raw), source)
        return self._cache[key]

    def candles(self, days: int = 250, underlyings: tuple[str, ...] = ("NIFTY", "BANKNIFTY")) -> dict[str, pd.DataFrame]:
        """Raw 5-minute candles per underlying (same source selection as the backtests)."""
        return {u: self.features(u, days)[0][["open", "high", "low", "close", "volume"]] for u in underlyings}

    def data_source(self, underlying: str = "NIFTY", days: int = 250) -> str:
        return self.features(underlying, days)[1]

    # ------------------------------------------------------------------ backtests
    def _strategy(self, strategy_id: str, params: dict[str, Any] | None = None):
        cls_params = dict(self.settings.strategies.params.get(strategy_id, {}))
        cls_params.update(params or {})
        strat = get_strategy(strategy_id)
        return type(strat)({k: v for k, v in cls_params.items() if k in strat.default_params})

    def backtest(self, strategy_id: str, underlying: str = "NIFTY", days: int = 250,
                 params: dict[str, Any] | None = None, mc_sims: int = 2000) -> dict[str, Any]:
        underlying = underlying.upper()
        strat = self._strategy(strategy_id, params)
        feat, source = self.features(underlying, days)
        lot = int(self.lot_sizes.get(underlying, 1))
        signals = strat.scan(feat, underlying, None)
        trades, skipped = backtest_signals(strat, signals, feat, lot, self.cost_model)
        metrics = compute_metrics(trades)
        n_days = len(set(feat.index.date))
        train, test = min(60, max(20, n_days // 4)), min(20, max(5, n_days // 12))
        wf = walk_forward(strat, feat, underlying, lot, self.cost_model, train_days=train, test_days=test)
        use_oos = bool(wf["folds"])
        report = report_card(wf["oos_metrics"] if use_oos else metrics, self.settings.strategies)
        report["metrics_basis"] = "walk_forward_oos" if use_oos else "full_sample"
        report["warnings"] = list(report["warnings"]) + list(wf["warnings"])
        report["backtest_quality"] = strat.backtest_quality
        if strat.backtest_quality == "approximate":
            report["warnings"].insert(0, "approximate backtest (modelled option data): paper-only")
        if not signals and strat.last_skip_reason:
            report["warnings"].append(f"no signals: {strat.last_skip_reason}")
        report["data_source"] = source
        if source == "synthetic":
            report["warnings"].insert(0, SYNTHETIC_DISCLAIMER)
        equity, cum = [], 0.0
        for ts, pnl in zip(trades["exit_ts"], trades["pnl"]):
            cum += float(pnl)
            equity.append({"time": int(pd.Timestamp(ts).timestamp()), "value": round(cum, 2)})
        tlist = trades.to_dict(orient="records")
        return _json({
            "strategy_id": strategy_id, "underlying": underlying, "days": n_days, "data_source": source,
            "real_performance": source != "synthetic",
            "disclaimer": SYNTHETIC_DISCLAIMER if source == "synthetic" else "",
            "backtest_quality": strat.backtest_quality, "lot_size": lot, "params": strat.params,
            "n_signals": len(signals), "skipped_overlap": skipped,
            "metrics": metrics, "report": report, "trades": tlist, "equity": equity,
            "monte_carlo": monte_carlo(trades["r"].to_numpy(), n=mc_sims), "walk_forward": wf,
        })

    def report_all(self, underlying: str = "NIFTY", days: int = 250) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for sid in STRATEGY_IDS:
            res = self.backtest(sid, underlying, days, mc_sims=200)
            rep = dict(res["report"])
            rep["metrics"] = res["metrics"]
            out[sid] = rep
        return out
