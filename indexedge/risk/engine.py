"""Pre-trade risk engine (spec §8). Every order passes ``RiskEngine.check`` before it is sent.

Rules (all evaluated; every failure is reported as a human-readable reason):

1. **Day lock** — once locked (kill switch, max daily loss, max trades, max consecutive losses) no new
   trades for the rest of the IST day. The lock is persisted in the journal ``settings`` table under
   ``risk_lock:<date>`` so restarts keep it; it lapses automatically the next day.
2. **Session window** — trading day only; no entries in the first ``no_trade_first_min`` minutes after 09:15
   (entries allowed from exactly 09:15 + N), none in the last ``no_trade_last_min`` minutes before the broker
   ``cutoff`` (default 15:20 ⇒ last entry strictly before 15:00), none at/after the cutoff.
3. **Event blackout** and **stale feed** (``feed_age_s > stale_feed_seconds``; unknown age counts as stale).
4. **Daily loss** — realised net P&L ≤ −max_daily_loss ⇒ lock. Realised + open unrealised ≤ −limit ⇒ reject.
   A new trade's risk may not exceed the remaining daily loss budget.
5. **Max trades/day** and **max consecutive losses** ⇒ lock.
6. **Max open positions**, **per-index lot exposure** (``max_lots_per_index``).
7. **Sizing** — risk = capital × risk_per_trade_pct. With ``premium`` (option buy/sell) risk per unit =
   |premium − premium_stop| (premium_stop missing ⇒ whole premium at risk); otherwise |entry − stop| ×
   point_value. Quantity is whole lots; under one lot ⇒ rejected. An explicit ``qty`` must be a lot multiple
   and may not exceed the risk-sized quantity.
8. **Margin** — when ``funds`` is given, required margin (``margin_per_lot`` × lots, or premium × qty for
   option buys) must not exceed ``funds.available_cash``. Futures / option selling without ``margin_per_lot``
   cannot be verified and are rejected when funds are supplied. ``funds=None`` skips the margin check.
9. **Post-loss cooldown** — no entries within ``cooldown_after_loss_min`` of the last losing exit today.
10. **Revenge-size block** — while the most recent closed trade today is a loss (i.e. until a non-losing trade
    closes), quantity may not exceed that losing trade's quantity × ``revenge_size_multiplier``. Auto-sized
    quantities are capped; explicit quantities above the cap are rejected.

``can_edit_limits()`` is False from pre-open (09:00) to close (15:30) on trading days when
``lock_edits_during_market`` is set.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Any

from indexedge.config.settings import RiskSettings
from indexedge.core.clock import IST, MARKET_CLOSE, MARKET_OPEN, PRE_OPEN, Clock, parse_hhmm
from indexedge.core.types import Funds, MarketEvent, Position, Side
from indexedge.journal.db import Journal
from indexedge.journal.models import DayStats

log = logging.getLogger(__name__)

LOCK_KEY = "risk_lock:"


@dataclass
class TradeProposal:
    underlying: str
    side: Side | str
    entry: float  # underlying / instrument price used for point-based sizing
    stop: float
    lot_size: int
    qty: int | None = None  # explicit quantity (units, lot multiple); None = auto-size
    premium: float | None = None  # option premium at entry ⇒ premium-based sizing
    premium_stop: float | None = None  # option premium at which the trade is exited
    point_value: float = 1.0
    margin_per_lot: float | None = None  # broker margin per lot (futures, option selling)


@dataclass
class RiskDecision:
    allowed: bool
    reasons: list[str] = field(default_factory=list)
    qty: int = 0
    notes: list[str] = field(default_factory=list)  # informational (e.g. "size capped …") when allowed
    risk_amount: float = 0.0  # rupees at risk for ``qty``


def _rs(x: float) -> str:
    return f"₹{x:,.0f}"


class RiskEngine:
    def __init__(self, settings: RiskSettings, clock: Clock, journal: Journal, cutoff: str = "15:20") -> None:
        self.settings = settings
        self.clock = clock
        self.journal = journal
        self.cutoff: time = parse_hhmm(cutoff)

    # ------------------------------------------------------------------------------------- locking
    def _lock_key(self) -> str:
        return f"{LOCK_KEY}{self.clock.today().isoformat()}"

    def lock_day(self, reason: str) -> None:
        if self.locked:
            return
        self.journal.set_setting(self._lock_key(), {"reason": reason, "ts": self.clock.now().isoformat()})
        log.warning("Trading locked for %s: %s", self.clock.today(), reason)

    @property
    def locked(self) -> bool:
        return self.journal.get_setting(self._lock_key()) is not None

    @property
    def lock_reason(self) -> str:
        v = self.journal.get_setting(self._lock_key())
        return (v or {}).get("reason", "") if isinstance(v, dict) else ""

    def can_edit_limits(self) -> bool:
        if not self.settings.lock_edits_during_market:
            return True
        now = self.clock.now()
        in_session = self.clock.is_trading_day(now.date()) and PRE_OPEN <= now.time() < MARKET_CLOSE
        return not in_session

    # --------------------------------------------------------------------------------------- sizing
    @property
    def risk_per_trade(self) -> float:
        return self.settings.capital * self.settings.risk_per_trade_pct / 100.0

    @property
    def max_daily_loss(self) -> float:
        return self.settings.capital * self.settings.max_daily_loss_pct / 100.0

    def size_position(
        self,
        entry: float,
        stop: float,
        lot_size: int,
        point_value: float = 1.0,
        risk_amount: float | None = None,
    ) -> int:
        """Units (lots × lot_size) risking ≤ ``risk_amount`` (default capital × risk %); 0 if < 1 lot."""
        per_unit = abs(entry - stop) * point_value
        return self._lots_for(per_unit, lot_size, self.risk_per_trade if risk_amount is None else risk_amount)

    @staticmethod
    def _lots_for(per_unit_risk: float, lot_size: int, budget: float) -> int:
        if lot_size <= 0 or per_unit_risk <= 0 or not math.isfinite(per_unit_risk) or budget <= 0:
            return 0
        lots = math.floor(budget / (per_unit_risk * lot_size) + 1e-9)
        return max(lots, 0) * lot_size

    @staticmethod
    def per_unit_risk(p: TradeProposal) -> float:
        if p.premium is not None:
            stop_prem = p.premium_stop if p.premium_stop is not None else 0.0
            return abs(p.premium - stop_prem)
        return abs(p.entry - p.stop) * p.point_value

    def refresh_locks(self, stats: DayStats | None = None) -> bool:
        """Lock the day if a day-level limit is breached (call after each close). Returns ``locked``."""
        s = self.settings
        stats = stats or self.journal.today_stats(self.clock.today())
        if self.max_daily_loss > 0 and stats.realised_pnl <= -self.max_daily_loss:
            self.lock_day(
                f"Max daily loss hit: realised {_rs(stats.realised_pnl)} (limit -{_rs(self.max_daily_loss)})"
            )
        if stats.trades >= s.max_trades_per_day:
            self.lock_day(f"Max trades per day reached ({stats.trades}/{s.max_trades_per_day})")
        if stats.consecutive_losses >= s.max_consecutive_losses:
            self.lock_day(f"{stats.consecutive_losses} consecutive losses (limit {s.max_consecutive_losses})")
        return self.locked

    # -------------------------------------------------------------------------------------- status
    def status(self, open_positions: list[Position] | None = None) -> dict[str, Any]:
        stats = self.journal.today_stats(self.clock.today())
        pnl = stats.realised_pnl + sum(p.unrealised_pnl for p in open_positions or [])
        return {
            "locked": self.locked,
            "lock_reason": self.lock_reason,
            "daily_pnl": round(pnl, 2),
            "trades_today": stats.trades,
            "max_trades": self.settings.max_trades_per_day,
            "daily_loss_used_pct": round(max(0.0, -pnl) / self.max_daily_loss * 100, 1)
            if self.max_daily_loss > 0
            else 0.0,
        }

    # --------------------------------------------------------------------------------------- check
    def check(
        self,
        proposal: TradeProposal,
        open_positions: list[Position] | None = None,
        funds: Funds | None = None,
        feed_age_s: float | None = 0.0,
        blackout: MarketEvent | None = None,
    ) -> RiskDecision:
        s, p = self.settings, proposal
        positions = [x for x in (open_positions or []) if x.net_qty != 0]
        now = self.clock.now()
        reasons: list[str] = []
        notes: list[str] = []
        stats = self.journal.today_stats(now.date())

        self.refresh_locks(stats)
        if self.locked:
            reasons.append(f"Trading is locked for today: {self.lock_reason}")

        reasons += self._session_reasons(now)
        if blackout is not None:
            reasons.append(
                f"Event blackout: {blackout.title} at {blackout.ts.astimezone(IST):%H:%M} "
                f"(no entries {blackout.blackout_before_min} min before to "
                f"{blackout.blackout_after_min} min after)"
            )
        if feed_age_s is None or not math.isfinite(feed_age_s) or feed_age_s > s.stale_feed_seconds:
            age = "unknown" if feed_age_s is None or not math.isfinite(feed_age_s) else f"{feed_age_s:.0f}s"
            reasons.append(f"Market data feed is stale (last tick {age} ago, limit {s.stale_feed_seconds}s)")

        unrealised = sum(x.unrealised_pnl for x in positions)
        day_pnl = stats.realised_pnl + unrealised
        if -self.max_daily_loss < stats.realised_pnl and day_pnl <= -self.max_daily_loss:
            reasons.append(
                f"Day P&L including open positions {_rs(day_pnl)} is at the daily loss limit "
                f"(-{_rs(self.max_daily_loss)})"
            )
        if len(positions) >= s.max_open_positions:
            reasons.append(f"Max open positions reached ({len(positions)}/{s.max_open_positions})")

        if stats.last_loss_ts is not None and s.cooldown_after_loss_min > 0:
            mins = (now - stats.last_loss_ts).total_seconds() / 60
            if 0 <= mins < s.cooldown_after_loss_min:
                reasons.append(
                    f"Cooling down after a loss: {s.cooldown_after_loss_min - mins:.0f} min left "
                    f"(cooldown {s.cooldown_after_loss_min} min)"
                )

        qty, risk_amt = self._size(p, positions, stats, day_pnl, reasons, notes)
        if qty > 0 and funds is not None:
            reasons += self._margin_reasons(p, qty, funds)

        allowed = not reasons
        return RiskDecision(
            allowed=allowed,
            reasons=reasons,
            qty=qty if allowed else 0,
            notes=notes,
            risk_amount=round(risk_amt, 2) if allowed else 0.0,
        )

    # ------------------------------------------------------------------------------------ helpers
    def _session_reasons(self, now: datetime) -> list[str]:
        s = self.settings
        if not self.clock.is_trading_day(now.date()):
            return ["Market closed today (weekend or exchange holiday)"]
        t = now.time()
        if t < MARKET_OPEN or t >= MARKET_CLOSE:
            return [f"Market is closed at {t:%H:%M} (session 09:15-15:30)"]
        if t >= self.cutoff:
            return [f"Past the broker intraday cutoff {self.cutoff:%H:%M}; no new entries"]
        out = []
        mins_open = self.clock.minutes_since_open(now)
        if mins_open < s.no_trade_first_min:
            out.append(
                f"No entries in the first {s.no_trade_first_min} min after the open "
                f"({s.no_trade_first_min - mins_open:.1f} min left)"
            )
        cutoff_dt = datetime.combine(now.date(), self.cutoff, IST)
        mins_to_cutoff = (cutoff_dt - now).total_seconds() / 60
        if mins_to_cutoff <= s.no_trade_last_min:
            out.append(
                f"No entries in the last {s.no_trade_last_min} min before the {self.cutoff:%H:%M} cutoff"
            )
        return out

    def _size(
        self,
        p: TradeProposal,
        positions: list[Position],
        stats: Any,
        day_pnl: float,
        reasons: list[str],
        notes: list[str],
    ) -> tuple[int, float]:
        s = self.settings
        side = getattr(p.side, "value", p.side)
        if p.lot_size <= 0:
            reasons.append("Invalid lot size")
            return 0, 0.0
        if p.premium is None:
            if p.entry == p.stop:
                reasons.append("Stop equals entry; risk cannot be sized")
                return 0, 0.0
            if (side == "BUY" and p.stop > p.entry) or (side == "SELL" and p.stop < p.entry):
                reasons.append(f"Stop {p.stop} is on the wrong side of entry {p.entry} for a {side}")
                return 0, 0.0
        per_unit = self.per_unit_risk(p)
        if per_unit <= 0:
            reasons.append("Premium stop equals premium; risk cannot be sized")
            return 0, 0.0

        remaining = self.max_daily_loss + min(day_pnl, 0.0)
        budget = min(self.risk_per_trade, remaining)
        if budget < self.risk_per_trade:
            notes.append(f"Risk budget reduced to {_rs(budget)} (remaining daily loss allowance)")
        sized = self._lots_for(per_unit, p.lot_size, budget)
        if sized == 0:
            reasons.append(
                f"Stop too wide: one lot risks {_rs(per_unit * p.lot_size)} but the allowed risk is "
                f"{_rs(budget)} ({s.risk_per_trade_pct}% of {_rs(s.capital)})"
            )
            return 0, 0.0

        # per-index lot exposure
        max_lots = s.max_lots_per_index.get(p.underlying.upper())
        cap = sized
        if max_lots is not None:
            used = sum(
                abs(x.net_qty) / max(x.instrument.lot_size, 1)
                for x in positions
                if x.instrument.name.upper() == p.underlying.upper()
            )
            free_lots = int(max_lots - math.ceil(used - 1e-9))
            if free_lots <= 0:
                reasons.append(f"{p.underlying} exposure limit reached ({used:g}/{max_lots} lots open)")
                return 0, 0.0
            cap = min(cap, free_lots * p.lot_size)

        # revenge-size block: last closed trade today was a loss
        revenge_cap = None
        if stats.consecutive_losses > 0 and stats.last_loss_qty > 0:
            revenge_cap = int(stats.last_loss_qty * s.revenge_size_multiplier) // p.lot_size * p.lot_size

        if p.qty is not None:
            q = int(p.qty)
            if q <= 0 or q % p.lot_size:
                reasons.append(f"Quantity {q} is not a whole number of lots (lot size {p.lot_size})")
                return 0, 0.0
            if q > sized:
                reasons.append(
                    f"Quantity {q} risks {_rs(per_unit * q)}, above the allowed {_rs(budget)} "
                    f"(max {sized} units)"
                )
            if q > cap and cap < sized:
                reasons.append(
                    f"Quantity {q} exceeds {p.underlying} lot exposure limit (max {cap} more units)"
                )
            if revenge_cap is not None and q > revenge_cap:
                reasons.append(
                    f"Revenge-size block: {q} exceeds the last losing trade's size "
                    f"{stats.last_loss_qty} x {s.revenge_size_multiplier:g}"
                )
            return q, per_unit * q

        q = cap
        if cap < sized:
            notes.append(f"Size capped to {cap} by the {p.underlying} lot exposure limit")
        if revenge_cap is not None and q > revenge_cap:
            if revenge_cap <= 0:
                reasons.append("Revenge-size block: allowed size after the last loss is below one lot")
                return 0, 0.0
            notes.append(f"Size capped to {revenge_cap} after a loss (revenge-size block)")
            q = revenge_cap
        return q, per_unit * q

    @staticmethod
    def _margin_reasons(p: TradeProposal, qty: int, funds: Funds) -> list[str]:
        side = getattr(p.side, "value", p.side)
        if p.margin_per_lot is not None:
            need = p.margin_per_lot * qty / p.lot_size
        elif p.premium is not None and side == "BUY":
            need = p.premium * qty
        else:
            return ["Margin requirement unknown for this order; cannot verify funds"]
        if need > funds.available_cash:
            return [f"Insufficient margin: need {_rs(need)}, available {_rs(funds.available_cash)}"]
        return []
