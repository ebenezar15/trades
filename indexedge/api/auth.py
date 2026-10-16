"""Access control.

* Requests made on the PC itself (loopback, *without* proxy headers) are trusted.
* Every other device (iPad, phone, any browser via the free Cloudflare link) must log in with the owner's
  username + password (scrypt-hashed, stored in ``<data_dir>/auth.json``). Sessions are random tokens kept only as
  SHA-256 hashes; cookies are HttpOnly, SameSite=Lax and Secure over https.
* The first password can be created on the PC, or on another device with the 6-digit setup code shown on the PC.
* 5 failed logins per 15 minutes per client IP lock that IP out for the rest of the window.
* The older LAN pairing link (``?token=``) still works for devices paired before.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import socket
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

log = logging.getLogger(__name__)

COOKIE = "ie_token"            # legacy LAN pairing cookie
SESSION_COOKIE = "ie_session"
SESSION_DAYS = 30
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}
PROXY_HEADERS = ("x-forwarded-for", "cf-connecting-ip", "forwarded", "x-real-ip", "cf-ray")
MAX_FAILS = 5
FAIL_WINDOW_S = 15 * 60
PUBLIC_PREFIXES = ("/api/auth/", "/api/ping", "/assets/", "/icons/")
PUBLIC_FILES = {"/manifest.webmanifest", "/sw.js", "/favicon.ico", "/favicon.svg", "/apple-touch-icon.png"}


# ----------------------------------------------------------------------------------- helpers
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


def is_local(host: str | None, headers: Any = None) -> bool:
    """True only for requests made on this PC directly (a tunnel/proxy also connects from loopback!)."""
    if (host or "") not in LOCAL_HOSTS:
        return False
    if headers is not None:
        for h in PROXY_HEADERS:
            if headers.get(h):
                return False
    return True


def token_ok(candidate: str | None, token: str) -> bool:
    return bool(candidate) and bool(token) and hmac.compare_digest(str(candidate), token)


def client_ip(request: Any) -> str:
    h = request.headers
    ip = h.get("cf-connecting-ip") or (h.get("x-forwarded-for") or "").split(",")[0].strip()
    return ip or (request.client.host if request.client else "unknown")


def is_https(request: Any) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https" \
        or bool(request.headers.get("cf-ray"))


def lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def _hash(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32).hex()


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


# ----------------------------------------------------------------------------------- store
class AuthStore:
    def __init__(self, data_dir: Path) -> None:
        self.path = Path(data_dir) / "auth.json"
        self._lock = threading.Lock()
        self._fails: dict[str, list[float]] = {}
        self.data: dict[str, Any] = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text())
            except Exception:  # noqa: BLE001
                log.error("auth.json unreadable — password must be set again")
                self.data = {}
        if not self.password_set and not self.data.get("setup_code"):
            self.data["setup_code"] = f"{secrets.randbelow(10 ** 6):06d}"
            self._save()
        if not self.password_set:
            log.warning("No IndexEdge password yet. Create it on this PC, or on your iPad with setup code %s",
                        self.setup_code)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data))
        try:
            tmp.chmod(0o600)
        except OSError:
            pass
        tmp.replace(self.path)

    # -------------------------------------------------------------- password
    @property
    def password_set(self) -> bool:
        return bool(self.data.get("hash"))

    @property
    def setup_code(self) -> str | None:
        return None if self.password_set else self.data.get("setup_code")

    @property
    def username(self) -> str | None:
        return self.data.get("username")

    def set_password(self, username: str, password: str) -> None:
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters")
        if not username.strip():
            raise ValueError("Username required")
        salt = secrets.token_bytes(16)
        with self._lock:
            self.data.update(username=username.strip(), salt=salt.hex(), hash=_hash(password, salt))
            self.data.pop("setup_code", None)
            self.data["sessions"] = {}           # changing the password logs every device out
            self._save()

    def check_password(self, username: str, password: str) -> bool:
        if not self.password_set:
            return False
        ok_user = hmac.compare_digest(username.strip().lower(), str(self.username or "").lower())
        calc = _hash(password, bytes.fromhex(self.data["salt"]))
        return ok_user and hmac.compare_digest(calc, self.data["hash"])

    # -------------------------------------------------------------- sessions
    def new_session(self) -> str:
        sid = secrets.token_urlsafe(32)
        with self._lock:
            sess = {k: v for k, v in self.data.get("sessions", {}).items() if v > time.time()}
            sess[_sha(sid)] = time.time() + SESSION_DAYS * 86400
            self.data["sessions"] = sess
            self._save()
        return sid

    def session_ok(self, sid: str | None) -> bool:
        if not sid:
            return False
        exp = self.data.get("sessions", {}).get(_sha(sid))
        return bool(exp and exp > time.time())

    def end_session(self, sid: str | None) -> None:
        if not sid:
            return
        with self._lock:
            self.data.get("sessions", {}).pop(_sha(sid), None)
            self._save()

    # -------------------------------------------------------------- brute-force protection
    def locked_out(self, ip: str) -> bool:
        now = time.time()
        fails = [t for t in self._fails.get(ip, []) if now - t < FAIL_WINDOW_S]
        self._fails[ip] = fails
        return len(fails) >= MAX_FAILS

    def record_fail(self, ip: str) -> None:
        self._fails.setdefault(ip, []).append(time.time())

    def clear_fails(self, ip: str) -> None:
        self._fails.pop(ip, None)


def request_authorised(request: Any, auth: AuthStore, pairing_token: str) -> bool:
    if is_local(request.client.host if request.client else None, request.headers):
        return True
    if auth.session_ok(request.cookies.get(SESSION_COOKIE)):
        return True
    return token_ok(request.cookies.get(COOKIE), pairing_token)


def _public(path: str) -> bool:
    return path in PUBLIC_FILES or any(path.startswith(p) for p in PUBLIC_PREFIXES)


async def auth_middleware(request: Request, call_next) -> Response:  # noqa: ANN001
    auth: AuthStore = request.app.state.auth
    token: str = request.app.state.access_token
    path = request.url.path
    if _public(path) or request_authorised(request, auth, token):
        return await call_next(request)
    q = request.query_params.get("token")
    if token_ok(q, token):                                        # legacy LAN pairing link
        resp = RedirectResponse(url=path or "/", status_code=303)
        resp.set_cookie(COOKIE, token, max_age=3600 * 24 * 365, httponly=True, samesite="strict")
        return resp
    if path.startswith("/api"):
        return JSONResponse({"detail": "login required"}, status_code=401)
    return await call_next(request)          # the web app itself loads, then shows the login screen
