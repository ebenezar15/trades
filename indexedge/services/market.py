"""Live market state: per-underlying 5-minute frames (history + live bars), last prices, demo feed,
and option-chain snapshots.

Data flow: broker ticks (or the demo feed) -> ``on_tick`` -> CandleAggregator -> closed bar appended to the
frame -> ``bus.CANDLE`` published. Index spot has no volume, so the near-month future's volume for the same
bucket is attached to each index bar when available.
"""
from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from indexedge.core import bus as topics
from indexedge.core.clock import IST, MARKET_OPEN, Clock
from indexedge.core.types import Instrument, InstrumentKind, OptionType, Tick
from indexedge.data.aggregator import CandleAggregator
from indexedge.data.store import CandleStore, empty_candles
from indexedge.data.synthetic import generate_intraday, generate_option_chain, trading_days
from indexedge.features.options import bs_price, enrich_chain, max_pain, oi_walls, pcr

log = logging.getLogger(__name__)

UNDERLYINGS = ("NIFTY", "BANKNIFTY")
HISTORY_DAYS = 60
TickSink = Callable[[Tick], Awaitable[None] | None]


def _start_for(end_day: date, days: int, holidays: set[date]) -> date:
    """First trading day such that ``days`` trading days end on ``end_day``."""
    d = end_day
    count = 1
    while count < days:
        d -= timedelta(days=1)
        if d.weekday() < 5 and d not in holidays:
            count += 1
    return d


