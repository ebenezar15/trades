"""The indices IndexEdge trades, and where each one lives at Angel One.

NIFTY and BANKNIFTY are NSE indices with derivatives on NFO; SENSEX is a BSE index with derivatives on BFO.
Lot sizes, strikes and expiries for real trading always come from the Angel One instrument master — the
``fallback_*`` values here are only used for demo data and when the master is unavailable.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UnderlyingInfo:
    name: str                 # canonical name in the instrument master (``name`` column)
    label: str                # shown in the UI
    index_exchange: str       # NSE | BSE
    fo_exchange: str          # NFO | BFO
    index_token: str          # Angel One index token
    index_symbol: str         # Angel One trading symbol of the index
    yahoo: str                # Yahoo Finance ticker (fallback data feed)
    fallback_strike_step: float
    fallback_lot_size: int
    expiry_weekday: int       # weekly / monthly expiry weekday (Mon=0): NSE Tuesday, BSE Thursday
    weekly_options: bool      # SEBI: one weekly index contract per exchange (NIFTY on NSE, SENSEX on BSE)


UNDERLYINGS: tuple[str, ...] = ("NIFTY", "BANKNIFTY", "SENSEX")

INFO: dict[str, UnderlyingInfo] = {
    "NIFTY": UnderlyingInfo("NIFTY", "NIFTY", "NSE", "NFO", "99926000", "Nifty 50", "^NSEI", 50.0, 65, 1, True),
    "BANKNIFTY": UnderlyingInfo("BANKNIFTY", "BANKNIFTY", "NSE", "NFO", "99926009", "Nifty Bank", "^NSEBANK",
                                100.0, 30, 1, False),
    "SENSEX": UnderlyingInfo("SENSEX", "SENSEX", "BSE", "BFO", "99919000", "SENSEX", "^BSESN", 100.0, 20, 3, True),
}

VIX = UnderlyingInfo("INDIAVIX", "INDIA VIX", "NSE", "NFO", "99926017", "India VIX", "^INDIAVIX", 0.0, 1, 1, False)


def info(underlying: str) -> UnderlyingInfo:
    try:
        return INFO[str(underlying).upper()]
    except KeyError as exc:
        raise ValueError(f"unknown underlying {underlying!r}; expected one of {list(UNDERLYINGS)}") from exc


def fo_exchange(underlying: str) -> str:
    u = INFO.get(str(underlying).upper())
    return u.fo_exchange if u else "NFO"


def index_exchange(underlying: str) -> str:
    u = INFO.get(str(underlying).upper())
    return u.index_exchange if u else "NSE"


def index_tokens(overrides: dict[str, str] | None = None) -> dict[str, str]:
    """Default index tokens merged with any user overrides (old config files may lack SENSEX)."""
    base = {u: i.index_token for u, i in INFO.items()}
    base[VIX.name] = VIX.index_token
    return {**base, **(overrides or {})}
