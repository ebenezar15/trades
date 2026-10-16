"""Premium-selling strategies modelled on the underlying: ``straddle_920`` and ``expiry_straddle``.

Intraday option prices are not in the candle history, so both strategies use a **simplified premium model**
(documented approximation, ``backtest_quality = "approximate"``):

* ATM time value ``tv = sqrt(2/pi) * S * iv * sqrt(t)`` (the Brenner-Subrahmanyam ATM straddle
  approximation), ``t`` = calendar days to expiry plus the remaining fraction of today's session, / 365.
* Straddle value at spot ``S`` and strike ``K``: ``V = sqrt(tv^2 + (S - K)^2)`` — time value at the money,
  intrinsic value far from it. Call leg ``(V + (S-K))/2``, put leg ``(V - (S-K))/2``.
* ``iv`` is a parameter (default 12% Nifty / 14% Bank Nifty) or ``extra["iv"]``; no smile, no IV changes.
* Bar extremes are checked for stops (call leg at the bar high, put leg at the bar low); stops fill at the
  trigger + 2% (SL-limit buffer) or at the bar open when the open is already beyond.

The resulting P&L is in **premium points per unit**. The raw signal still carries underlying-point levels
for the UI: ``stop`` is the approximate spot level at which a stop triggers (upside; the downside is
symmetric), ``targets`` is empty, and ``instrument_hint="straddle"``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, ClassVar

import numpy as np
import pandas as pd

from indexedge.backtest.simulate import TradeOutcome
from indexedge.core.types import Regime, Side, StrategySignal
from indexedge.strategies.base import BarView, Strategy, fmt, hhmm_to_min

SQRT_2_PI = math.sqrt(2.0 / math.pi)
SESSION_MIN = 375.0
CLOSE_MIN = 15 * 60 + 30
STRIKE_STEP = {"NIFTY": 50.0, "BANKNIFTY": 100.0}


@dataclass
class PremiumOutcome(TradeOutcome):
    """TradeOutcome in premium points plus the individual option legs for cost calculation."""

    legs: list[tuple[float, float, str]] = field(default_factory=list)   # (entry_px, exit_px, entry_side)
    spot_entry: float = 0.0
    risk_points: float = 0.0       # premium points that define 1R


def time_to_expiry(at: datetime, expiry: date) -> float:
    """Years to expiry: whole calendar days + remaining fraction of today's session (min 1 minute)."""
    mins = at.hour * 60 + at.minute
    rem = max(CLOSE_MIN - mins, 1) / SESSION_MIN
    days = max((expiry - at.date()).days, 0)
    return (days + rem) / 365.0


def leg_values(s: float | np.ndarray, k: float, tv: float) -> tuple[Any, Any]:
    v = np.sqrt(tv * tv + (s - k) ** 2)
    return (v + (s - k)) / 2.0, (v - (s - k)) / 2.0


def atm_tv(spot: float, iv: float, t: float) -> float:
    return SQRT_2_PI * spot * iv * math.sqrt(max(t, 1e-9))


def _parse_dates(v: Any) -> set[date] | None:
    if not v:
        return None
    out = set()
    for d in v:
        out.add(d if isinstance(d, date) else date.fromisoformat(str(d)))
    return out


class _PremiumStrategy(Strategy):
    instrument_hint = "straddle"
    backtest_quality = "approximate"
    regimes: ClassVar[set[Regime] | None] = {Regime.RANGE}

    def _expiry_for(self, d: date) -> date:
        dates = _parse_dates(self.params.get("expiry_dates"))
        if dates:
            nxt = sorted(x for x in dates if x >= d)
            if nxt:
                return nxt[0]
        wd = int(self.params.get("expiry_weekday", 1))
        return d + timedelta(days=(wd - d.weekday()) % 7)

    def _iv(self, underlying: str, extra: dict[str, Any] | None) -> float:
        if extra and extra.get("iv"):
            return float(extra["iv"])
        return float(self._param("iv", underlying))

    def _strike(self, spot: float, underlying: str) -> float:
        step = STRIKE_STEP.get(underlying, 50.0)
        return round(spot / step) * step

    def info(self) -> dict[str, Any]:
        out = super().info()
        ed = _parse_dates(out["params"].get("expiry_dates"))
        out["params"]["expiry_dates"] = sorted(d.isoformat() for d in ed) if ed else None
        return out


