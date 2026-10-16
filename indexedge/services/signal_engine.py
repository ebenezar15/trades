"""Runs every strategy on each closed 5-minute bar, composes gated/explained signals and routes them
according to each strategy's execution mode (signal-only / one-tap confirm / auto)."""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Any

import pandas as pd

from indexedge.core import bus as topics
from indexedge.core.types import ExecutionMode, Signal
from indexedge.features.engine import build_features
from indexedge.signals.serialize import signal_to_json

log = logging.getLogger(__name__)

SYNTHETIC_NOTE = "Statistics come from SYNTHETIC demo data — they are not real performance"
FEATURE_DAYS = 30


class SignalEngine:
    def __init__(self, runtime: Any) -> None:
        self.rt = runtime
        self.recent: deque[Signal] = deque(maxlen=300)
        self.by_id: dict[str, Signal] = {}
        self.features: dict[str, pd.DataFrame] = {}
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ features
    def compute_features(self, underlying: str) -> pd.DataFrame:
        frame = self.rt.market.frame(underlying)
        if frame.empty:
            return frame
        days = sorted(set(frame.index.date))[-FEATURE_DAYS:]
        sub = frame[frame.index.date >= days[0]]
        feat = build_features(sub)
        self.features[underlying] = feat
        return feat

    def regime(self, underlying: str) -> str:
        feat = self.features.get(underlying)
        if feat is None or feat.empty or "regime" not in feat:
            return "unknown"
        return str(feat["regime"].iloc[-1])

    # ------------------------------------------------------------------ stats used for probability
    def stats_for(self, strategy_id: str, underlying: str) -> dict[str, Any]:
        rep = self.rt.reports.get(underlying, {}).get(strategy_id)
        if not rep:
            return {}
        synthetic = rep.get("data_source") == "synthetic"
        if synthetic and not self.rt.settings.app.demo_mode:
            return {"badges": ["insufficient_data"]}       # never present synthetic stats as real
        summ = rep.get("summary") or {}
        n = int(summ.get("n") or 0)
        wr = summ.get("win_rate")
        return {"n": n, "win_rate": wr, "wins": (wr or 0) * n, "badges": rep.get("badges", []),
                "expectancy_r": summ.get("expectancy_r"), "synthetic": synthetic}

    # ------------------------------------------------------------------ main hook
    async def on_candle(self, payload: dict) -> None:
        """Bus handler: runs the scan as a background task so the feed / timer loop never stalls."""
        underlying = payload.get("underlying")
        if underlying is None:
            return
        task = asyncio.create_task(self._guarded(underlying))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _guarded(self, underlying: str) -> None:
        async with self._lock:
            try:
                await self._run(underlying)
            except Exception:
                log.exception("signal engine failed for %s", underlying)

    async def _run(self, underlying: str) -> list[Signal]:
        rt = self.rt
        if not rt.market.demo and rt.market.sources.get(underlying) == "synthetic":
            log.warning("no real history for %s yet — signals withheld", underlying)
            return []
        feat = await asyncio.to_thread(self.compute_features, underlying)
        if feat.empty:
            return []
        regime = self.regime(underlying)
        context = rt.context.latest if rt.context else None
        news = rt.news.latest(20) if rt.news else []
        extra = rt.market.oi_extra(underlying)
        out: list[Signal] = []
        for sid, strat in rt.strategies.items():
            try:
                raw = strat.latest(feat, underlying, extra)
            except Exception:
                log.exception("strategy %s failed", sid)
                continue
            if raw is None:
                continue
            stats = self.stats_for(sid, underlying)
            sig = rt.composer.compose(raw, feat.iloc[-1], context, stats, regime, news=news,
                                      now=rt.clock.now())
            if stats.get("synthetic"):
                sig.reasons.insert(0, SYNTHETIC_NOTE)
            if sig.status == "active" and not rt.strategy_enabled(sid, underlying):
                sig.status = "blocked"
                sig.blocked_reason = f"Strategy not validated for {underlying} (enable it in Strategy Lab to override)"
            await self.publish(sig, strat.name)
            out.append(sig)
        return out

    async def publish(self, sig: Signal, strategy_name: str | None = None) -> None:
        rt = self.rt
        self.recent.appendleft(sig)
        self.by_id[sig.id] = sig
        try:
            rt.journal.record_signal(sig)
        except Exception:
            log.exception("journal record_signal failed")
        mode = rt.execution_mode(sig.raw.strategy_id)
        payload = self.to_json(sig, strategy_name)
        await rt.bus.publish(topics.SIGNAL, payload)
        if sig.status != "active":
            return
        if mode is ExecutionMode.AUTO:
            task = asyncio.create_task(rt.execute_signal(sig))     # order chasing must not block the bus
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        elif mode is ExecutionMode.CONFIRM:
            rt.confirmations.add(sig)
            sig.status = "pending_confirm"
            await rt.notify_signal(sig, payload, confirm=True)
        else:
            await rt.notify_signal(sig, payload, confirm=False)

    def to_json(self, sig: Signal, strategy_name: str | None = None) -> dict:
        strat = self.rt.strategies.get(sig.raw.strategy_id)
        d = signal_to_json(sig, strategy_name or getattr(strat, "name", None))
        stats = self.stats_for(sig.raw.strategy_id, sig.raw.underlying)
        d.setdefault("win_rate", stats.get("win_rate"))
        d.setdefault("expectancy_r", stats.get("expectancy_r"))
        d["execution_mode"] = self.rt.execution_mode(sig.raw.strategy_id).value
        pend = self.rt.confirmations.get(sig.id) if self.rt.confirmations else None
        if pend is not None:
            d["status"] = "pending_confirm"
            d["confirm_expires_at"] = pend.expires_at.isoformat()
        return d
