"""Offline tests for the Telegram notifier using httpx.MockTransport."""
from __future__ import annotations

import json
import logging

import httpx
import pytest

from indexedge.notify.telegram import TelegramNotifier, escape_markdown, redact

TOKEN = "123456:SECRET-token"


def make(handler, token=TOKEN, chat="42"):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return TelegramNotifier(lambda: token, chat, client=client), client


def test_escape_markdown():
    assert escape_markdown("NIFTY 25,000 CE @ 1.5 (x2)!") == r"NIFTY 25,000 CE @ 1\.5 \(x2\)\!"
    assert escape_markdown("a_b*c[d]-e") == r"a\_b\*c\[d\]\-e"
    assert redact("POST https://api.telegram.org/bot123:abc/sendMessage") == \
        "POST https://api.telegram.org/bot<redacted>/sendMessage"


async def test_send_with_buttons():
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    n, client = make(handler)
    assert await n.send("Buy NIFTY 25000 CE @ 101.5", buttons=[("Confirm", "confirm:sig1"), ("Skip", "skip:sig1")])
    path, body = sent[0]
    assert path == f"/bot{TOKEN}/sendMessage"
    assert body["chat_id"] == "42" and body["parse_mode"] == "MarkdownV2"
    assert body["text"] == r"Buy NIFTY 25000 CE @ 101\.5"
    assert body["reply_markup"]["inline_keyboard"][0][1] == {"text": "Skip", "callback_data": "skip:sig1"}
    with pytest.raises(ValueError):
        await n.send("x", buttons=[("Go", "x" * 65)])
    await client.aclose()


async def test_not_configured_is_noop():
    def handler(request):
        raise AssertionError("must not call network")

    n, client = make(handler, token=None)
    assert not n.configured and await n.send("hi") is False
    assert await n.poll_once(lambda d: None) == 0
    n2, client2 = make(handler, chat="")
    assert await n2.send("hi") is False
    await client.aclose()
    await client2.aclose()


async def test_send_failure_returns_false_and_never_logs_token(caplog):
    def bad(request):
        return httpx.Response(400, json={"ok": False, "description": "Bad Request: can't parse entities"})

    def boom(request):
        raise httpx.ConnectError(f"cannot reach {request.url}")

    caplog.set_level(logging.DEBUG)
    n, client = make(bad)
    assert await n.send("x") is False
    n2, client2 = make(boom)
    assert await n2.send("x") is False
    assert TOKEN not in caplog.text
    await client.aclose()
    await client2.aclose()


async def test_poll_callbacks_dispatches_only_authorised_chat():
    updates = [
        {"update_id": 10, "callback_query": {"id": "cq1", "data": "confirm:sig1",
                                             "from": {"id": 42}, "message": {"chat": {"id": 42}}}},
        {"update_id": 11, "callback_query": {"id": "cq2", "data": "confirm:sig2",
                                             "from": {"id": 666}, "message": {"chat": {"id": 666}}}},
        {"update_id": 12, "message": {"text": "hello"}},
    ]
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method = request.url.path.rsplit("/", 1)[-1]
        calls.append((method, body))
        if method == "getUpdates":
            return httpx.Response(200, json={"ok": True, "result": updates if "offset" not in body else []})
        return httpx.Response(200, json={"ok": True, "result": True})

    got = []

    async def on_cb(data: str) -> str:
        got.append(data)
        return "Confirmed"

    n, client = make(handler)
    assert await n.poll_once(on_cb, timeout=1) == 1
    assert got == ["confirm:sig1"]
    answers = [b for m, b in calls if m == "answerCallbackQuery"]
    assert answers == [{"callback_query_id": "cq1", "text": "Confirmed"},
                       {"callback_query_id": "cq2", "text": "Not authorised"}]
    assert await n.poll_once(on_cb, timeout=1) == 0
    assert [b for m, b in calls if m == "getUpdates"][-1]["offset"] == 13
    await client.aclose()


async def test_poll_handler_exception_is_contained():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [
                {"update_id": 1, "callback_query": {"id": "c", "data": "x", "from": {"id": 42}}}]})
        return httpx.Response(200, json={"ok": True, "result": True})

    def bad_handler(data):
        raise RuntimeError("boom")

    n, client = make(handler)
    assert await n.poll_once(bad_handler, timeout=1) == 0
    await client.aclose()
