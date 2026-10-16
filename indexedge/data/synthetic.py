"""Deterministic synthetic market data for tests, demo mode and offline backtests.

The generators are tuned so that strategies can be exercised meaningfully (not to predict anything):

* ``generate_intraday`` simulates 1-minute log returns for each session (09:15-15:30 IST) and aggregates them
  to the requested interval. Each day is drawn from a persistent Markov regime (trend up / trend down / range /
  high volatility) which sets drift, volatility and (for range days) mean reversion towards the open.
  Overnight gaps carry ~25% of daily variance, with rare larger jumps. Intraday volatility and volume are
  U-shaped; returns are fat-tailed (Student-t); volume is futures-like and rises with |return|.
* ``generate_option_chain`` prices a chain with Black-Scholes on a skewed smile and puts open interest
  concentrated on round strikes (calls above spot, puts below).
* ``synthetic_master_rows`` produces rows in the exact Angel One ``OpenAPIScripMaster.json`` format
  (strike x100, tick_size in paise, expiry ``DDMMMYYYY``).

Everything is deterministic given ``seed``; weekends and the supplied holidays are skipped.
"""
from __future__ import annotations

import calendar
import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Iterable

import numpy as np
import pandas as pd
from scipy.special import ndtr

from indexedge.core.clock import IST

SESSION_MINUTES = 375  # 09:15 -> 15:30
SESSION_START = time(9, 15)
TICK = 0.05


@dataclass(frozen=True)
class UnderlyingSpec:
    name: str
    base: float              # starting level
    daily_vol: float         # target close-to-close daily volatility (fraction)
    strike_step: float
    lot_size: int
    daily_volume: float      # futures-like units traded per day
    base_iv: float           # ATM implied vol (annualised)
    index_token: str
    index_symbol: str


SPECS: dict[str, UnderlyingSpec] = {
    "NIFTY": UnderlyingSpec("NIFTY", 25000.0, 0.009, 50.0, 65, 7.5e6, 0.12, "99926000", "Nifty 50"),
    "BANKNIFTY": UnderlyingSpec("BANKNIFTY", 56000.0, 0.011, 100.0, 30, 2.4e6, 0.14, "99926009", "Nifty Bank"),
}

REGIMES = ("trend_up", "trend_down", "range", "high_vol")
# Row = today's regime, column = tomorrow's. Persistent regimes give multi-day trends.
_TRANSITIONS = np.array([
    [0.70, 0.05, 0.20, 0.05],
    [0.05, 0.65, 0.20, 0.10],
    [0.15, 0.12, 0.65, 0.08],
    [0.12, 0.18, 0.30, 0.40],
])
_VOL_MULT = np.array([0.95, 1.05, 0.80, 1.70])
_DRIFT_SIGMAS = np.array([0.65, -0.65, 0.0, 0.0])   # intraday drift in units of the day's sigma
_GAP_SHARE = 0.25                                    # share of daily variance realised overnight
_RANGE_KAPPA = 0.015                                 # per-minute mean reversion on range days
_VOL_SCALE = 0.93                                    # calibration so realised daily vol ~= spec


def _spec(underlying: str) -> UnderlyingSpec:
    try:
        return SPECS[underlying.upper()]
    except KeyError as exc:
        raise ValueError(f"unknown underlying {underlying!r}; expected one of {sorted(SPECS)}") from exc


def trading_days(start: date, days: int, holidays: Iterable[date] | None = None) -> list[date]:
    """First ``days`` weekdays on/after ``start`` that are not holidays."""
    hol = set(holidays or ())
    out: list[date] = []
    d = start
    while len(out) < days:
        if d.weekday() < 5 and d not in hol:
            out.append(d)
        d += timedelta(days=1)
    return out


def _u_shape(n: int, a: float, ta: float, b: float, tb: float) -> np.ndarray:
    t = np.arange(n, dtype=float)
    return 1.0 + a * np.exp(-t / ta) + b * np.exp(-(n - 1 - t) / tb)


def _round_tick(x: np.ndarray) -> np.ndarray:
    return np.round(np.round(x / TICK) * TICK, 2)


