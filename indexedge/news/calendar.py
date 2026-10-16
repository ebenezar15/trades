"""Scheduled-event calendar: macro events (``config/events.yaml``), F&O expiries and holidays.

* ``upcoming(days)`` — events from now (including ones whose post-event window is still running).
* ``in_blackout(at)`` — the high/medium-impact event whose blackout window contains ``at`` (else None).
* ``event_risk(at)`` — 0..1 score that rises linearly over the 24 h before a high-impact event, is 1 inside
  its blackout window and decays over 6 h after it (medium-impact events count half).

Times in ``events.yaml`` are IST. Events outside market hours (e.g. FOMC at ~23:30/00:30 IST) never cause an
intraday blackout but still raise ``event_risk`` around the next session.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import yaml

from indexedge.config.settings import ROOT
from indexedge.core.clock import IST, MARKET_OPEN, parse_hhmm
from indexedge.core.types import MarketEvent

log = logging.getLogger(__name__)

DEFAULT_EVENTS_PATH = ROOT / "config" / "events.yaml"
RISK_LEAD_HOURS = 24.0
RISK_DECAY_HOURS = 6.0
IMPACT_FACTOR = {"high": 1.0, "medium": 0.5, "low": 0.0}
MEDIUM_BLACKOUT_MIN = 5
NON_RISK_CATEGORIES = {"expiry", "holiday"}   # calendar facts, not macro shocks


def _parse_date(v: Any) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v))


def load_events(path: Path, before_min: int = 15, after_min: int = 15) -> list[MarketEvent]:
    """Load ``events.yaml`` (list of {date, time, title, category, impact, verified?}). Bad rows are skipped."""
    path = Path(path)
    if not path.exists():
        log.warning("events file %s not found", path)
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    if isinstance(raw, dict):
        raw = raw.get("events", [])
    out: list[MarketEvent] = []
    for row in raw:
        try:
            d = _parse_date(row["date"])
            t = parse_hhmm(str(row.get("time") or "09:15"))
            impact = str(row.get("impact", "medium")).lower()
            b, a = _windows(impact, before_min, after_min)
            category = str(row.get("category", "other")).lower()
            out.append(MarketEvent(id=f"{category}-{d.isoformat()}-{t.strftime('%H%M')}",
                                   ts=datetime.combine(d, t, IST), title=str(row["title"]),
                                   category=category, impact=impact,
                                   blackout_before_min=b, blackout_after_min=a))
        except Exception as exc:  # noqa: BLE001 - best-effort, must never raise
            log.warning("skipping bad event row %r: %s", row, exc)
    return out


def _windows(impact: str, before_min: int, after_min: int) -> tuple[int, int]:
    if impact == "high":
        return before_min, after_min
    if impact == "medium":
        return min(before_min, MEDIUM_BLACKOUT_MIN), min(after_min, MEDIUM_BLACKOUT_MIN)
    return 0, 0


class EventCalendar:
    """``settings`` may be the full ``Settings`` or ``NewsSettings`` (blackout minutes, holidays)."""

    def __init__(self, settings: Any, clock: Any, path: Path | None = None,
                 expiries: Iterable[date] | dict[str, Iterable[date]] | None = None,
                 holidays: Iterable[date | str] | None = None) -> None:
        news = getattr(settings, "news", settings)
        self.before_min = int(getattr(news, "blackout_before_min", 15))
        self.after_min = int(getattr(news, "blackout_after_min", 15))
        self.clock = clock
        self.path = Path(path) if path else DEFAULT_EVENTS_PATH
        self._macro = load_events(self.path, self.before_min, self.after_min)
        self._expiries: list[MarketEvent] = []
        self._holidays: list[MarketEvent] = []
        hol = list(holidays or []) + list(getattr(settings, "holidays", []) or [])
        hol += list(getattr(clock, "holidays", []) or [])
        self.set_holidays(hol)
        if expiries:
            self.set_expiries(expiries)

    # ------------------------------------------------------------------ sources
    def set_expiries(self, expiries: Iterable[date] | dict[str, Iterable[date]]) -> None:
        """Replace expiry events. Accepts a list of dates or ``{underlying: [dates]}``."""
        by_name = expiries if isinstance(expiries, dict) else {"F&O": expiries}
        evs: list[MarketEvent] = []
        for name, dates in by_name.items():
            for d in sorted({_parse_date(x) for x in dates}):
                evs.append(MarketEvent(id=f"expiry-{name}-{d.isoformat()}",
                                       ts=datetime.combine(d, time(15, 30), IST),
                                       title=f"{name} expiry", category="expiry", impact="medium",
                                       blackout_before_min=0, blackout_after_min=0))
        self._expiries = evs

    def set_holidays(self, holidays: Iterable[date | str]) -> None:
        self._holidays = [MarketEvent(id=f"holiday-{d.isoformat()}", ts=datetime.combine(d, MARKET_OPEN, IST),
                                      title="NSE trading holiday", category="holiday", impact="low",
                                      blackout_before_min=0, blackout_after_min=0)
                          for d in sorted({_parse_date(x) for x in holidays})]

    @property
    def events(self) -> list[MarketEvent]:
        return sorted(self._macro + self._expiries + self._holidays, key=lambda e: e.ts)

    # ------------------------------------------------------------------ queries
    def upcoming(self, days: int = 7, at: datetime | None = None) -> list[MarketEvent]:
        at = at or self.clock.now()
        end = at + timedelta(days=days)
        return [e for e in self.events
                if e.ts + timedelta(minutes=e.blackout_after_min) >= at and e.ts <= end]

    def on_day(self, d: date) -> list[MarketEvent]:
        return [e for e in self.events if e.ts.astimezone(IST).date() == d]

    def in_blackout(self, at: datetime | None = None) -> MarketEvent | None:
        at = at or self.clock.now()
        for e in self.events:
            if e.blackout_before_min <= 0 and e.blackout_after_min <= 0:
                continue
            if e.ts - timedelta(minutes=e.blackout_before_min) <= at <= e.ts + timedelta(
                    minutes=e.blackout_after_min):
                return e
        return None

    def event_risk(self, at: datetime | None = None) -> float:
        at = at or self.clock.now()
        risk = 0.0
        for e in self.events:
            factor = IMPACT_FACTOR.get(e.impact, 0.0)
            if factor <= 0 or e.category in NON_RISK_CATEGORIES:
                continue
            mins = (e.ts - at).total_seconds() / 60.0           # >0 before the event
            before, after = max(e.blackout_before_min, 0), max(e.blackout_after_min, 0)
            if -after <= mins <= before:
                r = 1.0
            elif before < mins <= RISK_LEAD_HOURS * 60:
                r = 1.0 - (mins - before) / (RISK_LEAD_HOURS * 60 - before)
            elif -after - RISK_DECAY_HOURS * 60 <= mins < -after:
                r = 1.0 - (-mins - after) / (RISK_DECAY_HOURS * 60)
            else:
                continue
            risk = max(risk, factor * max(0.0, min(1.0, r)))
        return round(risk, 4)


def event_to_dict(e: MarketEvent) -> dict[str, Any]:
    return {"id": e.id, "ts": e.ts.isoformat(), "title": e.title, "category": e.category, "impact": e.impact}
