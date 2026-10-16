"""JSON helpers shared by REST and WebSocket layers."""
from __future__ import annotations

import dataclasses
import math
from datetime import date, datetime
from enum import Enum
from typing import Any

import numpy as np
import pandas as pd


def jsonable(v: Any) -> Any:
    """Recursively convert numpy / pandas / dataclass / enum values into JSON-safe Python values."""
    if v is None or isinstance(v, (str, bool, int)):
        return v
    if isinstance(v, float):
        return None if math.isnan(v) or math.isinf(v) else v
    if isinstance(v, Enum):
        return v.value
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, (pd.Timestamp, datetime)):
        return None if pd.isna(v) else v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [jsonable(x) for x in v]
    if isinstance(v, np.ndarray):
        return [jsonable(x) for x in v.tolist()]
    if isinstance(v, pd.DataFrame):
        return [jsonable(r) for r in v.to_dict(orient="records")]
    if isinstance(v, pd.Series):
        return jsonable(v.to_dict())
    if dataclasses.is_dataclass(v):
        return jsonable(dataclasses.asdict(v))
    if v is pd.NaT:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return str(v)


def candles_json(df: pd.DataFrame) -> list[dict]:
    if df is None or df.empty:
        return []
    out = []
    for ts, r in zip(df.index, df[["open", "high", "low", "close"]].to_numpy()):
        vol = 0.0
        out.append({"time": int(pd.Timestamp(ts).timestamp()), "open": float(r[0]), "high": float(r[1]),
                    "low": float(r[2]), "close": float(r[3]), "volume": vol})
    if "volume" in df:
        for row, v in zip(out, df["volume"].to_numpy()):
            row["volume"] = 0.0 if pd.isna(v) else float(v)
    return out


def line_json(s: pd.Series) -> list[dict]:
    s = s.dropna()
    return [{"time": int(pd.Timestamp(t).timestamp()), "value": round(float(v), 2)} for t, v in s.items()]
