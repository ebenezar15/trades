"""DuckDB-backed trading journal.

* One connection guarded by an ``RLock`` so the journal can be shared by the asyncio loop, the broker
  websocket thread and the scheduler.
* JSON payloads (lists / dicts) are stored as VARCHAR JSON and decoded on read.
* Timestamps are stored as naive IST ``TIMESTAMP`` values and returned tz-aware (``Asia/Kolkata``).
* Schema is versioned in ``schema_version``; :data:`MIGRATIONS` are applied in order on open.
* The ``settings`` key/value table holds small persistent state (e.g. the risk engine's day lock).
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from indexedge.core.clock import IST
from indexedge.core.types import Fill, NewsItem, Order, Signal, new_id
from indexedge.journal.models import DayStats, TradeRecord

_TS_COLS = {"ts", "created_at", "updated_at", "entry_ts", "exit_ts", "applied_at"}
_JSON_COLS = {
    "targets",
    "probability_ci",
    "badges",
    "reasons",
    "context_reasons",
    "news_reasons",
    "top_features",
    "features",
    "rule_violations",
    "context",
    "tags",
    "entities",
    "old_value",
    "new_value",
    "value",
}

MIGRATIONS: list[tuple[int, list[str]]] = [
    (
        1,
        [
            """CREATE TABLE IF NOT EXISTS signals (
            id VARCHAR PRIMARY KEY, ts TIMESTAMP, created_at TIMESTAMP, updated_at TIMESTAMP,
            strategy_id VARCHAR, underlying VARCHAR, side VARCHAR, timeframe VARCHAR,
            entry DOUBLE, stop DOUBLE, targets VARCHAR, probability DOUBLE, probability_ci VARCHAR,
            sample_size INTEGER, expected_rr DOUBLE, ev_per_trade DOUBLE, quantity INTEGER,
            instrument VARCHAR, badges VARCHAR, reasons VARCHAR, context_reasons VARCHAR,
            news_reasons VARCHAR, top_features VARCHAR, features VARCHAR, regime VARCHAR, status VARCHAR,
            blocked_reason VARCHAR, status_reason VARCHAR)""",
            """CREATE TABLE IF NOT EXISTS orders (
            client_id VARCHAR PRIMARY KEY, broker_id VARCHAR, symbol VARCHAR, token VARCHAR, side VARCHAR,
            qty INTEGER, order_type VARCHAR, price DOUBLE, trigger_price DOUBLE, product VARCHAR, tag VARCHAR,
            status VARCHAR, filled_qty INTEGER, avg_price DOUBLE, message VARCHAR,
            created_at TIMESTAMP, updated_at TIMESTAMP)""",
            """CREATE TABLE IF NOT EXISTS fills (
            id VARCHAR PRIMARY KEY, broker_order_id VARCHAR, ts TIMESTAMP, symbol VARCHAR, token VARCHAR,
            side VARCHAR, qty INTEGER, price DOUBLE)""",
            """CREATE TABLE IF NOT EXISTS trades (
            id VARCHAR PRIMARY KEY, signal_id VARCHAR, strategy_id VARCHAR, underlying VARCHAR,
            instrument_symbol VARCHAR, side VARCHAR, qty INTEGER, entry_ts TIMESTAMP, entry_price DOUBLE,
            stop DOUBLE, targets VARCHAR, mode VARCHAR, status VARCHAR, exit_ts TIMESTAMP, exit_price DOUBLE,
            outcome VARCHAR, pnl DOUBLE, costs DOUBLE, r_multiple DOUBLE, mfe DOUBLE, mae DOUBLE,
            planned_entry DOUBLE, point_value DOUBLE, regime VARCHAR, rule_violations VARCHAR,
            features VARCHAR, reasons VARCHAR, context VARCHAR)""",
            """CREATE TABLE IF NOT EXISTS notes (
            id VARCHAR PRIMARY KEY, trade_id VARCHAR, ts TIMESTAMP, text VARCHAR, tags VARCHAR,
            emotion VARCHAR)""",
            """CREATE TABLE IF NOT EXISTS news (
            id VARCHAR PRIMARY KEY, ts TIMESTAMP, source VARCHAR, title VARCHAR, url VARCHAR, summary VARCHAR,
            sentiment DOUBLE, relevance DOUBLE, impact VARCHAR, entities VARCHAR)""",
            """CREATE TABLE IF NOT EXISTS settings_audit (
            id VARCHAR PRIMARY KEY, ts TIMESTAMP, key VARCHAR, old_value VARCHAR, new_value VARCHAR,
            source VARCHAR)""",
            """CREATE TABLE IF NOT EXISTS equity_daily (
            day DATE PRIMARY KEY, equity DOUBLE, realised_pnl DOUBLE, trades INTEGER)""",
            """CREATE TABLE IF NOT EXISTS settings (
            key VARCHAR PRIMARY KEY, value VARCHAR, updated_at TIMESTAMP)""",
        ],
    ),
]

SCHEMA_VERSION = MIGRATIONS[-1][0]


# ---------------------------------------------------------------------------------------------- helpers
def _json_default(o: Any) -> Any:
    if isinstance(o, Enum):
        return o.value
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if is_dataclass(o) and not isinstance(o, type):
        return asdict(o)
    if hasattr(o, "item"):  # numpy scalars
        return o.item()
    if isinstance(o, (set, tuple)):
        return list(o)
    return str(o)


def dumps(v: Any) -> str | None:
    return None if v is None else json.dumps(v, default=_json_default)


def loads(v: Any) -> Any:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if not isinstance(v, str):
        return v
    try:
        return json.loads(v)
    except ValueError:
        return v


def to_ist_naive(dt: datetime | None) -> datetime | None:
    """Store timestamps as naive IST wall-clock time."""
    if dt is None:
        return None
    if isinstance(dt, pd.Timestamp):
        dt = dt.to_pydatetime()
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(IST).replace(tzinfo=None)


def _bound(v: date | datetime | None, end: bool = False) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return to_ist_naive(v)
    d = v + timedelta(days=1) if end else v
    return datetime.combine(d, time(0, 0))


def _enum(v: Any) -> Any:
    return v.value if isinstance(v, Enum) else v


class Journal:
    """Persistent store for signals, orders, fills, trades, notes, news and small settings."""

    def __init__(self, path: Path | str = ":memory:") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._con = duckdb.connect(self.path)
        self._migrate()

    # ------------------------------------------------------------------------------------- plumbing
    def close(self) -> None:
        with self._lock:
            self._con.close()

    def _exec(self, sql: str, params: list | tuple | None = None) -> None:
        with self._lock:
            self._con.execute(sql, params or [])

    def _rows(self, sql: str, params: list | tuple | None = None) -> list[tuple]:
        with self._lock:
            return self._con.execute(sql, params or []).fetchall()

    def _df(self, sql: str, params: list | tuple | None = None) -> pd.DataFrame:
        with self._lock:
            df = self._con.execute(sql, params or []).df()
        for col in df.columns:
            if col in _TS_COLS:
                df[col] = pd.to_datetime(df[col]).dt.tz_localize(IST)
            elif col in _JSON_COLS:
                df[col] = df[col].astype(object).map(loads)
        return df

    def _migrate(self) -> None:
        with self._lock:
            self._con.execute(
                "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER, applied_at TIMESTAMP)"
            )
            cur = self._con.execute("SELECT max(version) FROM schema_version").fetchone()[0] or 0
            for version, stmts in MIGRATIONS:
                if version <= cur:
                    continue
                self._con.execute("BEGIN TRANSACTION")
                try:
                    for s in stmts:
                        self._con.execute(s)
                    self._con.execute(
                        "INSERT INTO schema_version VALUES (?, ?)", [version, to_ist_naive(datetime.now(IST))]
                    )
                    self._con.execute("COMMIT")
                except Exception:
                    self._con.execute("ROLLBACK")
                    raise

    @property
    def schema_version(self) -> int:
        return self._rows("SELECT max(version) FROM schema_version")[0][0] or 0

    # -------------------------------------------------------------------------------------- signals
    def record_signal(self, sig: Signal) -> None:
        raw = sig.raw
        self._exec(
            "INSERT OR REPLACE INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                sig.id,
                to_ist_naive(raw.ts),
                to_ist_naive(sig.created_at or raw.ts),
                to_ist_naive(sig.created_at),
                raw.strategy_id,
                raw.underlying,
                _enum(raw.side),
                raw.timeframe,
                raw.entry,
                raw.stop,
                dumps(raw.targets),
                sig.probability,
                dumps(sig.probability_ci),
                sig.sample_size,
                sig.expected_rr,
                sig.ev_per_trade,
                sig.quantity,
                sig.instrument.symbol if sig.instrument else None,
                dumps(sig.badges),
                dumps(raw.reasons),
                dumps(sig.context_reasons),
                dumps(sig.news_reasons),
                dumps(sig.top_features),
                dumps(raw.features),
                _enum(sig.regime),
                sig.status,
                sig.blocked_reason,
                "",
            ],
        )

    def update_signal_status(self, id: str, status: str, reason: str = "") -> None:
        self._exec(
            "UPDATE signals SET status = ?, status_reason = ?, updated_at = ? WHERE id = ?",
            [status, reason, to_ist_naive(datetime.now(IST)), id],
        )

    def signals(
        self,
        start: date | datetime | None = None,
        end: date | datetime | None = None,
        strategy: str | None = None,
        status: str | None = None,
        limit: int | None = None,
    ) -> pd.DataFrame:
        where, params = self._filters("ts", start, end, {"strategy_id": strategy, "status": status})
        sql = f"SELECT * FROM signals {where} ORDER BY ts DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return self._df(sql, params)

    # ------------------------------------------------------------------------------- orders / fills
    def record_order(self, order: Order) -> None:
        r = order.request
        self._exec(
            "INSERT OR REPLACE INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                r.client_id,
                order.broker_id,
                r.instrument.symbol,
                r.instrument.token,
                _enum(r.side),
                r.quantity,
                _enum(r.order_type),
                r.price,
                r.trigger_price,
                _enum(r.product),
                r.tag,
                _enum(order.status),
                order.filled_qty,
                order.avg_price,
                order.message,
                to_ist_naive(order.created_at),
                to_ist_naive(order.updated_at or order.created_at),
            ],
        )

    def orders(self, tag: str | None = None) -> pd.DataFrame:
        where, params = self._filters(None, None, None, {"tag": tag})
        return self._df(f"SELECT * FROM orders {where} ORDER BY created_at", params)

    def record_fill(self, fill: Fill) -> str:
        fid = new_id("fill_")
        self._exec(
            "INSERT INTO fills VALUES (?,?,?,?,?,?,?,?)",
            [
                fid,
                fill.broker_order_id,
                to_ist_naive(fill.ts),
                fill.instrument.symbol,
                fill.instrument.token,
                _enum(fill.side),
                fill.quantity,
                fill.price,
            ],
        )
        return fid

    def fills(self, broker_order_id: str | None = None) -> pd.DataFrame:
        where, params = self._filters(None, None, None, {"broker_order_id": broker_order_id})
        return self._df(f"SELECT * FROM fills {where} ORDER BY ts", params)

    # -------------------------------------------------------------------------------------- trades
    def open_trade(self, trade: TradeRecord) -> str:
        t = trade
        self._exec(
            "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                t.id,
                t.signal_id,
                t.strategy_id,
                t.underlying,
                t.instrument_symbol,
                t.side,
                t.qty,
                to_ist_naive(t.entry_ts),
                t.entry_price,
                t.stop,
                dumps(t.targets),
                t.mode,
                t.status,
                to_ist_naive(t.exit_ts),
                t.exit_price,
                t.outcome,
                t.pnl,
                t.costs,
                t.r_multiple,
                t.mfe,
                t.mae,
                t.planned_entry,
                t.point_value,
                t.regime,
                dumps(t.rule_violations),
                dumps(t.features),
                dumps(t.reasons),
                dumps(t.context),
            ],
        )
        return t.id

    def close_trade(
        self,
        id: str,
        exit_ts: datetime,
        exit_price: float,
        outcome: str,
        pnl: float,
        costs: float = 0.0,
        *,
        mfe: float | None = None,
        mae: float | None = None,
        r_multiple: float | None = None,
        rule_violations: list[str] | None = None,
    ) -> None:
        """Close an open trade. ``pnl`` is gross rupees; ``r_multiple`` derived from the stop if omitted."""
        rows = self._rows(
            "SELECT side, entry_price, stop, status, rule_violations FROM trades WHERE id = ?", [id]
        )
        if not rows:
            raise KeyError(f"trade {id} not found")
        side, entry, stop, status, viol = rows[0]
        if status == "closed":
            raise ValueError(f"trade {id} is already closed")
        if r_multiple is None and stop is not None and entry is not None and abs(entry - stop) > 0:
            sign = 1 if side == "BUY" else -1
            r_multiple = (exit_price - entry) * sign / abs(entry - stop)
        violations = (loads(viol) or []) + list(rule_violations or [])
        self._exec(
            "UPDATE trades SET status='closed', exit_ts=?, exit_price=?, outcome=?, pnl=?, costs=?, "
            "r_multiple=?, mfe=coalesce(?, mfe), mae=coalesce(?, mae), rule_violations=? WHERE id=?",
            [
                to_ist_naive(exit_ts),
                exit_price,
                outcome,
                pnl,
                costs,
                r_multiple,
                mfe,
                mae,
                dumps(violations),
                id,
            ],
        )

    def trades(
        self,
        start: date | datetime | None = None,
        end: date | datetime | None = None,
        strategy: str | None = None,
        status: str | None = None,
        mode: str | None = None,
        underlying: str | None = None,
    ) -> pd.DataFrame:
        """Trades filtered by entry time; ``end`` as a date is inclusive. Adds ``net_pnl`` column."""
        where, params = self._filters(
            "entry_ts",
            start,
            end,
            {"strategy_id": strategy, "status": status, "mode": mode, "underlying": underlying},
        )
        df = self._df(f"SELECT * FROM trades {where} ORDER BY entry_ts, id", params)
        df["net_pnl"] = df["pnl"].astype(float) - df["costs"].fillna(0.0).astype(float)
        return df

    def trade(self, id: str) -> dict[str, Any] | None:
        df = self._df("SELECT * FROM trades WHERE id = ?", [id])
        if df.empty:
            return None
        row = df.iloc[0].to_dict()
        return {k: (None if not isinstance(v, (list, dict)) and pd.isna(v) else v) for k, v in row.items()}

    def today_stats(self, day: date, include_backtest: bool = False) -> DayStats:
        mode_sql = "" if include_backtest else " AND mode <> 'backtest'"
        lo, hi = _bound(day), _bound(day, end=True)
        opened = self._rows(
            f"SELECT qty, status FROM trades WHERE entry_ts >= ? AND entry_ts < ?{mode_sql} "
            "ORDER BY entry_ts",
            [lo, hi],
        )
        closed = self._rows(
            "SELECT pnl, costs, qty, exit_ts FROM trades WHERE status='closed' "
            "AND exit_ts >= ? AND exit_ts < ?"
            f"{mode_sql} ORDER BY exit_ts",
            [lo, hi],
        )
        stats = DayStats(
            trades=len(opened),
            open_trades=sum(1 for _, s in opened if s == "open"),
            last_trade_qty=opened[-1][0] if opened else 0,
        )
        for pnl, costs, qty, exit_ts in closed:
            net = (pnl or 0.0) - (costs or 0.0)
            stats.realised_pnl += net
            if net < 0:
                stats.consecutive_losses += 1
                stats.last_loss_ts = exit_ts.replace(tzinfo=IST)
                stats.last_loss_qty = qty
            else:
                stats.consecutive_losses = 0
        return stats

    # ------------------------------------------------------------------------------- notes / news
    def add_note(
        self,
        trade_id: str,
        text: str,
        tags: list[str] | None = None,
        emotion: str = "",
        ts: datetime | None = None,
    ) -> str:
        nid = new_id("note_")
        self._exec(
            "INSERT INTO notes VALUES (?,?,?,?,?,?)",
            [nid, trade_id, to_ist_naive(ts or datetime.now(IST)), text, dumps(list(tags or [])), emotion],
        )
        return nid

    def notes(self, trade_id: str | None = None) -> pd.DataFrame:
        where, params = self._filters(None, None, None, {"trade_id": trade_id})
        return self._df(f"SELECT * FROM notes {where} ORDER BY ts", params)

    def record_news(self, item: NewsItem) -> None:
        self._exec(
            "INSERT OR REPLACE INTO news VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                item.id,
                to_ist_naive(item.ts),
                item.source,
                item.title,
                item.url,
                item.summary,
                item.sentiment,
                item.relevance,
                item.impact,
                dumps(item.entities),
            ],
        )

    def news(self, limit: int = 50) -> pd.DataFrame:
        return self._df(f"SELECT * FROM news ORDER BY ts DESC LIMIT {int(limit)}")

    # --------------------------------------------------------------------- settings / audit / equity
    def get_setting(self, key: str, default: Any = None) -> Any:
        rows = self._rows("SELECT value FROM settings WHERE key = ?", [key])
        return loads(rows[0][0]) if rows else default

    def set_setting(self, key: str, value: Any) -> None:
        self._exec(
            "INSERT OR REPLACE INTO settings VALUES (?,?,?)",
            [key, dumps(value), to_ist_naive(datetime.now(IST))],
        )

    def audit_setting(
        self, key: str, old: Any, new: Any, source: str = "ui", ts: datetime | None = None
    ) -> None:
        self._exec(
            "INSERT INTO settings_audit VALUES (?,?,?,?,?,?)",
            [new_id("aud_"), to_ist_naive(ts or datetime.now(IST)), key, dumps(old), dumps(new), source],
        )

    def settings_audit(self) -> pd.DataFrame:
        return self._df("SELECT * FROM settings_audit ORDER BY ts")

    def record_equity(self, day: date, equity: float, realised_pnl: float, trades: int) -> None:
        self._exec(
            "INSERT OR REPLACE INTO equity_daily VALUES (?,?,?,?)", [day, equity, realised_pnl, trades]
        )

    def equity_daily(self) -> pd.DataFrame:
        return self._df("SELECT * FROM equity_daily ORDER BY day")

    # -------------------------------------------------------------------------------------- export
    def export(self, path: Path | str, fmt: str = "csv") -> Path:
        from indexedge.journal.export import export_journal

        return export_journal(self, path, fmt)

    # ------------------------------------------------------------------------------------ internal
    @staticmethod
    def _filters(ts_col: str | None, start: Any, end: Any, eq: dict[str, Any]) -> tuple[str, list]:
        clauses, params = [], []
        if ts_col and start is not None:
            clauses.append(f"{ts_col} >= ?")
            params.append(_bound(start))
        if ts_col and end is not None:
            if isinstance(end, datetime):
                clauses.append(f"{ts_col} <= ?")
            else:
                clauses.append(f"{ts_col} < ?")
            params.append(_bound(end, end=True))
        for col, val in eq.items():
            if val is not None:
                clauses.append(f"{col} = ?")
                params.append(val)
        return ("WHERE " + " AND ".join(clauses)) if clauses else "", params