class MarketData:
    def __init__(self, settings: Any, clock: Clock, bus: Any, store: CandleStore, master: Any = None,
                 data_broker: Any = None, demo: bool = True) -> None:
        self.settings = settings
        self.clock = clock
        self.bus = bus
        self.store = store
        self.master = master
        self.data_broker = data_broker
        self.demo = demo
        self.frames: dict[str, pd.DataFrame] = {u: empty_candles() for u in UNDERLYINGS}
        self.ltp: dict[str, float] = {}
        self.prev_close: dict[str, float] = {}
        self.day_open: dict[str, float] = {}
        self.vix: float | None = None
        self.last_tick_at: datetime | None = None
        self.data_source = "synthetic" if demo else "broker"
        self._token_map: dict[str, str] = {}          # token -> underlying / "VIX" / "FUT:<u>"
        self._sinks: list[TickSink] = []
        self._agg = CandleAggregator(5, self._on_bar_close)
        self._fut_bars: dict[tuple[str, pd.Timestamp], float] = {}
        self._demo_task: asyncio.Task | None = None
        self._demo_rng = np.random.default_rng()
        self._demo_cumvol: dict[str, float] = {}
        self.demo_option_tokens: dict[str, Instrument] = {}
        self._chain_cache: dict[tuple[str, str], tuple[datetime, dict]] = {}
        self._chain_first_oi: dict[tuple[str, str], dict[tuple[float, str], float]] = {}

    # ------------------------------------------------------------------ sinks
    def add_tick_sink(self, sink: TickSink) -> None:
        self._sinks.append(sink)

    async def _dispatch(self, tick: Tick) -> None:
        for s in self._sinks:
            try:
                res = s(tick)
                if asyncio.iscoroutine(res):
                    await res
            except Exception:
                log.exception("tick sink failed")

    # ------------------------------------------------------------------ history
    async def load_history(self, days: int = HISTORY_DAYS) -> None:
        for u in UNDERLYINGS:
            df = empty_candles()
            try:
                df = self.store.load(u, "5m")
            except Exception:  # noqa: BLE001
                log.warning("candle store read failed for %s", u)
            if not self.demo and self.data_broker is not None and self.master is not None:
                try:
                    from indexedge.data.history import HistoryService

                    hs = HistoryService(self.data_broker, self.store, self.master, clock=self.clock)
                    await hs.backfill(u, "5m", days)
                    df = self.store.load(u, "5m")
                    self.data_source = "broker"
                except Exception as exc:  # noqa: BLE001
                    log.warning("history backfill failed for %s: %s", u, exc)
            if df is None or df.empty:
                df = self._synthetic_history(u, days)
                self.data_source = "synthetic"
            df = df[df.index.date < self.clock.today()] if self.clock.is_market_open() else df
            self.frames[u] = df.tail(days * 75)
            if not df.empty:
                last_day = df.index[-1].date()
                self.prev_close[u] = float(df["close"].iloc[-1])
                self.ltp.setdefault(u, self.prev_close[u])
                today = df[df.index.date == last_day]
                self.day_open.setdefault(u, float(today["open"].iloc[0]))
        if self.vix is None:
            self.vix = 13.5 if self.demo else None

    def _synthetic_history(self, underlying: str, days: int) -> pd.DataFrame:
        hol = set(self.clock.holidays)
        today = self.clock.today()
        end = today if (self.clock.is_trading_day(today) and self.clock.now().time() >= MARKET_OPEN
                        and not self.clock.is_market_open()) else self.clock.previous_trading_day(today)
        start = _start_for(end, days, hol)
        seed = 7 if underlying == "NIFTY" else 11
        return generate_intraday(underlying, days=days, interval_min=5, seed=seed, start=start, holidays=hol)

    # ------------------------------------------------------------------ live feed
    async def start(self) -> None:
        if self.demo or self.data_broker is None:
            self._demo_task = asyncio.create_task(self._demo_loop())
            return
        tokens: list[tuple[str, str]] = []
        idx = self.settings.broker.index_tokens
        for u in UNDERLYINGS:
            if u in idx:
                tokens.append(("NSE", idx[u]))
                self._token_map[idx[u]] = u
            if self.master is not None:
                fut = self.master.nearest_future(u, self.clock.today())
                if fut is not None:
                    tokens.append(("NFO", fut.token))
                    self._token_map[fut.token] = f"FUT:{u}"
        if "INDIAVIX" in idx:
            tokens.append(("NSE", idx["INDIAVIX"]))
            self._token_map[idx["INDIAVIX"]] = "VIX"
        try:
            await self.data_broker.subscribe_ticks(tokens, self.on_tick)
        except Exception as exc:  # noqa: BLE001
            log.error("tick subscription failed: %s", exc)

    async def subscribe_instrument(self, inst: Instrument) -> None:
        """Make sure ticks flow for a traded instrument (options/futures)."""
        if self.demo or self.data_broker is None:
            self.demo_option_tokens[inst.token] = inst
            return
        if inst.token in self._token_map:
            return
        self._token_map.setdefault(inst.token, f"INST:{inst.symbol}")
        try:
            await self.data_broker.subscribe_ticks([(inst.exchange, inst.token)], self.on_tick)
        except Exception as exc:  # noqa: BLE001
            log.error("subscribe %s failed: %s", inst.symbol, exc)

    async def stop(self) -> None:
        if self._demo_task:
            self._demo_task.cancel()

    async def on_tick(self, tick: Tick) -> None:
        self.last_tick_at = self.clock.now()
        key = self._token_map.get(tick.token, "")
        if key in UNDERLYINGS:
            self.ltp[key] = tick.ltp
            if tick.close:
                self.prev_close[key] = tick.close
            if tick.open:
                self.day_open[key] = tick.open
            await self._agg.on_tick(tick)
        elif key == "VIX":
            self.vix = tick.ltp
        elif key.startswith("FUT:"):
            closed = await self._agg.on_tick(tick)
            for _tok, bar in closed:
                self._fut_bars[(key[4:], pd.Timestamp(bar.name))] = float(bar.get("volume", 0.0))
        await self._dispatch(tick)
        if key in UNDERLYINGS:
            await self.bus.publish(topics.TICK, {"underlying": key, "ltp": tick.ltp,
                                                 "ts": tick.ts.isoformat()})

    async def flush(self) -> None:
        """Close bars whose bucket has ended (called every few seconds by the runtime)."""
        await self._agg.flush(self.clock.now())

    async def _on_bar_close(self, token: str, bar: pd.Series) -> None:
        key = self._token_map.get(token, "")
        if key not in UNDERLYINGS:
            return
        ts = pd.Timestamp(bar.name)
        row = {c: float(bar.get(c, np.nan)) for c in ("open", "high", "low", "close")}
        vol = self._fut_bars.pop((key, ts), None)
        if vol is None and self.master is not None:
            fut = self.master.nearest_future(key, self.clock.today())
            cur = self._agg.current(fut.token) if fut is not None else None
            vol = float(cur.get("volume", 0.0)) if cur is not None and pd.Timestamp(cur.name) == ts else 0.0
        row["volume"] = float(vol or bar.get("volume", 0.0) or 0.0)
        frame = self.frames[key]
        new = pd.DataFrame([row], index=pd.DatetimeIndex([ts]))
        frame = pd.concat([frame[frame.index != ts], new]).sort_index()
        self.frames[key] = frame.tail(HISTORY_DAYS * 80)
        try:
            self.store.save(key, "5m", new)
        except Exception:  # noqa: BLE001
            log.debug("could not persist bar")
        await self.bus.publish(topics.CANDLE, {"underlying": key, "tf": "5m", "candle_ts": ts,
                                               "bar": {"time": int(ts.timestamp()), **row}})

    # ------------------------------------------------------------------ demo feed
    async def _demo_loop(self) -> None:
        """Synthetic ticks during market hours so the whole app can be explored without a broker."""
        vol_per_s = {"NIFTY": 0.009 / math.sqrt(22500), "BANKNIFTY": 0.011 / math.sqrt(22500)}
        for u in UNDERLYINGS:
            self._token_map.setdefault(f"DEMO_{u}", u)
            self._token_map.setdefault(f"DEMO_FUT_{u}", f"FUT:{u}")
        while True:
            try:
                await asyncio.sleep(1.0)
                now = self.clock.now()
                if not self.clock.is_market_open(now):
                    continue
                for u in UNDERLYINGS:
                    last = self.ltp.get(u) or self.prev_close.get(u) or (25000.0 if u == "NIFTY" else 56000.0)
                    if u not in self.day_open or self.frames[u].empty or \
                            self.frames[u].index[-1].date() < now.date() and u not in self._demo_cumvol:
                        self.day_open[u] = last
                    px = round(last * math.exp(self._demo_rng.normal(0, vol_per_s[u])), 2)
                    mins = max(1.0, self.clock.minutes_since_open(now))
                    vol = float(self._demo_rng.poisson(40 if 30 < mins < 330 else 120)) * 25
                    self._demo_cumvol[u] = self._demo_cumvol.get(u, 0.0) + vol
                    await self.on_tick(Tick(token=f"DEMO_FUT_{u}", ts=now, ltp=px * 1.001,
                                            volume=self._demo_cumvol[u]))
                    await self.on_tick(Tick(token=f"DEMO_{u}", ts=now, ltp=px, volume=0.0,
                                            close=self.prev_close.get(u)))
                if self.vix is not None:
                    self.vix = max(9.0, self.vix * math.exp(self._demo_rng.normal(0, 0.002)))
                for inst in list(self.demo_option_tokens.values()):
                    await self._dispatch(Tick(token=inst.token, ts=now, ltp=self.demo_price(inst, now)))
                await self.flush()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("demo feed error")

    def demo_price(self, inst: Instrument, now: datetime | None = None) -> float:
        """Model price for a demo instrument (Black-Scholes for options, spot+basis for futures)."""
        now = now or self.clock.now()
        spot = self.ltp.get(inst.name) or self.prev_close.get(inst.name) or 0.0
        if inst.kind is InstrumentKind.OPTION and inst.strike and inst.expiry:
            exp = datetime.combine(inst.expiry, datetime.min.time(), IST) + timedelta(hours=15, minutes=30)
            t = max((exp - now).total_seconds() / (365 * 86400), 1 / (365 * 24 * 12))
            opt = inst.option_type.value if inst.option_type else "CE"
            px = bs_price(spot, inst.strike, t, 0.065, (self.vix or 13.5) / 100, opt)
            return max(0.05, round(round(px / 0.05) * 0.05, 2))
        if inst.kind is InstrumentKind.FUTURE:
            return round(spot * 1.001, 1)
        return spot

    # ------------------------------------------------------------------ option chain
    async def option_chain(self, underlying: str, expiry: date | None = None) -> dict:
        underlying = underlying.upper()
        expiries: list[date] = []
        if self.master is not None:
            try:
                expiries = self.master.expiries(underlying, self.clock.today())
            except Exception:  # noqa: BLE001
                expiries = []
        if not expiries:
            expiries = [self.clock.today() + timedelta(days=(1 - self.clock.today().weekday()) % 7 or 7)]
        expiry = expiry or expiries[0]
        key = (underlying, expiry.isoformat())
        now = self.clock.now()
        cached = self._chain_cache.get(key)
        if cached and (now - cached[0]).total_seconds() < 60:
            return cached[1]
        spot = self.ltp.get(underlying) or self.prev_close.get(underlying) or 0.0
        chain = None
        if not self.demo and self.data_broker is not None and self.master is not None:
            chain = await self._live_chain(underlying, expiry, spot)
        if chain is None or chain.empty:
            seed = int(now.timestamp() // 300) % 10_000
            chain = generate_option_chain(spot, expiry, now, seed=seed, underlying=underlying)
        out = self._chain_payload(underlying, expiry, expiries, spot, chain, now)
        self._chain_cache[key] = (now, out)
        return out

    async def _live_chain(self, underlying: str, expiry: date, spot: float) -> pd.DataFrame | None:
        """Snapshot ±12 strikes around ATM via SmartAPI getMarketData(FULL)."""
        sc = getattr(self.data_broker, "_sc", None)
        if sc is None:
            return None
        try:
            atm = self.master.atm_strike(underlying, spot, expiry)
            step = self.master.strike_step(underlying, expiry)
            insts = [i for i in self.master.chain(underlying, expiry)
                     if i.strike is not None and abs(i.strike - atm) <= 12 * step]
            by_token = {i.token: i for i in insts}
            rows: list[dict] = []
            toks = list(by_token)
            for k in range(0, len(toks), 50):
                res = await asyncio.to_thread(sc.getMarketData, "FULL", {"NFO": toks[k:k + 50]})
                for q in ((res or {}).get("data") or {}).get("fetched", []) or []:
                    inst = by_token.get(str(q.get("symbolToken")))
                    if inst is None:
                        continue
                    rows.append({"strike": inst.strike, "option_type": inst.option_type.value,
                                 "ltp": float(q.get("ltp") or 0), "oi": float(q.get("opnInterest") or 0),
                                 "volume": float(q.get("tradeVolume") or 0),
                                 "ltp_change": float(q.get("netChange") or 0)})
                await asyncio.sleep(1.0)
            df = pd.DataFrame(rows)
            if df.empty:
                return None
            first = self._chain_first_oi.setdefault((underlying, expiry.isoformat()), {})
            if self.clock.now().date() != getattr(self, "_chain_day", None):
                first.clear()
                self._chain_day = self.clock.now().date()
            for r in df.itertuples():
                first.setdefault((r.strike, r.option_type), r.oi)
            df["oi_change"] = [r.oi - first.get((r.strike, r.option_type), r.oi) for r in df.itertuples()]
            return df
        except Exception as exc:  # noqa: BLE001
            log.warning("live option chain failed: %s", exc)
            return None

    def _chain_payload(self, underlying: str, expiry: date, expiries: list[date], spot: float,
                       chain: pd.DataFrame, now: datetime) -> dict:
        try:
            rich = enrich_chain(chain, spot, expiry, now)
        except Exception:  # noqa: BLE001
            rich = chain
        rows: dict[float, dict] = {}
        for r in rich.to_dict(orient="records"):
            k = float(r["strike"])
            side = "ce" if str(r["option_type"]).upper() == OptionType.CE.value else "pe"
            rows.setdefault(k, {"strike": k})[side] = {
                f: (None if pd.isna(r.get(f)) else float(r[f])) if f != "buildup" else r.get("buildup")
                for f in ("ltp", "oi", "oi_change", "iv", "delta", "gamma", "theta", "vega", "buildup")
            }
        try:
            walls = oi_walls(chain)
            mp = float(max_pain(chain))
            p = float(pcr(chain))
        except Exception:  # noqa: BLE001
            walls, mp, p = {"resistance": [], "support": []}, None, None
        return {"spot": spot, "expiry": expiry.isoformat(), "expiries": [e.isoformat() for e in expiries[:8]],
                "pcr": p, "max_pain": mp, "walls": walls, "rows": [rows[k] for k in sorted(rows)],
                "source": "synthetic" if self.demo else "broker", "ts": now.isoformat()}

    def oi_extra(self, underlying: str) -> dict | None:
        """OI context for the oi_pcr strategy from the latest cached chain (None if none)."""
        for (u, _), (_, payload) in self._chain_cache.items():
            if u == underlying and payload.get("pcr") is not None:
                return {"pcr": payload["pcr"], "max_pain": payload["max_pain"], "walls": payload["walls"]}
        return None

    # ------------------------------------------------------------------ helpers
    def feed_age_s(self) -> float | None:
        if self.last_tick_at is None:
            return None
        return (self.clock.now() - self.last_tick_at).total_seconds()

    def frame(self, underlying: str) -> pd.DataFrame:
        return self.frames.get(underlying.upper(), empty_candles())

    def history_days(self) -> list[date]:
        return sorted(set(self.frames["NIFTY"].index.date))


__all__ = ["UNDERLYINGS", "MarketData", "trading_days"]
