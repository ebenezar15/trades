"""Signal composition (spec §7): ``Signal = StrategyTrigger ∧ ContextGate ∧ NewsGate ∧ RegimeFilter``.

Gates are evaluated in this order; the first failing gate sets ``status='blocked'`` and ``blocked_reason``
(the signal is still fully composed so the user can study it):

1. ``"External context unavailable or stale: <inputs>"`` — no snapshot, or required inputs stale.
2. ``"Risk-off: <reasons>"`` — context engine flagged risk-off (VIX spike / strongly negative cues).
3. ``"Event blackout: <event>"`` — a scheduled high-impact event window (news calendar).
4. ``"Regime <r> not suitable for <strategy>"`` — the strategy declares the regimes it works in.
5. ``"Strategy not validated for live signals"`` — ``settings.strategies.enabled[id] is False``.

Probability = calibrated model P(win) when a *usable* model exists for the strategy (``win`` = final target hit
before the stop), otherwise the strategy's historical win rate with a Wilson 95 % CI, and ``None`` ("insufficient
data") when fewer than ``settings.strategies.min_trades_any`` trades exist. Probabilities are never constants.

EV per trade (₹, for the suggested quantity, one lot if the size is zero) = p·reward − (1−p)·risk − costs, with
reward measured to the *final* target (consistent with the label) and costs from the cost function.

Instrument: with ``execution.preferred_instrument == 'option'`` the ATM (or ITM1/OTM1) CE for longs / PE for
shorts of the nearest expiry (rolled to the next expiry on expiry day, except straddles); otherwise the nearest
future. Option risk is approximated with **delta ≈ 0.5**: one index point ≈ ₹0.5 of premium, so sizing uses
``point_value=0.5`` and premium stop/targets are ``premium ∓ 0.5·|level − entry|`` (see
:mod:`indexedge.execution.trades`). The premium used for cost estimates is a rough ATM approximation
(``0.4·S·σ·√T``) unless the caller passes the live premium.
"""
from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from indexedge.core.clock import IST
from indexedge.core.types import (
    ContextSnapshot,
    Instrument,
    InstrumentKind,
    NewsItem,
    OptionType,
    Regime,
    Side,
    Signal,
    StrategySignal,
    new_id,
)
from indexedge.models.calibration import wilson_ci
from indexedge.signals.costs import CostFn, default_cost_fn

log = logging.getLogger(__name__)

OPTION_DELTA = 0.5
DEFAULT_IV = 0.13
ALIGN_THRESHOLD = 0.1


def _regime(r: Any) -> Regime:
    if isinstance(r, Regime):
        return r
    try:
        return Regime(str(r))
    except ValueError:
        return Regime.UNKNOWN


def _fmt_bias(x: float) -> str:
    return f"{x:+.2f}"


