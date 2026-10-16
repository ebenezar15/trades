"""One-tap confirmation queue (execution mode ``confirm``).

A signal waiting for the user's approval (UI button or Telegram inline button) lives here until it is confirmed,
skipped or expires (default 60 s — a stale entry price must never be executed later). ``confirm`` returns the
signal for the caller to hand to :meth:`TradeManager.execute`; nothing here talks to the broker.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from indexedge.core.clock import Clock
from indexedge.core.types import Signal


@dataclass
class Pending:
    signal: Signal
    created_at: datetime
    expires_at: datetime


class PendingConfirmations:
    def __init__(self, clock: Clock | None = None, ttl_s: float = 60.0, journal: Any = None) -> None:
        self.clock = clock or Clock()
        self.ttl = timedelta(seconds=ttl_s)
        self.journal = journal
        self._items: dict[str, Pending] = {}

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, signal_id: str) -> bool:
        return signal_id in self._items

    def _status(self, sig: Signal, status: str, reason: str = "") -> None:
        sig.status = status
        if self.journal is not None:
            self.journal.update_signal_status(sig.id, status, reason)

    def add(self, signal: Signal) -> Pending:
        now = self.clock.now()
        p = Pending(signal, now, now + self.ttl)
        self._items[signal.id] = p
        self._status(signal, "pending_confirm")
        return p

    def get(self, signal_id: str) -> Pending | None:
        return self._items.get(signal_id)

    def pending(self) -> list[Pending]:
        self.expire()
        return sorted(self._items.values(), key=lambda p: p.created_at)

    def confirm(self, signal_id: str) -> Signal | None:
        """Pop and return the signal if it is still pending and unexpired, else ``None``."""
        self.expire()
        p = self._items.pop(signal_id, None)
        if p is None:
            return None
        self._status(p.signal, "active", "confirmed by user")
        return p.signal

    def skip(self, signal_id: str) -> bool:
        p = self._items.pop(signal_id, None)
        if p is None:
            return False
        self._status(p.signal, "skipped", "skipped by user")
        return True

    def expire(self, now: datetime | None = None) -> list[Signal]:
        now = now or self.clock.now()
        gone = [sid for sid, p in self._items.items() if now >= p.expires_at]
        out = []
        for sid in gone:
            p = self._items.pop(sid)
            self._status(p.signal, "expired", "confirmation window elapsed")
            out.append(p.signal)
        return out
