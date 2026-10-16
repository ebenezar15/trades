"""Offline tests for quote providers (no network: injected downloader / MockTransport)."""
from __future__ import annotations

import time
from datetime import datetime

import httpx
import numpy as np
import pandas as pd
import pytest

from indexedge.context.providers import (
    GiftNiftyProvider,
    Quote,
    StaticProvider,
    YFinanceProvider,
    parse_gift_nifty,
)
from indexedge.core.clock import IST

NOW = datetime(2026, 10, 16, 9, 0, tzinfo=IST)


def test_quote_make_computes_change():
    qt = Quote.make("ES=F", 101.0, 100.0, datetime(2026, 10, 16, 3, 0))
    assert qt.change_pct == pytest.approx(1.0)
    assert qt.ts.tzinfo is not None
    assert Quote.make("X", 5.0, None, NOW).change_pct == 0.0


async def test_static_provider_omits_unknown():
    p = StaticProvider({"A": Quote.make("A", 1, 1, NOW)})
    assert set(await p.fetch(["A", "B"])) == {"A"}


def _frame() -> pd.DataFrame:
    idx = pd.date_range("2026-10-14 14:00", periods=8, freq="15min", tz="UTC").append(
        pd.date_range("2026-10-15 14:00", periods=4, freq="15min", tz="UTC"))
    cols = pd.MultiIndex.from_product([["ES=F", "INR=X"], ["Open", "High", "Low", "Close", "Volume"]])
    data = np.ones((len(idx), len(cols)))
    df = pd.DataFrame(data, index=idx, columns=cols)
    df[("ES=F", "Close")] = [6000.0] * 8 + [6050.0, 6060.0, 6070.0, 6060.0]
    df[("INR=X", "Close")] = [88.0] * 8 + [88.2, 88.3, np.nan, np.nan]
    return df


async def test_yfinance_provider_parses_batch_frame_and_skips_gift():
    calls = []

    def fake_download(symbols, period, interval):
        calls.append(list(symbols))
        return _frame()

    p = YFinanceProvider(downloader=fake_download, now_fn=lambda: NOW)
    out = await p.fetch(["ES=F", "INR=X", "GIFTNIFTY", "^MISSING"])
    assert calls == [["ES=F", "INR=X", "^MISSING"]]
    assert set(out) == {"ES=F", "INR=X"}
    es = out["ES=F"]
    assert es.price == 6060.0 and es.prev_close == 6000.0 and es.change_pct == pytest.approx(1.0)
    # last bar 2026-10-15 14:45 UTC + 15 min = 20:30 IST
    assert es.ts == datetime(2026, 10, 15, 20, 30, tzinfo=IST)
    assert out["INR=X"].price == 88.3


async def test_yfinance_provider_timeout_and_errors_return_empty():
    def slow(symbols, period, interval):
        time.sleep(0.5)
        return _frame()

    def broken(symbols, period, interval):
        raise ConnectionError("offline")

    assert await YFinanceProvider(timeout_s=0.05, downloader=slow).fetch(["ES=F"]) == {}
    assert await YFinanceProvider(downloader=broken).fetch(["ES=F"]) == {}
    assert await YFinanceProvider(downloader=lambda *a: pd.DataFrame()).fetch(["ES=F"]) == {}


def test_parse_gift_nifty_json_and_html():
    assert parse_gift_nifty('{"data": [{"lastPrice": "25,312.5", "previousClose": 25200}]}') == (25312.5, 25200.0)
    html = '<script>var q = {"ltp": 25120.0, "prevClose": "25010.00"};</script>'
    assert parse_gift_nifty(html) == (25120.0, 25010.0)
    assert parse_gift_nifty('{"lastPrice": 12}') == (None, None)    # fails sanity bound
    assert parse_gift_nifty("garbage") == (None, None)


async def test_gift_provider_best_effort():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "IndexEdge" in request.headers["user-agent"]
        return httpx.Response(200, json={"lastPrice": 25300.0, "previousClose": 25250.0})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        p = GiftNiftyProvider(url="https://example.test/gift", client=client, now_fn=lambda: NOW)
        out = await p.fetch(["GIFTNIFTY", "ES=F"])
        assert out["GIFTNIFTY"].price == 25300.0 and out["GIFTNIFTY"].ts == NOW
        assert await p.fetch(["ES=F"]) == {}

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503))) as client:
        assert await GiftNiftyProvider(url="https://example.test/gift", client=client).fetch(["GIFTNIFTY"]) == {}

    def boom(request):
        raise httpx.ConnectError("down")

    async with httpx.AsyncClient(transport=httpx.MockTransport(boom)) as client:
        assert await GiftNiftyProvider(url="https://example.test/gift", client=client).fetch(["GIFTNIFTY"]) == {}
    assert await GiftNiftyProvider(url=None).fetch(["GIFTNIFTY"]) == {}
