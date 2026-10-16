"""Round-trip cost estimate used for EV-after-costs (composer) and journal costs (trade manager).

``CostFn = (kind, qty, entry, exit) -> rupees`` where ``kind`` is ``"future"`` or ``"option"`` and prices are the
traded instrument's prices (option premium for options). The real schedule lives in
:class:`indexedge.backtest.costs.CostModel`; when that module is unavailable a conservative approximation is used
(₹20 brokerage per order + 0.1 % of turnover for STT / exchange / GST / stamp duty) so callers never crash.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

log = logging.getLogger(__name__)

CostFn = Callable[[str, int, float, float], float]


def approx_round_trip(kind: str, qty: int, entry: float, exit: float) -> float:
    """Fallback estimate: ₹40 brokerage (two orders) + 0.1 % of round-trip turnover."""
    turnover = abs(qty) * (abs(entry) + abs(exit))
    return round(40.0 + 0.001 * turnover, 2)


def default_cost_fn() -> CostFn:
    """``CostModel().round_trip`` if :mod:`indexedge.backtest.costs` is importable, else the approximation."""
    try:
        from indexedge.backtest.costs import CostModel

        model = CostModel()

        def _fn(kind: str, qty: int, entry: float, exit: float) -> float:
            return float(model.round_trip(kind, qty, entry, exit))

        return _fn
    except Exception as exc:  # noqa: BLE001 - any import/constructor problem -> approximation
        log.debug("CostModel unavailable (%s); using approximate costs", exc)
        return approx_round_trip
