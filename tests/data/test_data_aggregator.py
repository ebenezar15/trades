from datetime import datetime

import pytest

from indexedge.core.clock import IST
from indexedge.core.types import Tick
from indexedge.data.aggregator import CandleAggregator


def _t(hh, mm, ss, ltp, vol, token="1"):
    return Tick(token=token, ts=datetime(2026, 10, 16, hh, mm, ss, tzinfo=IST), ltp=ltp, volume=vol)


async def test_builds_bars_from_cumulative_volume():
    closed = []
    agg = CandleAggregator(5, lambda tok, bar: closed.append((tok, bar)))
    for tick in [_t(9, 15, 1, 100, 1000), _t(9, 16, 0, 102, 1500), _t(9, 19, 59, 99, 1800),
                 _t(9, 20, 3, 101, 2000), _t(9, 24, 0, 103, 2600)]:
        await agg.on_tick(tick)
    assert len(closed) == 1
    tok, bar = closed[0]
    assert tok == "1" and bar.name == datetime(2026, 10, 16, 9, 15, tzinfo=IST)
    assert (bar.open, bar.high, bar.low, bar.close, bar.volume) == (100, 102, 99, 99, 1800)
    cur = agg.current("1")
    assert cur.open == 101 and cur.volume == 800  # 2600 - 1800


async def test_flush_and_async_handler():
    seen = []

    async def handler(tok, bar):
        seen.append(bar)

    agg = CandleAggregator(5, handler)
    await agg.on_tick(_t(10, 0, 5, 50, 10))  # first tick mid-session: volume base = its cumulative
    assert await agg.flush(datetime(2026, 10, 16, 10, 4, 59, tzinfo=IST)) == []
    out = await agg.flush(datetime(2026, 10, 16, 10, 5, tzinfo=IST))
    assert len(out) == 1 and seen[0].volume == 0 and agg.current("1") is None
    await agg.on_tick(_t(10, 6, 0, 51, 30))
    assert agg.current("1").volume == 20  # continues from the last cumulative value


async def test_gap_late_ticks_and_session_bounds():
    closed = []
    agg = CandleAggregator(5, lambda tok, bar: closed.append(bar))
    await agg.on_tick(_t(9, 10, 0, 1, 1))  # pre-open ignored
    assert agg.current("1") is None
    await agg.on_tick(_t(9, 15, 0, 10, 100))
    await agg.on_tick(_t(9, 31, 0, 11, 150))  # skipped buckets: no fake bars
    assert len(closed) == 1 and closed[0].volume == 100
    await agg.on_tick(_t(9, 29, 0, 99, 160))  # late tick for an older bucket ignored
    assert agg.current("1").high == 11
    await agg.on_tick(_t(15, 31, 0, 12, 200))  # after close ignored
    assert len(closed) == 1


async def test_tokens_independent_and_hourly_last_bar():
    closed = []
    agg = CandleAggregator(60, lambda tok, bar: closed.append((tok, bar)))
    await agg.on_tick(_t(15, 20, 0, 5, 0, token="A"))
    await agg.on_tick(_t(15, 20, 0, 7, 0, token="B"))
    assert agg.current("A").name.hour == 15 and agg.current("A").name.minute == 15
    out = await agg.flush(datetime(2026, 10, 16, 15, 30, tzinfo=IST))  # last 1h bar ends at the close
    assert {t for t, _ in out} == {"A", "B"}


def test_invalid_interval():
    with pytest.raises(ValueError):
        CandleAggregator(0, lambda *a: None)
