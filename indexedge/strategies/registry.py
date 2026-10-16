"""Strategy registry: every plug-in keyed by its id."""
from __future__ import annotations

from typing import Any

from indexedge.strategies.base import Strategy
from indexedge.strategies.cpr import CprPivot, CprVwap
from indexedge.strategies.divergence import RsiDivergence
from indexedge.strategies.gap import GapFill
from indexedge.strategies.inside_bar import InsideBarBreakout
from indexedge.strategies.oi_pcr import OiPcr
from indexedge.strategies.orb import ORB15
from indexedge.strategies.squeeze import SqueezeBreakout
from indexedge.strategies.straddle import ExpiryStraddle, Straddle920
from indexedge.strategies.trend import EmaSupertrendAdx, TripleSupertrend
from indexedge.strategies.vwap_pullback import VWAPPullback

STRATEGY_CLASSES: dict[str, type[Strategy]] = {
    cls.id: cls
    for cls in (ORB15, VWAPPullback, EmaSupertrendAdx, TripleSupertrend, CprPivot, CprVwap, SqueezeBreakout,
                RsiDivergence, OiPcr, Straddle920, ExpiryStraddle, GapFill, InsideBarBreakout)
}

STRATEGY_IDS: list[str] = list(STRATEGY_CLASSES)


def get_strategy(strategy_id: str, params: dict[str, Any] | None = None) -> Strategy:
    try:
        cls = STRATEGY_CLASSES[strategy_id]
    except KeyError as exc:
        raise KeyError(f"unknown strategy {strategy_id!r}; known: {STRATEGY_IDS}") from exc
    return cls(params)


def all_strategies(params_overrides: dict[str, dict[str, Any]] | None = None) -> dict[str, Strategy]:
    """Fresh instances of every strategy; ``params_overrides`` maps id -> params (unknown keys ignored)."""
    out: dict[str, Strategy] = {}
    for sid, cls in STRATEGY_CLASSES.items():
        over = dict((params_overrides or {}).get(sid) or {})
        over = {k: v for k, v in over.items() if k in cls.default_params}
        out[sid] = cls(over)
    return out
