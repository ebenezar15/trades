"""On-disk store of per-strategy ``ModelBundle`` objects: ``<dir>/<id>.joblib`` + ``<dir>/<id>.json`` metadata.

The JSON sidecar (metrics, usable flag, reliability bins) can be served by the API without unpickling models.
"""
from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path
from typing import Any

from indexedge.models.train import ModelBundle

log = logging.getLogger(__name__)
_SAFE = re.compile(r"[^A-Za-z0-9_.-]")


def _clean(o: Any) -> Any:
    """JSON-safe copy (NaN/inf -> None, numpy scalars -> python, tuples -> lists)."""
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, list | tuple):
        return [_clean(v) for v in o]
    if hasattr(o, "item") and not isinstance(o, str):
        try:
            o = o.item()
        except (ValueError, AttributeError):
            return str(o)
    if isinstance(o, float) and not math.isfinite(o):
        return None
    if isinstance(o, str | int | float | bool) or o is None:
        return o
    return str(o)


class ModelRegistry:
    def __init__(self, directory: str | Path) -> None:
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, ModelBundle] = {}

    def _stem(self, strategy_id: str) -> str:
        return _SAFE.sub("_", strategy_id) or "_"

    def _paths(self, strategy_id: str) -> tuple[Path, Path]:
        s = self._stem(strategy_id)
        return self.dir / f"{s}.joblib", self.dir / f"{s}.json"

    def put(self, strategy_id: str, bundle: ModelBundle) -> Path:
        model_path, meta_path = self._paths(strategy_id)
        tmp = model_path.with_suffix(".joblib.tmp")
        bundle.save(tmp)
        tmp.replace(model_path)
        meta_path.write_text(json.dumps(_clean(self._meta(strategy_id, bundle)), indent=2), encoding="utf-8")
        self._cache[strategy_id] = bundle
        return model_path

    @staticmethod
    def _meta(strategy_id: str, b: ModelBundle) -> dict[str, Any]:
        return {"strategy_id": strategy_id, "kind": b.kind, "usable": b.usable, "trained_at": b.trained_at,
                "base_rate": b.base_rate, "feature_cols": b.feature_cols, "metrics": b.metrics}

    def get(self, strategy_id: str) -> ModelBundle | None:
        if strategy_id in self._cache:
            return self._cache[strategy_id]
        model_path, _ = self._paths(strategy_id)
        if not model_path.exists():
            return None
        try:
            bundle = ModelBundle.load(model_path)
        except Exception as exc:  # noqa: BLE001 - corrupt / incompatible file -> behave as "no model"
            log.warning("could not load model %s: %s", model_path, exc)
            return None
        self._cache[strategy_id] = bundle
        return bundle

    def metadata(self, strategy_id: str) -> dict[str, Any] | None:
        _, meta_path = self._paths(strategy_id)
        if not meta_path.exists():
            return None
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def ids(self) -> list[str]:
        out = []
        for p in sorted(self.dir.glob("*.json")):
            meta = self.metadata(p.stem)
            out.append(meta.get("strategy_id", p.stem) if meta else p.stem)
        return out

    def delete(self, strategy_id: str) -> None:
        self._cache.pop(strategy_id, None)
        for p in self._paths(strategy_id):
            p.unlink(missing_ok=True)
