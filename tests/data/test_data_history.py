import sys
import types
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from indexedge.broker.paper import PaperBroker
from indexedge.broker.rate_limiter import RateLimiter
from indexedge.core.clock import IST, SimClock
from indexedge.data.history import INTERVALS, HistoryService, chunk_ranges, chunks_for, yfinance_download
from indexedge.data.instruments import InstrumentMaster
from indexedge.data.store import CandleStore
from indexedge.data.synthetic import generate_intraday, synthetic_master_rows

FULL = generate_intraday(days=200, interval_min=5, seed=2, start=date(2026, 1, 1))


def _source(calls):
    def src(exchange, token, interval, start, end):
        calls.append((exchange, token, interval, start, end))
        return FULL[(FULL.index >= start) & (FULL.index <= end)]
    return src


def test_chunk_ranges_respect_limits():
    s = datetime(2026, 1, 1, tzinfo=IST)
    e = s + timedelta(days=250)
    for tf, (_, max_days) in INTERVALS.items():
        chunks = chunks_for(tf, s, e)
        assert chunks[0][0] == s and chunks[-1][1] == e
        assert all(b - a < timedelta(days=max_days) for a, b in chunks)
        assert all(chunks[i][1] == chunks[i + 1][0] for i in range(len(chunks) - 1))
    assert len(chunks_for("1m", s, e)) == 9
    assert chunk_ranges(e, s, 30) == []
    with pytest.raises(ValueError):
        chunks_for("2m", s, e)


async def test_backfill_chunked_and_incremental(tmp_path):
    calls = []
    end = datetime(2026, 9, 1, 15, 30, tzinfo=IST)
    broker = PaperBroker(SimClock(end), candle_source=_source(calls))
    store = CandleStore(tmp_path)
    master = InstrumentMaster.from_json(synthetic_master_rows(date(2026, 1, 1)))
    fast = RateLimiter(1000, burst=1000)
    svc = HistoryService(broker, store, master, clock=SimClock(end), limiter=fast)
    n = await svc.backfill("NIFTY", "1m", days=90)
    expected = FULL[(FULL.index >= end - timedelta(days=90)) & (FULL.index <= end)]
    assert n == len(expected) and len(calls) == 4
    assert {c[:3] for c in calls} == {("NSE", "99926000", "1m")}
    pd.testing.assert_frame_equal(store.load("NIFTY", "1m"), expected, check_freq=False)

    calls.clear()
    n2 = await svc.backfill("NIFTY", "1m", days=90)  # incremental: only from the last stored bar
    assert len(calls) == 1 and calls[0][3] == expected.index[-1] and n2 <= 1


async def test_backfill_skips_failed_chunks(tmp_path):
    end = datetime(2026, 9, 1, tzinfo=IST)
    n_calls = {"n": 0}

    def flaky(exchange, token, interval, start, end_):
        n_calls["n"] += 1
        if n_calls["n"] == 1:
            raise RuntimeError("boom")
        return FULL[(FULL.index >= start) & (FULL.index <= end_)]

    svc = HistoryService(PaperBroker(candle_source=flaky), CandleStore(tmp_path), None, clock=SimClock(end),
                         limiter=RateLimiter(1000, burst=1000))
    n = await svc.backfill("BANKNIFTY", "5m", days=150)
    assert svc.errors == 1 and n > 0


def test_yfinance_offline_safe(monkeypatch):
    fake = types.ModuleType("yfinance")

    def boom(*a, **k):
        raise ConnectionError("offline")

    fake.download = boom
    monkeypatch.setitem(sys.modules, "yfinance", fake)
    out = yfinance_download("^NSEI", "5m", "5d")
    assert out.empty and list(out.columns) == ["open", "high", "low", "close", "volume"]


def test_yfinance_normalises_multiindex(monkeypatch):
    idx = pd.date_range("2026-10-15 03:45", periods=3, freq="5min", tz="UTC")
    cols = pd.MultiIndex.from_product([["Open", "High", "Low", "Close", "Adj Close", "Volume"], ["^NSEI"]])
    raw = pd.DataFrame([[1, 2, 0.5, 1.5, 1.5, 10]] * 3, index=idx, columns=cols)
    fake = types.ModuleType("yfinance")
    fake.download = lambda *a, **k: raw
    monkeypatch.setitem(sys.modules, "yfinance", fake)
    out = yfinance_download("^NSEI")
    assert list(out.columns) == ["open", "high", "low", "close", "volume"]
    assert out.index[0] == pd.Timestamp("2026-10-15 09:15", tz=IST)