LEARN_920 = """\
### 9:20 Short Straddle (paper-only / approximate backtest)

**What it is.** At 09:20 sell the at-the-money call and put of the current Nifty weekly expiry. Each leg
has its
own stop-loss at +25% of its entry premium. If one leg is stopped, the other leg's stop moves to its entry
price. Everything is closed at 15:15.

**Why it can work.** Option sellers collect time decay (theta). On quiet days both legs lose value and the
trade keeps most of the premium.

**When it fails.** Trend days: one leg is stopped, the market reverses, and the second leg is stopped too.
Average losses are typically **2–3× the average win**; long losing streaks (100+ days) have been reported.
Event days (RBI policy, Budget, results) are skipped.

**Rules (defaults).** Entry 09:20, ATM = strike nearest spot, per-leg SL 25% (SL-limit, 2% buffer), surviving
leg SL to cost after the first stop, hard exit 15:15, no re-entry.

**Backtest quality: approximate.** History has no option prices, so premiums come from a simple model
(ATM time value ≈ 0.8 × spot × IV × √t, no volatility smile or IV changes). The ₹ figures assume one lot per
leg. Use it to understand the *shape* of the payoff, not as proof of an edge. **Paper-only.**
"""

LEARN_EXPIRY = """\
### Expiry-day Iron Fly (paper-only / approximate backtest)

**What it is.** On weekly-expiry day only, at 10:00 sell the ATM call + put and buy protective wings at
± the combined premium (an iron fly). Time decay is fastest on expiry day.

**Why it can work.** Expiring options lose their remaining time value within hours; if Nifty stays near the
strike the fly expires close to maximum profit.

**When it fails.** Gamma risk is extreme on expiry day: a 0.5–1% move can turn a winner into a full loss
within
minutes. Event days and trend days hurt most.

**Rules (defaults).** Expiry days only (NSE calendar; default Tuesdays). Entry 10:00. Wings at ±1.0× the
combined straddle premium. Stop when the fly's value rises 25% above the entry credit, **or** spot moves
beyond
strike ± 0.8 × the entry straddle premium. Once +30% in profit, trail the stop to lock +10%. Exit 15:15.

**Backtest quality: approximate.** Premiums are modelled (see the 9:20 straddle). Disabled unless expiry
strategies are switched on in settings. **Paper-only.**
"""


