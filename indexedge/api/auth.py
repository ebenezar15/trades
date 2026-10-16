"""LAN access control. Requests from this PC (loopback) are trusted. Any other device (your phone on home
Wi-Fi) must first open the one-time pairing link shown in Settings, which stores an access cookie."""
from __future__ import annotations

import hmac
import secrets
import socket
from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

COOKIE = "ie_token"
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def load_or_create_token(data_dir: Path) -> str:
    path = Path(data_dir) / "access_token"
    if path.exists():
        tok = path.read_text().strip()
        if len(tok) >= 16:
            return tok
    tok = secrets.token_urlsafe(24)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tok)
    return tok


def is_local(host: str | None) -> bool:
    return (host or "") in LOCAL_HOSTS


def token_ok(candidate: str | None, token: str) -> bool:
    return bool(candidate) and hmac.compare_digest(str(candidate), token)


def lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


DENIED = """<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">
<body style="font-family:system-ui;background:#0b1220;color:#e5e7eb;padding:24px;max-width:520px;margin:auto">
<h2>IndexEdge — device not paired</h2>
<p>For your safety only paired devices can open IndexEdge. On the PC running IndexEdge open
<b>Settings → Phone access</b> and open the pairing link (or scan its QR code) on this device.</p></body>"""


async def auth_middleware(request: Request, call_next) -> Response:  # noqa: ANN001
    token: str = request.app.state.access_token
    host = request.client.host if request.client else None
    if is_local(host) or token_ok(request.cookies.get(COOKIE), token):
        return await call_next(request)
    q = request.query_params.get("token")
    if token_ok(q, token):
        resp = RedirectResponse(url=request.url.path or "/", status_code=303)
        resp.set_cookie(COOKIE, token, max_age=3600 * 24 * 365, httponly=True, samesite="strict")
        return resp
    if request.url.path.startswith("/api"):
        return Response('{"detail":"device not paired"}', status_code=401, media_type="application/json")
    return HTMLResponse(DENIED, status_code=401)