def _simulate_minutes(spec: UnderlyingSpec, n_days: int, rng: np.random.Generator):
    """Return per-minute (open, high, low, close, volume) arrays of shape (n_days, 375) and regimes."""
    m = SESSION_MINUTES
    vol_w = _u_shape(m, 1.6, 25.0, 0.8, 30.0)
    vol_w = vol_w / vol_w.sum()
    vlm_w = _u_shape(m, 2.2, 20.0, 1.4, 25.0)
    vlm_w = vlm_w / vlm_w.sum()
    long_drift = 0.10 / 250  # ~10%/yr upward drift of Indian indices

    o = np.empty((n_days, m))
    h = np.empty((n_days, m))
    lo = np.empty((n_days, m))
    c = np.empty((n_days, m))
    v = np.empty((n_days, m))
    regimes: list[str] = []

    state = 2  # start in range
    prev_close = spec.base
    for d in range(n_days):
        if d > 0:
            state = int(rng.choice(4, p=_TRANSITIONS[state]))
        regimes.append(REGIMES[state])
        sigma_d = spec.daily_vol * _VOL_SCALE * _VOL_MULT[state] * float(np.exp(rng.normal(0, 0.2)))

        gap = rng.normal(0.0, math.sqrt(_GAP_SHARE) * sigma_d)
        if rng.random() < 0.03:  # rare event gap
            gap += rng.choice([-1.0, 1.0]) * abs(rng.normal(1.5, 0.5)) * sigma_d
        day_open = prev_close * math.exp(gap)

        sigma_intra = math.sqrt(1.0 - _GAP_SHARE) * sigma_d
        sig_m = sigma_intra * np.sqrt(vol_w)
        eps = rng.standard_t(5, size=m) / math.sqrt(5.0 / 3.0)
        drift_total = _DRIFT_SIGMAS[state] * sigma_intra + long_drift
        if REGIMES[state] == "high_vol":
            drift_total = rng.choice([-1.0, 1.0]) * 0.4 * sigma_intra
        rets = drift_total / m + sig_m * eps

        if REGIMES[state] == "range":
            x = 0.0
            logp = np.empty(m)
            for i in range(m):
                x = x * (1.0 - _RANGE_KAPPA) + rets[i]
                logp[i] = x
        else:
            logp = np.cumsum(rets)
        closes = day_open * np.exp(logp)
        opens = np.empty(m)
        opens[0] = day_open
        opens[1:] = closes[:-1]
        wick_hi = np.abs(rng.normal(0, 0.6, m)) * sig_m
        wick_lo = np.abs(rng.normal(0, 0.6, m)) * sig_m
        highs = np.maximum(opens, closes) * np.exp(wick_hi)
        lows = np.minimum(opens, closes) * np.exp(-wick_lo)

        day_vol = spec.daily_volume * float(np.exp(rng.normal(0, 0.3))) * _VOL_MULT[state] ** 0.7
        activity = (0.6 + 0.4 * np.abs(eps)) * np.exp(rng.normal(0, 0.35, m))
        vols = np.round(day_vol * vlm_w * activity)

        o[d], h[d], lo[d], c[d], v[d] = opens, highs, lows, closes, vols
        prev_close = float(closes[-1])

    o, h, lo, c = (_round_tick(a) for a in (o, h, lo, c))
    h = np.maximum(h, np.maximum(o, c))
    lo = np.minimum(lo, np.minimum(o, c))
    return o, h, lo, c, v, regimes


