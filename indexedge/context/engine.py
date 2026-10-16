"""External context engine: turns global quotes into a macro bias / risk-off verdict (spec §5).

``score_context`` is pure (no I/O, no clock) so it can be unit-tested and replayed. ``ContextEngine`` wires
providers + scoring + the event bus and refreshes on a timer. The signal composer must treat a snapshot with
``stale_required`` (``usable == False``) as "no signal".

Scoring
-------
For every configured input with a fresh quote::

    z_i      = clip(change_pct_i / typical_daily_move_i, -3, 3)
    bias     = tanh( sum(weight_i * z_i) / sum(|weight_i|) )

Negative weights encode inverse relationships (VIX, USD/INR, crude, DXY, US yields). GIFT Nifty uses the
implied gap vs the previous Nifty close when that close is known. ``risk_off`` is set when India VIX is at or
above ``vix_risk_off_level`` or ``bias <= risk_off_threshold``.
"""
from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta
from typing import Any

from indexedge.config.settings import ContextSettings
from indexedge.context.providers import GIFT_NIFTY_SYMBOL, Quote, QuoteProvider
from indexedge.core import bus as topics
from indexedge.core.clock import IST
from indexedge.core.types import ContextSnapshot

log = logging.getLogger(__name__)

# Typical 1-sigma daily % move per data symbol, used to normalise changes before weighting so that a 0.3%
# rupee move counts as much as a 1% S&P move. Approximate long-run values; refresh occasionally.
TYPICAL_DAILY_MOVE_PCT: dict[str, float] = {
    GIFT_NIFTY_SYMBOL: 0.8,
    "^NSEI": 0.8,
    "^NSEBANK": 1.0,
    "ES=F": 1.0,
    "^GSPC": 1.0,
    "NQ=F": 1.3,
    "^IXIC": 1.3,
    "YM=F": 0.9,
    "^DJI": 0.9,
    "^N225": 1.3,
    "^HSI": 1.5,
    "^KS11": 1.2,
    "^STI": 0.8,
    "INR=X": 0.3,
    "DX-Y.NYB": 0.4,
    "^TNX": 2.0,       # % change of the yield level (~8bp at 4%)
    "BZ=F": 2.0,
    "CL=F": 2.2,
    "GC=F": 1.0,
    "^INDIAVIX": 5.0,
    "^VIX": 7.0,
}
DEFAULT_DAILY_MOVE_PCT = 1.0
Z_CLIP = 3.0
VIX_KEYS = ("india_vix",)


def typical_move(symbol: str) -> float:
    return TYPICAL_DAILY_MOVE_PCT.get(symbol, DEFAULT_DAILY_MOVE_PCT)


def gap_expectation(quotes: dict[str, Quote], prev_nifty_close: float | None,
                    symbol: str = GIFT_NIFTY_SYMBOL) -> float | None:
    """Implied Nifty opening gap in % from GIFT Nifty vs the previous Nifty cash close (None if unknown).

    GIFT Nifty usually trades at a small premium (cost of carry), so treat |gap| < ~0.15% as flat.
    """
    q = quotes.get(symbol)
    if q is None or not prev_nifty_close or prev_nifty_close <= 0 or not math.isfinite(q.price):
        return None
    return (q.price / prev_nifty_close - 1.0) * 100.0


def effective_age_seconds(ts: datetime, now: datetime, holidays: Iterable[date] = ()) -> float:
    """Age of a quote excluding whole weekend days / holidays in between (markets were shut anyway).

    Without this, Friday's India VIX would look 66 hours old at Monday's open.
    """
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=IST)
    age = (now - ts).total_seconds()
    if age <= 0:
        return 0.0
    hol = set(holidays)
    d = ts.astimezone(IST).date() + timedelta(days=1)
    end = now.astimezone(IST).date()
    closed_days = 0
    while d < end:
        if d.weekday() >= 5 or d in hol:
            closed_days += 1
        d += timedelta(days=1)
    return max(0.0, age - closed_days * 86400.0)


