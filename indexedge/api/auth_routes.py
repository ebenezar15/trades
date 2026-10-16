"""Login, first-password setup, logout and password change."""
from __future__ import annotations

import hmac

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse

from indexedge.api.auth import (
    SESSION_COOKIE,
    SESSION_DAYS,
    AuthStore,
    client_ip,
    is_https,
    is_local,
    request_authorised,
)

router = APIRouter(prefix="/api/auth")


def _auth(request: Request) -> AuthStore:
    return request.app.state.auth


def _local(request: Request) -> bool:
    return is_local(request.client.host if request.client else None, request.headers)


def _login_response(request: Request, payload: dict) -> JSONResponse:
    auth = _auth(request)
    resp = JSONResponse(payload)
    resp.set_cookie(SESSION_COOKIE, auth.new_session(), max_age=SESSION_DAYS * 86400, httponly=True,
                    samesite="lax", secure=is_https(request))
    return resp


@router.get("/status")
def status(request: Request):
    auth = _auth(request)
    local = _local(request)
    rt = getattr(request.app.state, "rt", None)
    tunnel = getattr(rt, "tunnel", None) if rt else None
    out = {
        "logged_in": request_authorised(request, auth, request.app.state.access_token),
        "local": local,
        "needs_setup": not auth.password_set,
        "username": auth.username,
        "remote_url": getattr(tunnel, "url", None) if tunnel else None,
    }
    if local and not auth.password_set:
        out["setup_code"] = auth.setup_code          # shown only on the PC itself
    return out


@router.post("/setup")
def setup(request: Request, body: dict = Body(...)):
    auth = _auth(request)
    if auth.password_set:
        raise HTTPException(409, "A password already exists — log in instead")
    ip = client_ip(request)
    if auth.locked_out(ip):
        raise HTTPException(429, "Too many attempts — wait 15 minutes")
    if not _local(request):
        code = str(body.get("setup_code") or "").strip()
        if not auth.setup_code or not hmac.compare_digest(code, auth.setup_code):
            auth.record_fail(ip)
            raise HTTPException(403, "Wrong setup code — it is shown on your PC (Settings / IndexEdge window)")
    try:
        auth.set_password(str(body.get("username") or ""), str(body.get("password") or ""))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _login_response(request, {"ok": True, "username": auth.username})


@router.post("/login")
def login(request: Request, body: dict = Body(...)):
    auth = _auth(request)
    ip = client_ip(request)
    if auth.locked_out(ip):
        raise HTTPException(429, "Too many failed attempts — try again in 15 minutes")
    if not auth.password_set:
        raise HTTPException(409, "No password yet — create one first")
    if not auth.check_password(str(body.get("username") or ""), str(body.get("password") or "")):
        auth.record_fail(ip)
        raise HTTPException(401, "Wrong username or password")
    auth.clear_fails(ip)
    return _login_response(request, {"ok": True, "username": auth.username})


@router.post("/logout")
def logout(request: Request):
    _auth(request).end_session(request.cookies.get(SESSION_COOKIE))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@router.post("/password")
def change_password(request: Request, body: dict = Body(...)):
    auth = _auth(request)
    if not request_authorised(request, auth, request.app.state.access_token):
        raise HTTPException(401, "login required")
    ip = client_ip(request)
    if auth.locked_out(ip):
        raise HTTPException(429, "Too many attempts — wait 15 minutes")
    if not auth.check_password(auth.username or "", str(body.get("current") or "")):
        auth.record_fail(ip)
        raise HTTPException(403, "Current password is wrong")
    try:
        auth.set_password(auth.username or "", str(body.get("new") or ""))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _login_response(request, {"ok": True})
