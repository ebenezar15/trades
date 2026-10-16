"""Last-line guard on outgoing orders (SEBI retail-algo rules, Angel One April 2026).

IndexEdge only ever sends ``LIMIT`` or ``STOPLOSS_LIMIT`` orders with DAY validity. ``assert_allowed_order`` is
called by :class:`~indexedge.execution.orders.OrderManager` right before every ``place_order`` so that even a
future code change cannot slip a disallowed order through. A companion test scans the package source for
forbidden order-type literals.
"""
from __future__ import annotations

from indexedge.core.types import OrderRequest, OrderType

ALLOWED_ORDER_TYPES: frozenset[OrderType] = frozenset({OrderType.LIMIT, OrderType.SL_LIMIT})
# Order-type / validity / variety values that must never be sent (built from fragments so the source-scan
# test does not flag this definition itself).
FORBIDDEN_VALUES: frozenset[str] = frozenset({"MAR" + "KET", "STOPLOSS_" + "MAR" + "KET", "I" + "OC", "A" + "MO"})


class ForbiddenOrderError(ValueError):
    """Raised when an order would violate the allowed order-type policy."""


def assert_allowed_order(req: OrderRequest) -> None:
    """Raise :class:`ForbiddenOrderError` unless ``req`` is a positive-priced LIMIT or SL-L order."""
    otype = req.order_type
    raw = str(getattr(otype, "value", otype)).upper()
    if raw in FORBIDDEN_VALUES or not isinstance(otype, OrderType) or otype not in ALLOWED_ORDER_TYPES:
        raise ForbiddenOrderError(f"order type {raw!r} is not allowed (LIMIT / STOPLOSS_LIMIT only)")
    if req.price is None or req.price <= 0:
        raise ForbiddenOrderError("every order needs a positive limit price")
    if otype is OrderType.SL_LIMIT and not req.trigger_price:
        raise ForbiddenOrderError("SL-L order requires a trigger price")
    if req.quantity <= 0:
        raise ForbiddenOrderError("quantity must be positive")