def _fmt_pct(x: float) -> str:
    return f"{x:+.2f}%" if abs(x) < 0.995 else f"{x:+.1f}%"


def _direction(contrib: float) -> str:
    if contrib > 0.05:
        return "bullish"
    if contrib < -0.05:
        return "bearish"
    return "neutral"


def score_context(quotes: dict[str, Quote], cfg: ContextSettings, now: datetime, event_risk: float = 0.0,
                  prev_nifty_close: float | None = None,
                  holidays: Iterable[date] = ()) -> ContextSnapshot:
    """Pure scoring of external inputs → :class:`ContextSnapshot` (see module docstring)."""
    holidays = tuple(holidays)
    inputs: dict[str, dict[str, Any]] = {}
    reasons: list[str] = []
    stale_required: list[str] = []
    num = den = 0.0
    risk_off = False
    contributions: list[tuple[float, str]] = []

    for name, inp in cfg.inputs.items():
        q = quotes.get(inp.symbol)
        row: dict[str, Any] = {"label": inp.label, "symbol": inp.symbol, "value": None, "change_pct": None,
                               "age_s": None, "stale": True, "missing": q is None, "weight": inp.weight,
                               "required": inp.required, "z": None, "contribution": 0.0}
        inputs[name] = row
        if q is None or not math.isfinite(q.price):
            row["missing"] = True
            if inp.required:
                stale_required.append(name)
                reasons.append(f"{inp.label}: no data (required input missing)")
            continue
        age = effective_age_seconds(q.ts, now, holidays)
        stale = age > inp.max_age_min * 60
        change = q.change_pct
        if inp.symbol == GIFT_NIFTY_SYMBOL:
            gap = gap_expectation(quotes, prev_nifty_close, inp.symbol)
            if gap is not None:
                row["gap_expectation_pct"] = round(gap, 3)
                change = gap
        row.update(value=q.price, change_pct=round(change, 3), age_s=round(age, 1), stale=stale)
        if stale:
            if inp.required:
                stale_required.append(name)
            reasons.append(f"{inp.label}: stale ({age / 60:.0f} min old"
                           f"{', required' if inp.required else ''}) — ignored")
            continue
        z = max(-Z_CLIP, min(Z_CLIP, change / typical_move(inp.symbol)))
        contrib = inp.weight * z
        row.update(z=round(z, 3), contribution=round(contrib, 3))
        num += contrib
        den += abs(inp.weight)
        if (name == "gift_nifty" or inp.symbol == GIFT_NIFTY_SYMBOL) and "gap_expectation_pct" in row:
            g = row["gap_expectation_pct"]
            kind = "flat open" if abs(g) < 0.15 else ("gap-up" if g > 0 else "gap-down")
            contributions.append((abs(contrib) + 10, f"GIFT Nifty implies {kind} ({_fmt_pct(g)})"))
            continue
        word = _direction(contrib)
        if inp.weight < 0 and word != "neutral":
            word += ", inverse"
        contributions.append((abs(contrib), f"{inp.label} {_fmt_pct(change)} ({word})"))

        is_vix = name in VIX_KEYS or "VIX" in inp.symbol.upper()
        if is_vix and q.price >= cfg.vix_risk_off_level:
            risk_off = True
            reasons.insert(0, f"{inp.label} at {q.price:.1f} ≥ {cfg.vix_risk_off_level:g} — risk-off")

    bias = math.tanh(num / den) if den > 0 else 0.0
    if den == 0:
        reasons.append("No fresh external inputs — macro bias neutral")
    if bias <= cfg.risk_off_threshold:
        risk_off = True
        reasons.insert(0, f"Macro bias {bias:+.2f} ≤ {cfg.risk_off_threshold:+.2f} — risk-off")

    # Strongest drivers first, then staleness / risk notes collected above.
    driver_lines = [text for _, text in sorted(contributions, key=lambda t: -t[0])]
    summary = f"Macro bias {bias:+.2f} ({_direction(bias)})"
    event_risk = max(0.0, min(1.0, float(event_risk)))
    tail = []
    if event_risk >= 0.5:
        tail.append(f"Event risk elevated ({event_risk:.2f})")
    if stale_required:
        tail.append("External context unusable: " + ", ".join(stale_required) + " stale/missing")
    return ContextSnapshot(ts=now, macro_bias=round(bias, 4), risk_off=risk_off, event_risk=event_risk,
                           inputs=inputs, reasons=[summary, *reasons, *driver_lines, *tail],
                           stale_required=stale_required)


