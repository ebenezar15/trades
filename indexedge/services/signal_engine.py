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

    def _scan_all(self, feat: pd.DataFrame, underlying: str, extra: dict | None) -> dict[str, Any]:
        """Run every strategy on the latest bar (worker thread). Returns id -> StrategySignal | str (reason)."""
        res: dict[str, Any] = {}
        for sid, strat in list(self.rt.strategies.items()):
            try:
                raw = strat.latest(feat, underlying, extra)
                res[sid] = raw if raw is not None else (strat.last_skip_reason or "setup conditions not met")
            except Exception as exc:
                log.exception("strategy %s failed", sid)
                res[sid] = f"error: {exc}"
        return res

    async def _run(self, underlying: str) -> list[Signal]:
        rt = self.rt
        agent = rt.agent
        if not rt.market.demo and rt.market.sources.get(underlying) == "synthetic":
            log.warning("no real history for %s yet — signals withheld", underlying)
            await agent.log("scan", f"{underlying}: waiting for real history before scanning", "warning")
            return []
        feat = await asyncio.to_thread(self.compute_features, underlying)
        if feat.empty:
            return []
        regime = self.regime(underlying)
        context = rt.context.latest if rt.context else None
        news = rt.news.latest(20) if rt.news else []
        extra = rt.market.oi_extra(underlying)
        results = await asyncio.to_thread(self._scan_all, feat, underlying, extra)
        out: list[Signal] = []
        for sid, raw in results.items():
            strat = rt.strategies.get(sid)
            if strat is None:
                continue
            if isinstance(raw, str):
                state = "disabled" if not rt.strategy_enabled(sid, underlying) else "no_setup"
                agent.set_watch(sid, underlying, state, raw)
                continue
            stats = self.stats_for(sid, underlying)
            sig = rt.composer.compose(raw, feat.iloc[-1], context, stats, regime, news=news,
                                      now=rt.clock.now())
            self._align_ev(sig, stats)
            if stats.get("synthetic"):
                sig.reasons.insert(0, SYNTHETIC_NOTE)
            if sig.status == "active" and not rt.strategy_enabled(sid, underlying):
                sig.status = "blocked"
                sig.blocked_reason = f"Strategy not validated for {underlying} (enable it in Strategy Lab to override)"
            agent.set_watch(sid, underlying, "signal" if sig.status == "active" else "blocked",
                            sig.blocked_reason or f"{raw.side.value} at {raw.entry:,.1f}")
            p = f"{sig.probability * 100:.0f}% (n={sig.sample_size})" if sig.probability is not None else "insufficient data"
            await agent.log("signal", f"{strat.name} {raw.side.value} {underlying} at {raw.entry:,.1f} "
                            f"(SL {raw.stop:,.1f}) · P {p} · "
                            + (f"BLOCKED: {sig.blocked_reason}" if sig.status != "active"
                               else f"mode {rt.execution_mode(sid).value}"),
                            "info" if sig.status == "active" else "warning")
            await self.publish(sig, strat.name)
            out.append(sig)
        bar_t = feat.index[-1].strftime("%H:%M")
        n_no = sum(1 for v in results.values() if isinstance(v, str))
        await agent.log("scan", f"{underlying} {bar_t} bar: scanned {len(results)} strategies — "
                        f"{len(out)} signal(s), {n_no} no setup · regime {regime.replace('_', ' ')}"
                        + (" · context risk-off" if context is not None and context.risk_off else ""))
        return out

    def _align_ev(self, sig: Signal, stats: dict[str, Any]) -> None:
        """Prefer the backtested expectancy (after costs, includes partial/time exits) for the ₹ expected value,
        so the R and ₹ figures shown together always agree."""
        exp_r = stats.get("expectancy_r")
        if exp_r is None or int(stats.get("n") or 0) < self.rt.settings.strategies.min_trades_any:
            return
        inst = sig.instrument
        pv = 0.5 if inst is not None and inst.kind.value == "OPTIDX" else 1.0
        qty = sig.quantity or (inst.lot_size if inst is not None else 1)
        risk_rs = sig.raw.risk_points * pv * qty
        sig.ev_per_trade = round(float(exp_r) * risk_rs, 2)
        sig.reasons.append(f"Expected value {float(exp_r):+.2f}R × ₹{risk_rs:,.0f} at risk = ₹{sig.ev_per_trade:,.0f} "
                           f"(backtested expectancy after costs)")

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
