"""IST clock and NSE market calendar. Injectable so backtests / replay can control time."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)
PRE_OPEN = time(9, 0)


def parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


class Clock:
    """Real-time clock. Subclass / replace with ``SimClock`` in backtests and replay."""

    def __init__(self, holidays: set[date] | None = None) -> None:
        self.holidays: set[date] = set(holidays or ())

    def now(self) -> datetime:
        return datetime.now(IST)

    def today(self) -> date:
        return self.now().date()

    def is_trading_day(self, d: date | None = None) -> bool:
        d = d or self.today()
        return d.weekday() < 5 and d not in self.holidays

    def is_market_open(self, at: datetime | None = None) -> bool:
        at = at or self.now()
        return self.is_trading_day(at.date()) and MARKET_OPEN <= at.time() < MARKET_CLOSE

    def minutes_since_open(self, at: datetime | None = None) -> float:
        at = at or self.now()
        open_dt = datetime.combine(at.date(), MARKET_OPEN, IST)
        return (at - open_dt).total_seconds() / 60

    def minutes_to_close(self, at: datetime | None = None) -> float:
        at = at or self.now()
        close_dt = datetime.combine(at.date(), MARKET_CLOSE, IST)
        return (close_dt - at).total_seconds() / 60

    def next_trading_day(self, d: date | None = None) -> date:
        d = (d or self.today()) + timedelta(days=1)
        while not self.is_trading_day(d):
            d += timedelta(days=1)
        return d

    def previous_trading_day(self, d: date | None = None) -> date:
        d = (d or self.today()) - timedelta(days=1)
        while not self.is_trading_day(d):
            d -= timedelta(days=1)
        return d


class SimClock(Clock):
    """Manually advanced clock for backtests, replay and tests."""

    def __init__(self, start: datetime, holidays: set[date] | None = None) -> None:
        super().__init__(holidays)
        self._now = start if start.tzinfo else start.replace(tzinfo=IST)

    def now(self) -> datetime:
        return self._now

    def set(self, at: datetime) -> None:
        self._now = at if at.tzinfo else at.replace(tzinfo=IST)

    def advance(self, **kwargs) -> None:
        self._now += timedelta(**kwargs)