def snapshot_to_dict(snap: ContextSnapshot) -> dict[str, Any]:
    """JSON-ready dict matching the ``GET /api/context`` contract."""
    return {"ts": snap.ts.isoformat(), "macro_bias": snap.macro_bias, "risk_off": snap.risk_off,
            "event_risk": snap.event_risk, "usable": snap.usable, "inputs": snap.inputs,
            "reasons": list(snap.reasons), "stale_required": list(snap.stale_required)}


class ContextEngine:
    """Fetches quotes from providers, scores them and publishes ``bus.CONTEXT`` snapshots.

    Quotes are cached across refreshes, so a provider that fails temporarily leaves the last quote in place;
    it then ages into ``stale`` instead of silently disappearing. When several providers return the same
    symbol, the freshest quote wins.
    """

    def __init__(self, providers: list[QuoteProvider], settings: Any, bus: Any, clock: Any,
                 event_risk_fn: Callable[[datetime], float] | None = None,
                 prev_close_fn: Callable[[], float | None] | None = None,
                 provider_timeout_s: float = 30.0) -> None:
        self.providers = list(providers)
        self.cfg: ContextSettings = getattr(settings, "context", settings)
        self.bus = bus
        self.clock = clock
        self.event_risk_fn = event_risk_fn
        self.prev_close_fn = prev_close_fn
        self.provider_timeout_s = provider_timeout_s
        self.quotes: dict[str, Quote] = {}
        self.latest: ContextSnapshot | None = None
        self._stop = asyncio.Event()

    @property
    def symbols(self) -> list[str]:
        return list(dict.fromkeys(i.symbol for i in self.cfg.inputs.values()))

    async def _fetch_one(self, provider: QuoteProvider, symbols: list[str]) -> dict[str, Quote]:
        try:
            return await asyncio.wait_for(provider.fetch(symbols), self.provider_timeout_s)
        except Exception as exc:  # noqa: BLE001 - best-effort, must never raise
            log.warning("context provider %s failed: %s", getattr(provider, "name", provider), exc)
            return {}

    async def refresh(self) -> ContextSnapshot:
        symbols = self.symbols
        results = await asyncio.gather(*(self._fetch_one(p, symbols) for p in self.providers))
        for res in results:
            for sym, q in res.items():
                old = self.quotes.get(sym)
                if old is None or q.ts >= old.ts:
                    self.quotes[sym] = q
        now = self.clock.now()
        event_risk = 0.0
        if self.event_risk_fn is not None:
            try:
                event_risk = float(self.event_risk_fn(now))
            except Exception:
                log.exception("event_risk_fn failed")
        prev_close = None
        if self.prev_close_fn is not None:
            try:
                prev_close = self.prev_close_fn()
            except Exception:
                log.exception("prev_close_fn failed")
        holidays = getattr(self.clock, "holidays", ())
        snap = score_context(self.quotes, self.cfg, now, event_risk, prev_nifty_close=prev_close,
                             holidays=holidays)
        self.latest = snap
        if self.bus is not None:
            await self.bus.publish(topics.CONTEXT, snap)
        return snap

    async def run(self) -> None:
        """Refresh every ``refresh_seconds`` until :meth:`stop` is called. Never raises."""
        self._stop.clear()
        while not self._stop.is_set():
            try:
                await self.refresh()
            except Exception:
                log.exception("context refresh failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=max(1, self.cfg.refresh_seconds))
            except TimeoutError:
                pass

    def stop(self) -> None:
        self._stop.set()
