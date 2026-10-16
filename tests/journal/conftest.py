from __future__ import annotations

from datetime import datetime

import pytest

from indexedge.core.clock import IST
from indexedge.journal.db import Journal
from indexedge.journal.models import TradeRecord


def ist(y: int, mo: int, d: int, h: int = 10, mi: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=IST)


def make_trade(**kw) -> TradeRecord:
    base = {
        "signal_id": "sig1",
        "strategy_id": "orb15",
        "underlying": "NIFTY",
        "instrument_symbol": "NIFTY26OCTFUT",
        "side": "BUY",
        "qty": 75,
        "entry_ts": ist(2026, 10, 15, 10, 0),
        "entry_price": 25000.0,
        "stop": 24950.0,
        "targets": [25100.0],
        "mode": "paper",
    }
    base.update(kw)
    return TradeRecord(**base)


@pytest.fixture
def mk_trade():
    return make_trade


@pytest.fixture
def journal():
    j = Journal(":memory:")
    yield j
    j.close()
