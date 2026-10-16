"""Journal record types.

``TradeRecord`` is the single row type used for paper, live and backtest trades so analytics treat them
identically. ``pnl`` is the *gross* P&L in rupees; ``costs`` are charges; net P&L = ``pnl - costs``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from indexedge.core.types import new_id

TRADE_MODES = ("paper", "live", "backtest", "manual")
TRADE_STATUSES = ("open", "closed")


@dataclass
class TradeRecord:
    signal_id: str | None
    strategy_id: str
    underlying: str
    instrument_symbol: str
    side: str  # "BUY" / "SELL" on the traded instrument
    qty: int
    entry_ts: datetime
    entry_price: float
    stop: float | None
    targets: list[float] = field(default_factory=list)
    mode: str = "paper"
    status: str = "open"
    id: str = field(default_factory=lambda: new_id("trd_"))
    # exit fields
    exit_ts: datetime | None = None
    exit_price: float | None = None
    outcome: str | None = None  # target / stop / time / eod / manual / kill_switch ...
    pnl: float | None = None  # gross rupees
    costs: float = 0.0
    r_multiple: float | None = None
    mfe: float | None = None  # max favourable excursion, points (>= 0)
    mae: float | None = None  # max adverse excursion, points (>= 0)
    # plan / context snapshot
    planned_entry: float | None = None  # signal entry price (plan); slippage = entry_price vs this
    point_value: float = 1.0
    regime: str | None = None
    rule_violations: list[str] = field(default_factory=list)
    features: dict[str, Any] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.mode not in TRADE_MODES:
            raise ValueError(f"mode must be one of {TRADE_MODES}, got {self.mode!r}")
        if self.status not in TRADE_STATUSES:
            raise ValueError(f"status must be one of {TRADE_STATUSES}, got {self.status!r}")
        side = getattr(self.side, "value", self.side)
        if side not in ("BUY", "SELL"):
            raise ValueError(f"side must be BUY or SELL, got {self.side!r}")
        self.side = side
        if self.qty <= 0:
            raise ValueError("qty must be positive")


@dataclass
class DayStats:
    """Per-day figures the risk engine needs (paper + live trades only; backtests excluded)."""

    realised_pnl: float = 0.0  # net of costs, closed trades whose exit is on that day
    trades: int = 0  # trades opened that day
    consecutive_losses: int = 0  # trailing run of losing closed trades (net pnl < 0)
    last_loss_ts: datetime | None = None
    last_trade_qty: int = 0
    last_loss_qty: int = 0
    open_trades: int = 0
