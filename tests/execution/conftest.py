"""Fixtures for execution tests (harness lives in exec_harness.py)."""
from __future__ import annotations

import pytest
from exec_harness import Harness, build


@pytest.fixture
async def h():
    harness = build()
    await harness.broker.subscribe_ticks([], harness.tm.on_tick)
    yield harness
    harness.journal.close()


@pytest.fixture
def make_harness():
    made = []

    async def _make(**kw) -> Harness:
        harness = build(**kw)
        await harness.broker.subscribe_ticks([], harness.tm.on_tick)
        made.append(harness)
        return harness

    yield _make
    for x in made:
        x.journal.close()
