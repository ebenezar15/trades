"""Shared domain types used across all IndexEdge modules.

Conventions
-----------
* All timestamps are timezone-aware and in IST (``Asia/Kolkata``).
* Candle data is passed around as a ``pandas.DataFrame`` with a tz-aware
  ``DatetimeIndex`` (candle *open* time) and float columns
  ``open, high, low, close, volume`` and optionally ``oi``.
* Prices are rupees (floats), never paise.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any

CANDLE_COLUMNS = ["open", "high", "low", "close", "volume"]


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

    @property
    def sign(self) -> int:
        return 1 if self is Side.BUY else -1

    def opposite(self) -> Side:
        return Side.SELL if self is Side.BUY else Side.BUY


class OrderType(str, Enum):
    """Only LIMIT and stop-loss-limit are allowed for algo orders (SEBI / Angel One).

    MARKET, IOC and AMO are intentionally absent and must never be added.
    """

    LIMIT = "LIMIT"
    SL_LIMIT = "STOPLOSS_LIMIT"


class ProductType(str, Enum):
    INTRADAY = "INTRADAY"
    CARRYFORWARD = "CARRYFORWARD"


class OrderStatus(str, Enum):
    PENDING = "PENDING"          # created locally, not yet acknowledged
    OPEN = "OPEN"                # resting at the exchange
    TRIGGER_PENDING = "TRIGGER_PENDING"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"

    @property
    def is_terminal(self) -> bool:
        return self in (OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED)


class InstrumentKind(str, Enum):
    INDEX = "INDEX"
    FUTURE = "FUTIDX"
    OPTION = "OPTIDX"
    EQUITY = "EQ"


class OptionType(str, Enum):
    CE = "CE"
    PE = "PE"


class Mode(str, Enum):
    PAPER = "paper"
    LIVE = "live"


class ExecutionMode(str, Enum):
    SIGNAL_ONLY = "signal_only"
    CONFIRM = "confirm"
    AUTO = "auto"


class Regime(str, Enum):
    TREND_UP = "trend_up"
    TREND_DOWN = "trend_down"
    RANGE = "range"
    HIGH_VOL = "high_vol"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Instrument:
    token: str
    symbol: str                 # broker trading symbol, e.g. NIFTY28OCT2625000CE
    name: str                   # underlying name, e.g. NIFTY / BANKNIFTY
    exchange: str               # NSE / NFO
    kind: InstrumentKind
    lot_size: int = 1
    tick_size: float = 0.05
    expiry: date | None = None
    strike: float | None = None
    option_type: OptionType | None = None


@dataclass
class Tick:
    token: str
    ts: datetime
    ltp: float
    volume: float = 0.0          # cumulative day volume
    oi: float = 0.0
    bid: float | None = None
    ask: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None   # previous close


@dataclass
class OrderRequest:
    instrument: Instrument
    side: Side
    quantity: int
    order_type: OrderType
    price: float
    trigger_price: float | None = None
    product: ProductType = ProductType.INTRADAY
    tag: str = ""                # strategy / trade id for audit
    client_id: str = field(default_factory=lambda: new_id("ord_"))

    def __post_init__(self) -> None:
        if not isinstance(self.order_type, OrderType):
            raise ValueError(f"order type {self.order_type!r} not allowed")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.price <= 0:
            raise ValueError("LIMIT price must be positive")
        if self.order_type is OrderType.SL_LIMIT and not self.trigger_price:
            raise ValueError("SL-L order requires trigger_price")


@dataclass
class Order:
    request: OrderRequest
    broker_id: str
    status: OrderStatus = OrderStatus.PENDING
    filled_qty: int = 0
    avg_price: float = 0.0
    message: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def pending_qty(self) -> int:
        return self.request.quantity - self.filled_qty


@dataclass
class Fill:
    broker_order_id: str
    instrument: Instrument
    side: Side
    quantity: int
    price: float
    ts: datetime


@dataclass
class Position:
    instrument: Instrument
    net_qty: int                 # +long / -short
    avg_price: float
    ltp: float = 0.0
    realised_pnl: float = 0.0

    @property
    def unrealised_pnl(self) -> float:
        return (self.ltp - self.avg_price) * self.net_qty if self.net_qty else 0.0


@dataclass
class Funds:
    available_cash: float
    used_margin: float = 0.0
    net: float = 0.0


@dataclass
class ContextSnapshot:
    """Output of the external-indices context engine."""

    ts: datetime
    macro_bias: float                       # -1 (bearish) .. +1 (bullish)
    risk_off: bool
    event_risk: float                       # 0..1
    inputs: dict[str, dict[str, Any]]       # name -> {value, change_pct, age_s, stale, weight}
    reasons: list[str]
    stale_required: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return not self.stale_required


@dataclass
class NewsItem:
    id: str
    ts: datetime
    source: str
    title: str
    url: str
    summary: str = ""
    sentiment: float = 0.0       # -1..1
    relevance: float = 0.0       # 0..1
    impact: str = "low"          # low / medium / high
    entities: list[str] = field(default_factory=list)


@dataclass
class MarketEvent:
    id: str
    ts: datetime
    title: str
    category: str                # rbi / fomc / budget / cpi / expiry / results / holiday / other
    impact: str = "medium"
    blackout_before_min: int = 15
    blackout_after_min: int = 15


@dataclass
class StrategySignal:
    """Raw trigger produced by a strategy plug-in (before probability/gating)."""

    strategy_id: str
    underlying: str              # NIFTY / BANKNIFTY
    ts: datetime
    side: Side                   # direction on the underlying
    entry: float
    stop: float
    targets: list[float]
    reasons: list[str]
    timeframe: str = "5m"
    features: dict[str, float] = field(default_factory=dict)
    instrument_hint: str = "future"     # future / option / index / straddle
    time_exit: str | None = None        # "HH:MM"

    @property
    def risk_points(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def rr(self) -> float:
        if not self.targets or self.risk_points == 0:
            return 0.0
        return abs(self.targets[0] - self.entry) / self.risk_points


@dataclass
class Signal:
    """Final, gated, explained signal shown to the user."""

    id: str
    raw: StrategySignal
    probability: float | None            # calibrated P(target before stop); None = insufficient data
    probability_ci: tuple[float, float] | None
    sample_size: int
    expected_rr: float
    ev_per_trade: float                  # rupees after costs, per suggested quantity
    quantity: int
    instrument: Instrument | None
    badges: list[str]
    reasons: list[str]
    context_reasons: list[str]
    news_reasons: list[str]
    top_features: list[tuple[str, float]]
    regime: Regime
    status: str = "active"               # active / blocked / taken / skipped / expired
    blocked_reason: str = ""
    created_at: datetime | None = None
