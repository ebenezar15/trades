"""Broker abstraction shared by the Angel One connector and the paper (simulated) broker."""
from __future__ import annotations

import abc
from datetime import datetime
from typing import Awaitable, Callable

import pandas as pd

from indexedge.core.types import Fill, Funds, Order, OrderRequest, Position, Tick

TickHandler = Callable[[Tick], Awaitable[None] | None]
OrderHandler = Callable[[Order], Awaitable[None] | None]


class BrokerError(Exception):
    pass


class BrokerBase(abc.ABC):
    """Async broker interface. All prices in rupees. Order types limited to LIMIT / SL-L."""

    name: str = "base"

    # --- session -----------------------------------------------------------------
    @abc.abstractmethod
    async def connect(self) -> None: ...

    @abc.abstractmethod
    async def disconnect(self) -> None: ...

    @property
    @abc.abstractmethod
    def connected(self) -> bool: ...

    # --- market data -------------------------------------------------------------
    @abc.abstractmethod
    async def subscribe_ticks(self, tokens: list[tuple[str, str]], handler: TickHandler) -> None:
        """Subscribe to live ticks. ``tokens`` = [(exchange, token), ...]."""

    @abc.abstractmethod
    async def get_candles(
        self, exchange: str, token: str, interval: str, start: datetime, end: datetime
    ) -> pd.DataFrame:
        """Historical candles; interval in {'1m','3m','5m','15m','1h','1d'}."""

    @abc.abstractmethod
    async def get_ltp(self, exchange: str, token: str, symbol: str = "") -> float: ...

    # --- orders ------------------------------------------------------------------
    @abc.abstractmethod
    async def place_order(self, req: OrderRequest) -> Order: ...

    @abc.abstractmethod
    async def modify_order(self, order: Order, price: float, trigger_price: float | None = None) -> Order: ...

    @abc.abstractmethod
    async def cancel_order(self, order: Order) -> Order: ...

    @abc.abstractmethod
    async def order_book(self) -> list[Order]: ...

    @abc.abstractmethod
    async def trade_book(self) -> list[Fill]: ...

    @abc.abstractmethod
    async def positions(self) -> list[Position]: ...

    @abc.abstractmethod
    async def funds(self) -> Funds: ...

    def on_order_update(self, handler: OrderHandler) -> None:
        """Register a callback for order status changes."""
        self._order_handlers = getattr(self, "_order_handlers", [])
        self._order_handlers.append(handler)

    async def _emit_order(self, order: Order) -> None:
        import asyncio

        for h in getattr(self, "_order_handlers", []):
            res = h(order)
            if asyncio.iscoroutine(res):
                await res