class SignalComposer:
    def __init__(
        self,
        settings: Any,
        registry: Any = None,
        risk: Any = None,
        calendar: Any = None,
        master: Any = None,
        *,
        strategies: Mapping[str, Any] | None = None,
        cost_fn: CostFn | None = None,
    ) -> None:
        self.settings = settings
        self.registry = registry
        self.risk = risk
        self.calendar = calendar
        self.master = master
        self.strategies = dict(strategies or {})
        self.cost_fn = cost_fn or default_cost_fn()

    # ------------------------------------------------------------------------------------------ public
    def compose(
        self,
        raw: StrategySignal,
        feat_row: Any = None,
        context: ContextSnapshot | None = None,
        stats: dict[str, Any] | None = None,
        regime: Regime | str = Regime.UNKNOWN,
        *,
        news: list[NewsItem] | None = None,
        premium: float | None = None,
        now: datetime | None = None,
    ) -> Signal:
        regime = _regime(regime)
        at = raw.ts if raw.ts is not None else (now or datetime.now(IST))
        strategy = self.strategies.get(raw.strategy_id)
        name = getattr(strategy, "name", None) or raw.strategy_id

        instrument, inst_note = self._instrument(raw, at.date())
        is_option = instrument is not None and instrument.kind is InstrumentKind.OPTION
        point_value = OPTION_DELTA if is_option else 1.0
        lot = self._lot_size(raw.underlying, instrument, at.date())

        qty = 0
        if self.risk is not None and lot > 0:
            qty = int(self.risk.size_position(raw.entry, raw.stop, lot, point_value=point_value))

        prob, ci, n, top, prob_src = self._probability(raw, feat_row, stats or {})
        reward_pts = abs(raw.targets[-1] - raw.entry) if raw.targets else 0.0
        risk_pts = raw.risk_points
        expected_rr = reward_pts / risk_pts if risk_pts > 0 else 0.0
        ev = self._ev(prob, reward_pts, risk_pts, qty or lot, point_value, raw, instrument, premium, at)

        reasons = list(raw.reasons)
        if prob_src:
            reasons.append(prob_src)
        if inst_note:
            reasons.append(inst_note)
        if qty == 0 and lot > 0 and self.risk is not None:
            reasons.append("Stop too wide for one lot at the configured risk per trade (EV shown per lot)")

        context_reasons = self._context_reasons(raw.side, context)
        news_reasons = self._news_reasons(news, at)

        badges = list(dict.fromkeys((stats or {}).get("badges") or []))
        if prob is None and "insufficient_data" not in badges:
            badges.append("insufficient_data")
        if prob is not None and ev < 0 and "negative_expectancy_warning" not in badges:
            badges.append("negative_expectancy_warning")

        blocked = self._gate(raw, context, regime, strategy, name, at)
        return Signal(
            id=new_id("sig_"),
            raw=raw,
            probability=None if prob is None else round(prob, 4),
            probability_ci=None if ci is None else (round(ci[0], 4), round(ci[1], 4)),
            sample_size=int(n),
            expected_rr=round(expected_rr, 2),
            ev_per_trade=round(ev, 2),
            quantity=qty,
            instrument=instrument,
            badges=badges,
            reasons=reasons,
            context_reasons=context_reasons,
            news_reasons=news_reasons,
            top_features=top,
            regime=regime,
            status="blocked" if blocked else "active",
            blocked_reason=blocked,
            created_at=at,
        )

    # ------------------------------------------------------------------------------------------ gates
    def _gate(self, raw: StrategySignal, context: ContextSnapshot | None, regime: Regime, strategy: Any,
              name: str, at: datetime) -> str:
        if context is None or not context.usable:
            inputs = ", ".join(context.stale_required) if context is not None else "no context snapshot"
            return f"External context unavailable or stale: {inputs}"
        if context.risk_off:
            why = "; ".join(context.reasons) if context.reasons else "risk-off conditions"
            return f"Risk-off: {why}"
        if self.calendar is not None:
            ev = self.calendar.in_blackout(at)
            if ev is not None:
                return f"Event blackout: {ev.title}"
        regimes = getattr(strategy, "regimes", None)
        if regimes and regime not in {_regime(r) for r in regimes}:
            return f"Regime {regime.value} not suitable for {name}"
        enabled = self.settings.strategies.enabled.get(raw.strategy_id)
        if enabled is False:
            return "Strategy not validated for live signals"
        return ""

    # ------------------------------------------------------------------------------------ probability
    def _probability(
        self, raw: StrategySignal, feat_row: Any, stats: dict[str, Any]
    ) -> tuple[float | None, tuple[float, float] | None, int, list[tuple[str, float]], str]:
        min_n = int(self.settings.strategies.min_trades_any)
        bundle = None
        if self.registry is not None:
            try:
                bundle = self.registry.get(raw.strategy_id)
            except Exception:
                log.exception("model registry lookup failed for %s", raw.strategy_id)
        row = feat_row if feat_row is not None else (raw.features or None)
        if bundle is not None and getattr(bundle, "usable", False) and row is not None:
            try:
                p = float(bundle.predict_proba(row)[0])
                n = int((bundle.metrics or {}).get("n", 0))
                if math.isfinite(p) and n >= min_n:
                    top: list[tuple[str, float]] = []
                    try:
                        top = [(str(f), float(v)) for f, v in bundle.top_features(row, k=5)]
                    except Exception:
                        log.debug("top_features failed for %s", raw.strategy_id, exc_info=True)
                    ci = wilson_ci(p * n, n)
                    return p, ci, n, top, f"Calibrated model probability ({n} historical setups)"
            except Exception:
                log.exception("model prediction failed for %s", raw.strategy_id)
        n = int(stats.get("n") or 0)
        wins = stats.get("wins")
        if wins is None and stats.get("win_rate") is not None:
            wins = float(stats["win_rate"]) * n
        if n < min_n or wins is None:
            return None, None, n, [], f"Insufficient data: {n} historical trades (< {min_n})"
        p = float(wins) / n
        return p, wilson_ci(float(wins), n), n, [], f"Historical win rate over {n} trades (no usable model)"

    # -------------------------------------------------------------------------------------------- EV
    def _ev(self, p: float | None, reward_pts: float, risk_pts: float, qty: int, pv: float,
            raw: StrategySignal, inst: Instrument | None, premium: float | None, at: datetime) -> float:
        if p is None or qty <= 0:
            return 0.0
        reward = reward_pts * qty * pv
        risk = risk_pts * qty * pv
        if inst is not None and inst.kind is InstrumentKind.OPTION:
            prem = premium if premium and premium > 0 else self._premium_estimate(raw, inst, at)
            costs = self.cost_fn("option", qty, prem, max(prem + pv * reward_pts, 0.05))
        else:
            exit_px = raw.targets[-1] if raw.targets else raw.entry
            costs = self.cost_fn("future", qty, raw.entry, exit_px)
        return p * reward - (1 - p) * risk - float(costs)

    @staticmethod
    def _premium_estimate(raw: StrategySignal, inst: Instrument, at: datetime) -> float:
        iv = raw.features.get("atm_iv") or raw.features.get("iv") or DEFAULT_IV
        if iv > 1.5:  # given in percent
            iv /= 100.0
        days = max(((inst.expiry or at.date()) - at.date()).days, 0) + 0.5
        return max(0.4 * raw.entry * float(iv) * math.sqrt(days / 365.0), 1.0)

    # ------------------------------------------------------------------------------------ instrument
    def _instrument(self, raw: StrategySignal, on: date) -> tuple[Instrument | None, str]:
        if self.master is None:
            return None, ""
        u = raw.underlying.upper()
        pref = str(self.settings.execution.preferred_instrument).lower()
        try:
            if pref == "option":
                exps = self.master.expiries(u, on)
                if raw.instrument_hint != "straddle":
                    exps = [e for e in exps if e > on] or exps   # no directional option buys on expiry day
                if not exps:
                    raise KeyError(f"no option expiries for {u}")
                expiry = exps[0]
                strike = self.master.atm_strike(u, raw.entry, expiry)
                opt = OptionType.CE if raw.side is Side.BUY else OptionType.PE
                shift = {"ITM1": -1, "OTM1": 1}.get(str(self.settings.execution.option_moneyness).upper(), 0)
                if shift:
                    step = self.master.strike_step(u, expiry)
                    strike += shift * step * (1 if opt is OptionType.CE else -1)
                return self.master.option(u, expiry, strike, opt), ""
            fut = self.master.nearest_future(u, on)
            if fut is None:
                raise KeyError(f"no live future for {u}")
            return fut, ""
        except KeyError as exc:
            return None, f"No tradeable instrument found ({exc.args[0] if exc.args else exc})"

    def _lot_size(self, underlying: str, inst: Instrument | None, on: date) -> int:
        if inst is not None:
            return inst.lot_size
        if self.master is not None:
            try:
                return int(self.master.lot_size(underlying, on))
            except KeyError:
                pass
        return 1

    # ------------------------------------------------------------------------------------- reasons
    @staticmethod
    def _context_reasons(side: Side, ctx: ContextSnapshot | None) -> list[str]:
        if ctx is None:
            return ["External context unavailable"]
        bias = float(ctx.macro_bias)
        direction = "long" if side is Side.BUY else "short"
        if abs(bias) < ALIGN_THRESHOLD:
            head = f"Global cues neutral ({_fmt_bias(bias)})"
        else:
            mood = "bullish" if bias > 0 else "bearish"
            aligned = (bias > 0) == (side is Side.BUY)
            head = f"Global cues {mood} ({_fmt_bias(bias)}) — {'aligned with' if aligned else 'against'} {direction}"
        out = [head]
        if ctx.event_risk >= 0.3:
            out.append(f"Event risk elevated ({ctx.event_risk:.2f})")
        return out + list(ctx.reasons)

    @staticmethod
    def _news_reasons(news: list[NewsItem] | None, at: datetime) -> list[str]:
        out = []
        for item in news or []:
            if item.relevance < 0.5 or item.ts > at:
                continue
            tone = "positive" if item.sentiment > 0.2 else "negative" if item.sentiment < -0.2 else "neutral"
            out.append(f"{item.impact.capitalize()}-impact news ({tone}): {item.title}")
        return out[:5]
