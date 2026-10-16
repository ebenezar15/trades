"""Angel One connection helper: what to type in the SmartAPI "Create App" form, and a step-by-step
connection test (never places orders)."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from email.utils import parsedate_to_datetime
from typing import Any

from indexedge.broker.static_ip import check_static_ip, public_ip
from indexedge.config.secrets import get_secret, secrets_status

log = logging.getLogger(__name__)

REDIRECT_URL = "https://smartapi.angelone.in/"
BROKER_SECRETS = ("ANGEL_API_KEY", "ANGEL_CLIENT_CODE", "ANGEL_PIN", "ANGEL_TOTP_SECRET")

# Common SmartAPI login errors -> what the user should do (codes as reported by users/forum; message also shown)
HINTS = {
    "AB1050": "Invalid TOTP — check the TOTP secret (the text key, not the 6-digit code) and your PC clock.",
    "AB1007": "Invalid client code or PIN.",
    "AB1004": "Angel One rejected the request — check API key and that the app type is 'Trading APIs'.",
    "AG8001": "Session token invalid — press Test again.",
    "AB8050": "Invalid API key — copy the API key of your SmartAPI app again.",
    "AB2001": "Account issue — log in once on the Angel One app/website to check for pending actions.",
}


def _hint(text: str) -> str:
    for code, hint in HINTS.items():
        if code in text:
            return hint
    low = text.lower()
    if "totp" in low:
        return HINTS["AB1050"]
    if "api key" in low or "apikey" in low:
        return HINTS["AB8050"]
    if "password" in low or "pin" in low:
        return HINTS["AB1007"]
    return ""


async def setup_info(rt: Any) -> dict[str, Any]:
    pub = None
    try:
        pub = await public_ip(rt.settings.broker.ip_check_url)
    except Exception:  # noqa: BLE001
        pub = None
    st = secrets_status()
    connected = bool(rt.angel and rt.angel.connected)
    fields = [
        {"field": "App type", "value": "Trading APIs", "note": "Choose 'Trading APIs' (not Publisher APIs)."},
        {"field": "App name", "value": "IndexEdge", "note": "Any name."},
        {"field": "Redirect URL", "value": REDIRECT_URL,
         "note": "Angel One requires an https web address — localhost and IP addresses are rejected. IndexEdge logs "
                 "in with PIN + TOTP and never uses this URL, so Angel One's own site is a safe choice."},
        {"field": "Postback URL", "value": "", "note": "Leave empty (optional)."},
        {"field": "Primary static IP",
         "value": pub or rt.settings.broker.static_ip or "your public IPv4 — open https://api.ipify.org to see it",
         "note": "Your PC's public IPv4. Needed only for placing live orders; market data works without it. "
                 "Ask your internet provider for a static IP before going live."},
    ]
    steps = [
        {"id": "app", "title": "Create the SmartAPI app (Trading APIs)", "done": bool(st["ANGEL_API_KEY"])},
        {"id": "totp", "title": "Enable TOTP and copy the text key", "done": bool(st["ANGEL_TOTP_SECRET"])},
        {"id": "secrets", "title": "Save API key, client code, PIN and TOTP secret",
         "done": all(st[k] for k in BROKER_SECRETS)},
        {"id": "test", "title": "Test the connection", "done": bool(rt.jobs.get("broker_test", {}).get("ok"))},
        {"id": "connect", "title": "Connect for real-time data", "done": connected},
    ]
    return {"connected": connected, "configured": all(st[k] for k in BROKER_SECRETS),
            "data_source": rt.market.data_source, "public_ip": pub, "static_ip": rt.settings.broker.static_ip,
            "smartapi_fields": fields, "secrets": st, "steps": steps}


async def _clock_skew_s() -> float | None:
    """Seconds between this PC's clock and an internet clock (TOTP fails if > ~30 s)."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.head("https://smartapi.angelone.in/")
        remote = parsedate_to_datetime(r.headers["date"]).timestamp()
        return time.time() - remote
    except Exception:  # noqa: BLE001
        return None


async def connection_test(rt: Any) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []

    def add(name: str, ok: bool | None, detail: str) -> None:
        steps.append({"name": name, "ok": ok, "detail": detail})

    st = secrets_status()
    missing = [k for k in BROKER_SECRETS if not st[k]]
    add("Secrets saved", not missing, "All four saved" if not missing else f"Missing: {', '.join(missing)}")
    if missing:
        return {"ok": False, "steps": steps}

    skew = await _clock_skew_s()
    if skew is None:
        add("PC clock", None, "Could not check (no internet time) — make sure Windows time is set automatically")
    else:
        add("PC clock", abs(skew) < 20, f"{skew:+.0f} s vs internet time"
            + ("" if abs(skew) < 20 else " — fix: Windows Settings → Time → Sync now (TOTP depends on it)"))
    try:
        import pyotp

        code = pyotp.TOTP(str(get_secret("ANGEL_TOTP_SECRET")).replace(" ", "")).now()
        add("TOTP secret format", True, f"Generates codes ({code[:2]}••••)")
    except Exception as exc:  # noqa: BLE001
        add("TOTP secret format", False, f"Not a valid TOTP text key: {exc}")
        return {"ok": False, "steps": steps}

    from indexedge.broker.angel_one import AngelOneBroker

    broker = AngelOneBroker(rt.settings, get_secret, rt.clock, dry_run=True, order_updates=False,
                            master=getattr(rt, "master", None))
    ok = True
    try:
        try:
            await asyncio.wait_for(broker.connect(), 30)
            add("Login (client code + PIN + TOTP)", True, "Logged in")
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            hint = _hint(msg)
            add("Login (client code + PIN + TOTP)", False, msg + (f" — {hint}" if hint else ""))
            return {"ok": False, "steps": steps}
        idx = rt.settings.broker.index_tokens
        for u, sym in (("NIFTY", "Nifty 50"), ("BANKNIFTY", "Nifty Bank")):
            try:
                px = await broker.get_ltp("NSE", idx[u], sym)
                add(f"Live price {u}", px > 0, f"{px:,.2f}")
            except Exception as exc:  # noqa: BLE001
                ok = False
                add(f"Live price {u}", False, str(exc))
        try:
            end = rt.clock.now()
            df = await broker.get_candles("NSE", idx["NIFTY"], "5m", end - timedelta(days=5), end)
            add("Historical candles", not df.empty, f"{len(df)} five-minute candles (last 5 days)")
        except Exception as exc:  # noqa: BLE001
            ok = False
            add("Historical candles", False, str(exc))
        try:
            f = await broker.funds()
            add("Funds", True, f"Available ₹{f.available_cash:,.0f}")
        except Exception as exc:  # noqa: BLE001
            add("Funds", None, f"Could not read funds: {exc}")
    finally:
        try:
            await broker.disconnect()
        except Exception:  # noqa: BLE001
            pass
    exp = rt.settings.broker.static_ip
    if exp:
        ip = await check_static_ip(exp, rt.settings.broker.ip_check_url)
        add("Static IP (live orders only)", bool(ip.get("ok")), ip.get("message", ""))
    else:
        add("Static IP (live orders only)", None, "Not set — fine for data and paper trading; required before Live")
    result = {"ok": ok, "steps": steps}
    rt.jobs["broker_test"] = {"ok": ok, "at": rt.clock.now().isoformat()}
    return result
