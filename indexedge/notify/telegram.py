"""Telegram bot notifier: alerts, EOD reports and one-tap confirm buttons.

* ``send(text, buttons)`` posts a MarkdownV2 message (text is escaped by default) with an optional inline
  keyboard; returns ``True`` on success, ``False`` on any failure or when not configured (no-op).
* ``poll_callbacks(handler)`` long-polls ``getUpdates`` and calls ``handler(callback_data)`` for button taps
  **from the configured chat only**, then acknowledges the tap.

The bot token is fetched lazily from ``token_getter`` (the secrets store) and is never logged: exception
messages are reduced to their type and httpx request logs are redacted.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

log = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
MAX_MESSAGE_CHARS = 4096
MAX_CALLBACK_BYTES = 64
_MDV2_SPECIAL = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")
_TOKEN_RE = re.compile(r"/bot[^/\s]+/")

CallbackHandler = Callable[[str], Awaitable[Any] | Any]


def escape_markdown(text: str) -> str:
    """Escape Telegram MarkdownV2 special characters."""
    return _MDV2_SPECIAL.sub(r"\\\1", text)


def redact(text: str) -> str:
    return _TOKEN_RE.sub("/bot<redacted>/", text)


class _RedactTokenFilter(logging.Filter):
    """httpx logs full request URLs at INFO; Telegram URLs contain the bot token."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 - best-effort, must never raise
            return True
        if "/bot" in msg:
            record.msg, record.args = redact(msg), ()
        return True


_filter = _RedactTokenFilter()
for _name in ("httpx", "httpcore"):
    if not any(isinstance(f, _RedactTokenFilter) for f in logging.getLogger(_name).filters):
        logging.getLogger(_name).addFilter(_filter)


class TelegramNotifier:
    def __init__(self, token_getter: Callable[[], str | None], chat_id: str | int | None,
                 client: httpx.AsyncClient | None = None, base_url: str = API_BASE,
                 timeout_s: float = 15.0) -> None:
        self._token_getter = token_getter
        self.chat_id = str(chat_id).strip() if chat_id not in (None, "") else ""
        self._client = client
        self._own_client = client is None
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self._offset: int | None = None
        self.last_poll_failed = False
        self._stop = asyncio.Event()

    # ------------------------------------------------------------------ plumbing
    def _token(self) -> str | None:
        try:
            tok = self._token_getter()
        except Exception:  # noqa: BLE001 - best-effort, must never raise
            return None
        return tok.strip() if tok else None

    @property
    def configured(self) -> bool:
        return bool(self.chat_id and self._token())

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout_s)
        return self._client

    async def _call(self, method: str, payload: dict[str, Any], timeout: float | None = None) -> Any:
        """POST a Bot API method; returns ``result`` or ``None`` on failure (never raises)."""
        token = self._token()
        if not token:
            return None
        url = f"{self.base_url}/bot{token}/{method}"
        try:
            resp = await self._get_client().post(url, json=payload, timeout=timeout or self.timeout_s)
            data = resp.json()
        except Exception as exc:  # noqa: BLE001 - best-effort, must never raise
            log.warning("telegram %s failed: %s", method, type(exc).__name__)
            return None
        if resp.status_code != 200 or not data.get("ok"):
            log.warning("telegram %s error %s: %s", method, resp.status_code,
                        redact(str(data.get("description", ""))[:200]))
            return None
        return data.get("result")

    # ------------------------------------------------------------------ public
    async def send(self, text: str, buttons: list[tuple[str, str]] | None = None,
                   escape: bool = True) -> bool:
        """Send a message to the configured chat. ``buttons`` = [(label, callback_data), ...] in one row."""
        if not self.configured:
            return False
        body = escape_markdown(text) if escape else text
        if len(body) > MAX_MESSAGE_CHARS:
            body = body[: MAX_MESSAGE_CHARS - 2].rstrip("\\") + "…"
        payload: dict[str, Any] = {"chat_id": self.chat_id, "text": body, "parse_mode": "MarkdownV2",
                                   "disable_web_page_preview": True}
        if buttons:
            row = []
            for label, data in buttons:
                if len(data.encode("utf-8")) > MAX_CALLBACK_BYTES:
                    raise ValueError(f"callback_data longer than {MAX_CALLBACK_BYTES} bytes: {data!r}")
                row.append({"text": label, "callback_data": data})
            payload["reply_markup"] = {"inline_keyboard": [row]}
        return await self._call("sendMessage", payload) is not None

    async def poll_once(self, handler: CallbackHandler, timeout: int = 25) -> int:
        """One ``getUpdates`` long-poll. Returns the number of callbacks dispatched to ``handler``."""
        if not self.configured:
            return 0
        payload: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["callback_query"]}
        if self._offset is not None:
            payload["offset"] = self._offset
        updates = await self._call("getUpdates", payload, timeout=timeout + 10)
        self.last_poll_failed = updates is None
        if not updates:
            return 0
        handled = 0
        for upd in updates:
            self._offset = max(self._offset or 0, int(upd.get("update_id", 0)) + 1)
            cq = upd.get("callback_query")
            if not cq:
                continue
            chat = str(((cq.get("message") or {}).get("chat") or {}).get("id", ""))
            sender = str((cq.get("from") or {}).get("id", ""))
            if self.chat_id not in (chat, sender):
                log.warning("ignoring Telegram callback from unauthorised chat %s", chat or sender)
                await self._call("answerCallbackQuery", {"callback_query_id": cq.get("id"),
                                                         "text": "Not authorised"})
                continue
            text = "Done"
            try:
                res = handler(str(cq.get("data", "")))
                if asyncio.iscoroutine(res):
                    res = await res
                if isinstance(res, str) and res:
                    text = res[:190]
                handled += 1
            except Exception:
                log.exception("telegram callback handler failed")
                text = "Failed — check the app"
            await self._call("answerCallbackQuery", {"callback_query_id": cq.get("id"), "text": text})
        return handled

    async def poll_callbacks(self, handler: CallbackHandler, timeout: int = 25,
                             idle_sleep_s: float = 30.0) -> None:
        """Long-poll forever (until :meth:`stop`). Sleeps when not configured or after errors."""
        self._stop.clear()
        while not self._stop.is_set():
            if not self.configured:
                await self._sleep(idle_sleep_s)
                continue
            try:
                await self.poll_once(handler, timeout)
                if self.last_poll_failed:
                    await self._sleep(5)
            except Exception:
                log.exception("telegram poll failed")
                await self._sleep(5)

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except TimeoutError:
            pass

    def stop(self) -> None:
        self._stop.set()

    async def aclose(self) -> None:
        if self._own_client and self._client is not None:
            await self._client.aclose()
            self._client = None