class Straddle920(_PremiumStrategy):
    id = "straddle_920"
    name = "9:20 Short Straddle"
    description = "Sell the ATM straddle at 09:20 with 25% per-leg stops; exit 15:15 (modelled premiums)."
    default_params: ClassVar[dict[str, Any]] = {
        "entry_time": "09:20",
        "leg_sl_pct": 25.0,
        "sl_buffer_pct": 2.0,
        "move_to_cost": True,
        "iv": {"NIFTY": 0.12, "BANKNIFTY": 0.14},
        "expiry_dates": None,
        "expiry_weekday": 1,
    }
    learn = LEARN_920

    def _scan(
        self, feat: pd.DataFrame, underlying: str, extra: dict[str, Any] | None
    ) -> list[StrategySignal]:
        p = self.params
        bv = BarView(feat)
        c = bv["close"]
        when = hhmm_to_min(p["entry_time"])
        skip = _parse_dates((extra or {}).get("skip_days")) or set()
        iv = self._iv(underlying, extra)
        out: list[StrategySignal] = []
        for d, s, e in bv.day_slices():
            i = next((k for k in range(s, e) if bv.close_min[k] == when), None)
            if i is None or d in skip or not np.isfinite(c[i]):
                continue
            spot = float(c[i])
            k = self._strike(spot, underlying)
            exp = self._expiry_for(d)
            at = bv.index[i].to_pydatetime() + timedelta(minutes=bv.step)
            tv = atm_tv(spot, iv, time_to_expiry(at, exp))
            ce, pe = leg_values(spot, k, tv)
            band = (p["leg_sl_pct"] / 100.0) * (ce + pe)        # ≈ spot move that stops a leg (delta ~0.5)
            reasons = [
                (f"{p['entry_time']} entry: sell {fmt(k, 0)} CE + PE (expiry {exp.isoformat()},"
                f" {(exp - d).days} day(s) away)"),
                (f"Modelled premiums CE {fmt(ce)} + PE {fmt(pe)} = {fmt(ce + pe)} (IV {iv * 100:.0f}%,"
                f" approximate)"),
                (f"Per-leg stop at +{p['leg_sl_pct']:.0f}% premium ≈ spot {fmt(spot - band)} /"
                f" {fmt(spot + band)}"),
                "Paper-only: approximate premium model, not real option prices",
            ]
            out.append(self._signal(bv, i, underlying, Side.SELL, spot, spot + band, [], reasons))
        return out

    def simulate(
        self, signal: StrategySignal, bars: pd.DataFrame, max_bars: int = 75
    ) -> PremiumOutcome | None:
        if bars is None or bars.empty:
            return None
        p = self.params
        bars = bars[bars.index.date == bars.index[0].date()]
        spot = signal.entry
        k = self._strike(spot, signal.underlying)
        exp = self._expiry_for(signal.ts.date())
        iv = float(self._param("iv", signal.underlying))
        step = int((bars.index[1] - bars.index[0]).total_seconds() // 60) if len(bars) > 1 else 5
        at0 = signal.ts + timedelta(minutes=step)
        ce0, pe0 = leg_values(spot, k, atm_tv(spot, iv, time_to_expiry(at0, exp)))
        credit = ce0 + pe0
        sl_mult = 1 + p["leg_sl_pct"] / 100.0
        buf = 1 + p["sl_buffer_pct"] / 100.0
        stops = {"ce": ce0 * sl_mult, "pe": pe0 * sl_mult}
        closed: dict[str, float] = {}
        t_exit = hhmm_to_min(signal.time_exit or "15:15")
        mfe = mae = 0.0
        last_ts, n = bars.index[0], 0
        cur = {"ce": ce0, "pe": pe0}
        for n, (ts, bar) in enumerate(bars.iterrows(), start=1):
            last_ts = ts
            tv = atm_tv(spot, iv, time_to_expiry(ts.to_pydatetime() + timedelta(minutes=step), exp))
            ce_hi, _ = leg_values(float(bar["high"]), k, tv)
            _, pe_lo = leg_values(float(bar["low"]), k, tv)
            ce_o, pe_o = leg_values(float(bar["open"]), k, tv)
            for leg, worst, opn in (("ce", ce_hi, ce_o), ("pe", pe_lo, pe_o)):
                if leg in closed or worst < stops[leg]:
                    continue
                closed[leg] = max(stops[leg] * buf, opn)
                other = "pe" if leg == "ce" else "ce"
                if p["move_to_cost"] and other not in closed:
                    stops[other] = min(stops[other], ce0 if other == "ce" else pe0)
            ce_c, pe_c = leg_values(float(bar["close"]), k, tv)
            cur = {"ce": closed.get("ce", ce_c), "pe": closed.get("pe", pe_c)}
            pnl_now = credit - cur["ce"] - cur["pe"]
            mfe, mae = max(mfe, pnl_now), max(mae, -pnl_now)
            if len(closed) == 2:
                return self._outcome(signal, bars, ts, credit, cur, ce0, pe0, "stop", mfe, mae, n, spot)
            if ts.hour * 60 + ts.minute + step >= t_exit:
                return self._outcome(signal, bars, ts, credit, cur, ce0, pe0, "time", mfe, mae, n, spot)
        outcome = "stop" if closed else "eod"
        return self._outcome(signal, bars, last_ts, credit, cur, ce0, pe0, outcome, mfe, mae, n, spot)

    def _outcome(self, signal, bars, ts, credit, cur, ce0, pe0, outcome, mfe, mae, n, spot) -> PremiumOutcome:
        debit = cur["ce"] + cur["pe"]
        pts = credit - debit
        risk = credit * self.params["leg_sl_pct"] / 100.0
        return PremiumOutcome(bars.index[0], credit, ts, debit, outcome, pts, pts / risk if risk else 0.0,
                              mfe, mae, n, legs=[(ce0, cur["ce"], "SELL"), (pe0, cur["pe"], "SELL")],
                              spot_entry=spot, risk_points=risk)


class ExpiryStraddle(_PremiumStrategy):
    id = "expiry_straddle"
    name = "Expiry-day Iron Fly"
    description = "On expiry day sell the 10:00 ATM straddle with wings (iron fly); premium-based stops."
    default_params: ClassVar[dict[str, Any]] = {
        "entry_time": "10:00",
        "wing_mult": 1.0,
        "sl_pct": 25.0,
        "spot_band_mult": 0.8,
        "lock_trigger_pct": 30.0,
        "lock_pct": 10.0,
        "sl_buffer_pct": 2.0,
        "iv": {"NIFTY": 0.12, "BANKNIFTY": 0.14},
        "expiry_dates": None,
        "expiry_weekday": 1,
    }
    learn = LEARN_EXPIRY

    def _fly_value(self, s: float, k: float, w: float, tv: float) -> float:
        ce, pe = leg_values(s, k, tv)
        wc, _ = leg_values(s, k + w, tv)
        _, wp = leg_values(s, k - w, tv)
        return float(ce + pe - wc - wp)

    def _setup(
        self, spot: float, at: datetime, underlying: str, iv: float
    ) -> tuple[float, float, float, float]:
        k = self._strike(spot, underlying)
        exp = self._expiry_for(at.date())
        tv = atm_tv(spot, iv, time_to_expiry(at, exp))
        ce, pe = leg_values(spot, k, tv)
        step = STRIKE_STEP.get(underlying, 50.0)
        w = max(step, round(self.params["wing_mult"] * (ce + pe) / step) * step)
        return k, float(ce + pe), w, tv

    def _scan(
        self, feat: pd.DataFrame, underlying: str, extra: dict[str, Any] | None
    ) -> list[StrategySignal]:
        p = self.params
        bv = BarView(feat)
        c = bv["close"]
        when = hhmm_to_min(p["entry_time"])
        iv = self._iv(underlying, extra)
        skip = _parse_dates((extra or {}).get("skip_days")) or set()
        out: list[StrategySignal] = []
        for d, s, e in bv.day_slices():
            if self._expiry_for(d) != d or d in skip:
                continue
            i = next((k for k in range(s, e) if bv.close_min[k] == when), None)
            if i is None or not np.isfinite(c[i]):
                continue
            spot = float(c[i])
            at = bv.index[i].to_pydatetime() + timedelta(minutes=bv.step)
            k, straddle, w, tv = self._setup(spot, at, underlying, iv)
            credit = self._fly_value(spot, k, w, tv)
            band = p["spot_band_mult"] * straddle
            reasons = [
                (f"Expiry day ({d.isoformat()}): sell {fmt(k, 0)} CE + PE at {p['entry_time']},"
                f" buy wings {fmt(k - w, 0)} PE / {fmt(k + w, 0)} CE"),
                (f"Modelled straddle premium {fmt(straddle)}, iron-fly credit {fmt(credit)} (IV"
                 f" {iv * 100:.0f}%,"
                " approximate)"),
                (f"Stop if the fly costs +{p['sl_pct']:.0f}% more than the credit or spot leaves"
                f" {fmt(k - band)}–{fmt(k + band)}"),
                "Paper-only: approximate premium model, not real option prices",
            ]
            out.append(self._signal(bv, i, underlying, Side.SELL, spot, k + band, [], reasons))
        return out

    def simulate(
        self, signal: StrategySignal, bars: pd.DataFrame, max_bars: int = 75
    ) -> PremiumOutcome | None:
        if bars is None or bars.empty:
            return None
        p = self.params
        bars = bars[bars.index.date == bars.index[0].date()]
        step = int((bars.index[1] - bars.index[0]).total_seconds() // 60) if len(bars) > 1 else 5
        iv = float(self._param("iv", signal.underlying))
        spot = signal.entry
        at0 = signal.ts + timedelta(minutes=step)
        k, straddle, w, _tv = self._setup(spot, at0, signal.underlying, iv)
        exp = self._expiry_for(signal.ts.date())
        credit = self._fly_value(spot, k, w, atm_tv(spot, iv, time_to_expiry(at0, exp)))
        wing0 = max(straddle - credit, 0.05)
        stop_val = credit * (1 + p["sl_pct"] / 100.0)
        band = p["spot_band_mult"] * straddle
        buf = 1 + p["sl_buffer_pct"] / 100.0
        t_exit = hhmm_to_min(signal.time_exit or "15:15")
        mfe = mae = 0.0
        locked = False
        val, ts, n = credit, bars.index[0], 0
        for n, (ts, bar) in enumerate(bars.iterrows(), start=1):
            tv = atm_tv(spot, iv, time_to_expiry(ts.to_pydatetime() + timedelta(minutes=step), exp))
            hi, lo, cl = float(bar["high"]), float(bar["low"]), float(bar["close"])
            worst = max(self._fly_value(hi, k, w, tv), self._fly_value(lo, k, w, tv))
            if worst >= stop_val:
                val = max(stop_val * buf, self._fly_value(float(bar["open"]), k, w, tv))
                val = min(val, 2 * w)
                return self._finish(signal, bars, ts, credit, val, "stop", mfe, mae, n, spot, wing0)
            if hi >= k + band or lo <= k - band:
                edge = k + band if hi >= k + band else k - band
                val = min(self._fly_value(edge, k, w, tv) * buf, 2 * w)
                return self._finish(signal, bars, ts, credit, val, "stop", mfe, mae, n, spot, wing0)
            val = self._fly_value(cl, k, w, tv)
            mfe, mae = max(mfe, credit - val), max(mae, val - credit)
            if not locked and credit - val >= credit * p["lock_trigger_pct"] / 100.0:
                locked = True
                stop_val = credit * (1 - p["lock_pct"] / 100.0)
            if ts.hour * 60 + ts.minute + step >= t_exit:
                return self._finish(signal, bars, ts, credit, val, "time", mfe, mae, n, spot, wing0)
        return self._finish(signal, bars, ts, credit, val, "eod", mfe, mae, n, spot, wing0)

    def _finish(self, signal, bars, ts, credit, val, outcome, mfe, mae, n, spot, wing0) -> PremiumOutcome:
        pts = credit - val
        risk = credit * self.params["sl_pct"] / 100.0
        # legs for charges (approximation): two short ATM legs, two long wings bought at entry and assumed
        # to be
        # sold back near zero (expiry day)
        wing = wing0 / 2
        short = (credit + 2 * wing) / 2
        legs = [(short, val / 2 + 0.05, "SELL"), (short, val / 2 + 0.05, "SELL"),
                (wing, 0.05, "BUY"), (wing, 0.05, "BUY")]
        return PremiumOutcome(bars.index[0], credit, ts, val, outcome, pts, pts / risk if risk else 0.0,
                              mfe, mae, n, legs=legs, spot_entry=spot, risk_points=risk)
