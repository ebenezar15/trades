"""The trading agent's activity log and per-strategy watch state (what the agent is doing right now)."""
from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from typing import Any

from indexedge.core.clock import MARKET_OPEN

AGENT = "agent"     # bus topic


class AgentLog:
    def __init__(self, bus: Any, clock: Any, maxlen: int = 500) -> None:
        self.bus = bus
        self.clock = clock
        self.entries: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self.watch: dict[tuple[str, str], dict[str, Any]] = {}

    async def log(self, kind: str, message: str, level: str = "info") -> dict[str, Any]:
        entry = {"ts": self.clock.now().isoformat(), "level": level, "kind": kind, "message": message}
        self.entries.appendleft(entry)
        if self.bus is not None:
            await self.bus.publish(AGENT, entry)
        return entry

    def set_watch(self, strategy_id: str, underlying: str, state: str, detail: str) -> None:
        self.watch[(strategy_id, underlying)] = {"state": state, "detail": detail,
                                                 "last_eval_ts": self.clock.now().isoformat()}

    def next_bar_in_s(self, now: datetime | None = None) -> float | None:
        now = now or self.clock.now()
        if not self.clock.is_market_open(now):
            return None
        open_dt = datetime.combine(now.date(), MARKET_OPEN, now.tzinfo)
        elapsed = (now - open_dt).total_seconds()
        nxt = open_dt + timedelta(seconds=(int(elapsed // 300) + 1) * 300)
        return round((nxt - now).total_seconds(), 1)
