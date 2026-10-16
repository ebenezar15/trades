"""Journal export to CSV (trades only) or Excel (one sheet per table, via openpyxl)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd

if TYPE_CHECKING:
    from indexedge.journal.db import Journal

EXPORT_FORMATS = ("csv", "xlsx")


def _flatten(df: pd.DataFrame) -> pd.DataFrame:
    """Make a DataFrame spreadsheet-safe: JSON-encode lists/dicts, ISO-format tz-aware timestamps."""
    out = df.copy()
    for col in out.columns:
        s = out[col]
        if isinstance(s.dtype, pd.DatetimeTZDtype):
            out[col] = s.map(lambda v: v.isoformat() if pd.notna(v) else "")
        elif s.dtype == object:
            out[col] = s.map(_cell)
    return out


def _cell(v: Any) -> Any:
    if isinstance(v, (list, dict, tuple)):
        return json.dumps(v, default=str)
    return v


def export_trades(trades: pd.DataFrame, path: Path | str, fmt: str = "csv") -> Path:
    path = Path(path)
    if fmt not in EXPORT_FORMATS:
        raise ValueError(f"fmt must be one of {EXPORT_FORMATS}")
    path.parent.mkdir(parents=True, exist_ok=True)
    flat = _flatten(trades)
    if fmt == "csv":
        flat.to_csv(path, index=False)
    else:
        flat.to_excel(path, index=False, sheet_name="trades", engine="openpyxl")
    return path


def export_journal(journal: Journal, path: Path | str, fmt: str = "csv") -> Path:
    """CSV: trades table. XLSX: sheets trades, signals, notes, orders, fills."""
    path = Path(path)
    if fmt not in EXPORT_FORMATS:
        raise ValueError(f"fmt must be one of {EXPORT_FORMATS}")
    if fmt == "csv":
        return export_trades(journal.trades(), path, "csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    sheets = {
        "trades": journal.trades(),
        "signals": journal.signals(),
        "notes": journal.notes(),
        "orders": journal.orders(),
        "fills": journal.fills(),
    }
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for name, df in sheets.items():
            _flatten(df).to_excel(xw, index=False, sheet_name=name)
    return path