def generate_intraday(
    underlying: str = "NIFTY",
    days: int = 250,
    interval_min: int = 5,
    seed: int = 0,
    start: date = date(2025, 1, 1),
    holidays: Iterable[date] | None = None,
) -> pd.DataFrame:
    """Synthetic intraday candles (bar open time index, IST) for ``days`` trading days.

    ``df.attrs['regimes']`` maps ISO date -> regime name used to generate that day.
    """
    if interval_min < 1:
        raise ValueError("interval_min must be >= 1")
    spec = _spec(underlying)
    rng = np.random.default_rng(seed + (0 if spec.name == "NIFTY" else 7919))
    sessions = trading_days(start, days, holidays)
    o, h, lo, c, v, regimes = _simulate_minutes(spec, len(sessions), rng)

    m = SESSION_MINUTES
    n_buckets = math.ceil(m / interval_min)
    minute = np.tile(np.arange(m), len(sessions))
    day_idx = np.repeat(np.arange(len(sessions)), m)
    key = day_idx * n_buckets + minute // interval_min
    starts = np.flatnonzero(np.r_[True, key[1:] != key[:-1]])
    ends = np.r_[starts[1:] - 1, key.size - 1]
    of, hf, lf, cf, vf = (a.ravel() for a in (o, h, lo, c, v))

    day_open = pd.DatetimeIndex([datetime.combine(d, SESSION_START) for d in sessions])
    bar_ts = day_open[day_idx[starts]] + pd.to_timedelta((minute[starts] // interval_min) * interval_min,
                                                         unit="min")
    df = pd.DataFrame(
        {
            "open": of[starts],
            "high": np.maximum.reduceat(hf, starts),
            "low": np.minimum.reduceat(lf, starts),
            "close": cf[ends],
            "volume": np.add.reduceat(vf, starts),
        },
        index=bar_ts.tz_localize(IST),
    ).astype(float)
    df.attrs["regimes"] = {d.isoformat(): r for d, r in zip(sessions, regimes)}
    return df


def generate_daily(
    underlying: str = "NIFTY",
    days: int = 250,
    seed: int = 0,
    start: date = date(2025, 1, 1),
    holidays: Iterable[date] | None = None,
) -> pd.DataFrame:
    """Daily candles consistent with ``generate_intraday`` (same seed => same path). Index = midnight IST."""
    intra = generate_intraday(underlying, days, 15, seed, start, holidays)
    key = intra.index.normalize()
    out = intra.groupby(key).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    out.index.name = None
    out.attrs["regimes"] = intra.attrs.get("regimes", {})
    return out


# ----------------------------------------------------------------------------------------- options
def _bs_price(s: float, k: np.ndarray, t: float, r: float, sigma: np.ndarray, is_call: np.ndarray) -> np.ndarray:
    """Vectorised Black-Scholes (no dividends). Private helper; analytics live in ``indexedge.features``."""
    sq = sigma * math.sqrt(t)
    d1 = (np.log(s / k) + (r + 0.5 * sigma**2) * t) / sq
    d2 = d1 - sq
    disc = math.exp(-r * t)
    call = s * ndtr(d1) - k * disc * ndtr(d2)
    put = k * disc * ndtr(-d2) - s * ndtr(-d1)
    return np.where(is_call, call, put)


def _expiry_dt(expiry: date | datetime) -> datetime:
    if isinstance(expiry, datetime):
        return expiry if expiry.tzinfo else expiry.replace(tzinfo=IST)
    return datetime.combine(expiry, time(15, 30), IST)


def generate_option_chain(
    spot: float,
    expiry: date | datetime,
    now: datetime,
    seed: int = 0,
    underlying: str = "NIFTY",
    n_strikes: int = 25,
    r: float = 0.065,
    base_iv: float | None = None,
) -> pd.DataFrame:
    """Synthetic option chain in long format.

    Columns: ``strike, option_type ('CE'/'PE'), ltp, oi, oi_change, volume, iv, expiry, underlying``.
    IV follows a put-skewed smile; OI/volume are lot multiples concentrated on round strikes.
    """
    spec = _spec(underlying)
    rng = np.random.default_rng(seed)
    step = spec.strike_step
    now = now if now.tzinfo else now.replace(tzinfo=IST)
    t = max((_expiry_dt(expiry) - now).total_seconds() / (365.0 * 86400.0), 1.0 / (365.0 * 24 * 60))
    atm_iv = base_iv if base_iv is not None else spec.base_iv
    atm = round(spot / step) * step
    strikes = atm + step * np.arange(-n_strikes, n_strikes + 1)
    strikes = strikes[strikes > 0]

    k = np.repeat(strikes, 2)
    opt = np.tile(np.array(["CE", "PE"]), strikes.size)
    is_call = opt == "CE"
    z = np.clip(np.log(k / spot) / (atm_iv * math.sqrt(max(t, 1 / 365))), -4, 4)
    iv = atm_iv * (1.0 - 0.12 * z + 0.035 * z**2) * np.exp(rng.normal(0, 0.01, k.size))
    iv = np.clip(iv, 0.5 * atm_iv, 3.0 * atm_iv)
    ltp = np.maximum(_round_tick(_bs_price(spot, k, t, r, iv, is_call)), TICK)

    dist = (k - atm) / step
    centre = np.where(is_call, 3.0, -3.0)
    hump = np.exp(-(((dist - centre) / 7.0) ** 2))
    otm = np.where(is_call, dist >= 0, dist <= 0)
    weight = hump * np.where(otm, 1.0, 0.35) + 0.04
    big_round = np.isclose(np.mod(k, step * 10), 0)
    mid_round = np.isclose(np.mod(k, step * 2), 0)
    weight = weight * np.where(big_round, 2.2, np.where(mid_round, 1.3, 1.0))
    lot = spec.lot_size
    base_oi = lot * 150_000
    oi = np.round(base_oi * weight * np.exp(rng.normal(0, 0.2, k.size)) / lot) * lot
    oi_change = np.round(oi * rng.normal(0.05, 0.15, k.size) / lot) * lot
    vol_factor = 0.5 + 3.0 * np.exp(-(dist**2) / 20.0)
    volume = np.round(oi * vol_factor * np.exp(rng.normal(0, 0.3, k.size)) / lot) * lot

    exp_date = expiry.date() if isinstance(expiry, datetime) else expiry
    return pd.DataFrame(
        {
            "strike": k.astype(float),
            "option_type": opt,
            "ltp": ltp.astype(float),
            "oi": oi.astype(float),
            "oi_change": oi_change.astype(float),
            "volume": volume.astype(float),
            "iv": np.round(iv, 4),
            "expiry": exp_date,
            "underlying": spec.name,
        }
    )


# ------------------------------------------------------------------------------ instrument master
def _last_weekday_of_month(year: int, month: int, weekday: int = 1) -> date:
    last = date(year, month, calendar.monthrange(year, month)[1])
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _shift_for_holidays(d: date, holidays: set[date]) -> date:
    while d.weekday() >= 5 or d in holidays:
        d -= timedelta(days=1)
    return d


def _monthly_expiries(on: date, n: int, holidays: set[date]) -> list[date]:
    out: list[date] = []
    y, mth = on.year, on.month
    while len(out) < n:
        e = _shift_for_holidays(_last_weekday_of_month(y, mth), holidays)
        if e >= on:
            out.append(e)
        mth += 1
        if mth > 12:
            y, mth = y + 1, 1
    return out


def _weekly_expiries(on: date, n: int, holidays: set[date]) -> list[date]:
    first = on + timedelta(days=(1 - on.weekday()) % 7)  # next Tuesday (or today)
    out = []
    for i in range(n + 1):
        e = _shift_for_holidays(first + timedelta(weeks=i), holidays)
        if e >= on:
            out.append(e)
    return out[:n]


def _fmt_expiry(d: date) -> str:
    return d.strftime("%d%b%Y").upper()


def synthetic_master_rows(
    on: date,
    holidays: Iterable[date] | None = None,
    spots: dict[str, float] | None = None,
    n_strikes: int = 30,
    weeklies: int = 4,
    monthlies: int = 3,
) -> list[dict]:
    """Instrument-master rows in OpenAPIScripMaster.json format (all values are strings).

    NIFTY: weekly Tuesday expiries + monthly (last Tuesday), lot 65, strike step 50.
    BANKNIFTY: monthly (last Tuesday) only, lot 30, strike step 100. Tuesday holidays shift to the previous
    trading day. Includes AMXIDX index rows for NIFTY, BANKNIFTY and INDIA VIX.
    """
    hol = set(holidays or ())
    spots = spots or {}
    rows: list[dict] = [
        {"token": "99926000", "symbol": "Nifty 50", "name": "NIFTY", "expiry": "", "strike": "0.000000",
         "lotsize": "1", "instrumenttype": "AMXIDX", "exch_seg": "NSE", "tick_size": "0.000000"},
        {"token": "99926009", "symbol": "Nifty Bank", "name": "BANKNIFTY", "expiry": "", "strike": "0.000000",
         "lotsize": "1", "instrumenttype": "AMXIDX", "exch_seg": "NSE", "tick_size": "0.000000"},
        {"token": "99926017", "symbol": "India VIX", "name": "INDIA VIX", "expiry": "", "strike": "0.000000",
         "lotsize": "1", "instrumenttype": "AMXIDX", "exch_seg": "NSE", "tick_size": "0.000000"},
    ]
    token = 35000
    freeze = {"NIFTY": "1800", "BANKNIFTY": "600"}
    for name in ("NIFTY", "BANKNIFTY"):
        spec = SPECS[name]
        monthly = _monthly_expiries(on, monthlies, hol)
        option_exps = sorted(set(monthly) | (set(_weekly_expiries(on, weeklies, hol)) if name == "NIFTY"
                                             else set()))
        for e in monthly:
            token += 1
            rows.append({
                "token": str(token), "symbol": f"{name}{e.strftime('%d%b%y').upper()}FUT", "name": name,
                "expiry": _fmt_expiry(e), "strike": "-1.000000", "lotsize": str(spec.lot_size),
                "instrumenttype": "FUTIDX", "exch_seg": "NFO", "tick_size": "5.000000",
                "freeze_qty": freeze[name],
            })
        spot = spots.get(name, spec.base)
        atm = round(spot / spec.strike_step) * spec.strike_step
        for e in option_exps:
            for i in range(-n_strikes, n_strikes + 1):
                strike = atm + i * spec.strike_step
                for ot in ("CE", "PE"):
                    token += 1
                    rows.append({
                        "token": str(token),
                        "symbol": f"{name}{e.strftime('%d%b%y').upper()}{int(strike)}{ot}",
                        "name": name, "expiry": _fmt_expiry(e), "strike": f"{strike * 100:.6f}",
                        "lotsize": str(spec.lot_size), "instrumenttype": "OPTIDX", "exch_seg": "NFO",
                        "tick_size": "5.000000", "freeze_qty": freeze[name],
                    })
    return rows
