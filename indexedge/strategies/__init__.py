"""Strategy plug-ins (see :mod:`indexedge.strategies.base` for the contract)."""
from indexedge.strategies.base import Strategy
from indexedge.strategies.registry import STRATEGY_IDS, all_strategies, get_strategy

__all__ = ["STRATEGY_IDS", "Strategy", "all_strategies", "get_strategy"]
