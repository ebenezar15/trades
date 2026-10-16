import asyncio
import itertools
import time

import pytest

from indexedge.broker.rate_limiter import RateLimiter


class FakeClock:
    def __init__(self):
        self.t = 1000.0
        self.sleeps = []

    def __call__(self):
        return self.t

    async def sleep(self, dt):
        self.sleeps.append(dt)
        self.t += dt


def _max_in_window(stamps, window=1.0):
    best = 0
    for i, s in enumerate(stamps):
        best = max(best, sum(1 for x in stamps[i:] if x - s < window - 1e-9))
    return best


async def test_never_more_than_rate_in_any_second():
    clk = FakeClock()
    lim = RateLimiter(8, clock=clk, sleep=clk.sleep)
    stamps = []
    for _ in range(50):
        await lim.acquire()
        stamps.append(clk.t)
    assert stamps[:8] == [1000.0] * 8  # burst allowed
    assert _max_in_window(stamps) == 8
    assert stamps[-1] - stamps[0] >= 50 / 8 - 1.0 - 1e-9


async def test_burst_one_spaces_evenly():
    clk = FakeClock()
    lim = RateLimiter(4, burst=1, clock=clk, sleep=clk.sleep)
    stamps = []
    for _ in range(5):
        await lim.acquire()
        stamps.append(clk.t)
    gaps = [b - a for a, b in itertools.pairwise(stamps)]
    assert all(g == pytest.approx(0.25) for g in gaps)


async def test_try_acquire_and_recent_count():
    clk = FakeClock()
    lim = RateLimiter(2, clock=clk, sleep=clk.sleep)
    assert lim.try_acquire() and lim.try_acquire() and not lim.try_acquire()
    assert lim.recent_count() == 2
    clk.t += 1.0
    assert lim.recent_count() == 0 and lim.try_acquire()


async def test_concurrent_callers_real_time():
    lim = RateLimiter(50, burst=5)
    stamps = []

    async def worker():
        async with lim:
            stamps.append(time.monotonic())

    t0 = time.monotonic()
    await asyncio.gather(*(worker() for _ in range(15)))
    assert len(stamps) == 15 and lim.acquired == 15
    assert time.monotonic() - t0 >= (15 - 5) / 50 * 0.9


def test_invalid_args():
    with pytest.raises(ValueError):
        RateLimiter(0)
    with pytest.raises(ValueError):
        RateLimiter(5, burst=0)
