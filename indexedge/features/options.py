"""Options analytics: Black-Scholes price/Greeks, implied volatility and option-chain statistics.

Units: ``T`` in years, ``r`` and ``sigma`` as decimals (0.2 = 20%). Theta is per calendar day, vega per
1 volatility point. In :func:`enrich_chain` the ``iv`` column is in **percent** (e.g. 14.2), matching NSE
option-chain convention.
"""
from __future__ import annotations

import math
from datetime import date, datetime

import numpy as np
import pandas as pd
from scipy.optimize import brentq
from scipy.stats import norm

from indexedge.core.clock import IST, MARKET_CLOSE

_EPS_T = 1e-9


def _norm_opt(opt: str) -> str:
    o = str(getattr(opt, "value", opt)).upper()
    if o in ("CE", "C", "CALL"):
        return "CE"
    if o in ("PE", "P", "PUT"):
        return "PE"
    raise ValueError(f"unknown option type {opt!r}")


def _d1d2(S: float, K: float, T: float, r: float, sigma: float) -> tuple[float, float]:
    sq = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / sq
    return d1, d1 - sq


def bs_price(S: float, K: float, T: float, r: float, sigma: float, opt: str = "CE") -> float:
    """European Black-Scholes price. At/after expiry (or zero vol) returns the (discounted) intrinsic."""
    o = _norm_opt(opt)
    if S <= 0 or K <= 0:
        return float("nan")
    if T <= _EPS_T or sigma <= 0:
        T = max(T, 0.0)
        fwd_intr = S - K * math.exp(-r * T)
        return max(fwd_intr, 0.0) if o == "CE" else max(-fwd_intr, 0.0)
    d1, d2 = _d1d2(S, K, T, r, sigma)
    if o == "CE":
        return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def bs_greeks(S: float, K: float, T: float, r: float, sigma: float, opt: str = "CE") -> dict:
    """Delta, gamma, theta (per calendar day) and vega (per 1 vol point)."""
    o = _norm_opt(opt)
    if S <= 0 or K <= 0 or T <= _EPS_T or sigma <= 0 or not np.isfinite(sigma):
        if S > 0 and K > 0:
            itm = (S > K) if o == "CE" else (S < K)
            delta = (1.0 if itm else 0.0) * (1 if o == "CE" else -1)
        else:
            delta = float("nan")
        return {"delta": delta, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    d1, d2 = _d1d2(S, K, T, r, sigma)
    pdf = norm.pdf(d1)
    disc = math.exp(-r * T)
    gamma = pdf / (S * sigma * math.sqrt(T))
    vega = S * pdf * math.sqrt(T) / 100.0
    common = -S * pdf * sigma / (2 * math.sqrt(T))
    if o == "CE":
        delta = norm.cdf(d1)
        theta = common - r * K * disc * norm.cdf(d2)
    else:
        delta = norm.cdf(d1) - 1.0
        theta = common + r * K * disc * norm.cdf(-d2)
    return {"delta": delta, "gamma": gamma, "theta": theta / 365.0, "vega": vega}


def implied_vol(price: float, S: float, K: float, T: float, r: float, opt: str = "CE",
                lo: float = 1e-4, hi: float = 5.0) -> float:
    """Implied volatility (decimal) by Brent's method; NaN when no solution exists in [lo, hi]."""
    try:
        o = _norm_opt(opt)
        if not all(np.isfinite([price, S, K, T])) or price <= 0 or S <= 0 or K <= 0 or T <= _EPS_T:
            return float("nan")
        f_lo = bs_price(S, K, T, r, lo, o) - price
        f_hi = bs_price(S, K, T, r, hi, o) - price
        if f_lo > 0 or f_hi < 0:
            return float("nan")
        if f_lo == 0:
            return lo
        return float(brentq(lambda s: bs_price(S, K, T, r, s, o) - price, lo, hi, xtol=1e-8, maxiter=200))
    except (ValueError, OverflowError, ZeroDivisionError):
        return float("nan")


# ----------------------------------------------------------------------------- chain analytics


def _opt_col(chain: pd.DataFrame) -> pd.Series:
    return chain["option_type"].map(lambda x: _norm_opt(x))


def pcr(chain: pd.DataFrame) -> float:
    """Put/call ratio of total open interest. NaN when call OI is zero or the chain is empty."""
    if chain is None or chain.empty:
        return float("nan")
    t = _opt_col(chain)
    pe = float(chain.loc[t == "PE", "oi"].sum())
    ce = float(chain.loc[t == "CE", "oi"].sum())
    return pe / ce if ce > 0 else float("nan")


def max_pain(chain: pd.DataFrame) -> float:
    """Strike at which total intrinsic value payable to option holders (by OI) is smallest."""
    if chain is None or chain.empty:
        return float("nan")
    t = _opt_col(chain)
    strikes = np.sort(chain["strike"].astype(float).unique())
    ce = chain[t == "CE"]
    pe = chain[t == "PE"]
    ck, co = ce["strike"].to_numpy(float), ce["oi"].fillna(0).to_numpy(float)
    pk, po = pe["strike"].to_numpy(float), pe["oi"].fillna(0).to_numpy(float)
    pain = [(co * np.clip(s - ck, 0, None)).sum() + (po * np.clip(pk - s, 0, None)).sum() for s in strikes]
    return float(strikes[int(np.argmin(pain))])


def oi_walls(chain: pd.DataFrame, n: int = 3) -> dict:
    """Top-``n`` call-OI strikes (resistance) and put-OI strikes (support), highest OI first."""
    if chain is None or chain.empty:
        return {"resistance": [], "support": []}
    t = _opt_col(chain)

    def top(kind: str) -> list[float]:
        sub = chain[t == kind].groupby("strike")["oi"].sum().sort_values(ascending=False)
        return [float(k) for k in sub.index[:n]]

    return {"resistance": top("CE"), "support": top("PE")}


def oi_buildup(price_change: float, oi_change: float) -> str:
    """Classic OI interpretation: price↑ OI↑ long_buildup, price↓ OI↑ short_buildup,
    price↑ OI↓ short_covering, price↓ OI↓ long_unwinding; otherwise neutral."""
    try:
        p, o = float(price_change), float(oi_change)
    except (TypeError, ValueError):
        return "neutral"
    if not (np.isfinite(p) and np.isfinite(o)) or p == 0 or o == 0:
        return "neutral"
    if p > 0:
        return "long_buildup" if o > 0 else "short_covering"
    return "short_buildup" if o > 0 else "long_unwinding"


def _to_dt(x: datetime | date) -> datetime:
    if isinstance(x, datetime):
        return x if x.tzinfo else x.replace(tzinfo=IST)
    return datetime.combine(x, MARKET_CLOSE, IST)


def year_fraction(expiry_dt: datetime | date, now: datetime) -> float:
    """Calendar-time year fraction to expiry (a bare date expires at 15:30 IST)."""
    return max((_to_dt(expiry_dt) - _to_dt(now)).total_seconds(), 0.0) / (365.0 * 86400.0)


_PRICE_CHANGE_COLS = ("ltp_change", "price_change", "change")


def enrich_chain(chain: pd.DataFrame, spot: float, expiry_dt: datetime | date, now: datetime,
                 r: float = 0.065) -> pd.DataFrame:
    """Add ``iv`` (percent), ``delta, gamma, theta, vega`` and ``buildup`` to a chain.

    IV is solved from ``ltp``; if that fails and the input already has an ``iv`` column (percent), it is kept.
    ``buildup`` uses a price-change column (``ltp_change``/``price_change``/``change``) or ``prev_ltp`` if
    present, else is ``neutral``.
    """
    out = chain.copy()
    if out.empty:
        for c in ("iv", "delta", "gamma", "theta", "vega"):
            out[c] = pd.Series(dtype=float)
        out["buildup"] = pd.Series(dtype=object)
        return out
    T = year_fraction(expiry_dt, now)
    given_iv = out["iv"].astype(float) if "iv" in out else pd.Series(np.nan, index=out.index)
    ivs, greeks = [], []
    for (_, row), giv in zip(out.iterrows(), given_iv):
        o = _norm_opt(row["option_type"])
        iv = implied_vol(float(row["ltp"]), float(spot), float(row["strike"]), T, r, o)
        if not np.isfinite(iv) and np.isfinite(giv) and giv > 0:
            iv = giv / 100.0
        ivs.append(iv * 100.0 if np.isfinite(iv) else np.nan)
        g = bs_greeks(float(spot), float(row["strike"]), T, r, iv if np.isfinite(iv) else 0.0, o) \
            if np.isfinite(iv) else {"delta": np.nan, "gamma": np.nan, "theta": np.nan, "vega": np.nan}
        greeks.append(g)
    out["iv"] = ivs
    for k in ("delta", "gamma", "theta", "vega"):
        out[k] = [g[k] for g in greeks]
    pc = None
    for c in _PRICE_CHANGE_COLS:
        if c in out:
            pc = out[c].astype(float)
            break
    if pc is None and "prev_ltp" in out:
        pc = out["ltp"].astype(float) - out["prev_ltp"].astype(float)
    if pc is not None and "oi_change" in out:
        out["buildup"] = [oi_buildup(p, o) for p, o in zip(pc, out["oi_change"].astype(float))]
    else:
        out["buildup"] = "neutral"
    return out
