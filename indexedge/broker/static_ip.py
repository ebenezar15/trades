"""Static-IP verification. Angel One only accepts orders from the registered (IPv4) static IP, so a mismatch
must block live orders (paper trading is unaffected)."""
from __future__ import annotations

import ipaddress
import logging

import httpx

log = logging.getLogger(__name__)

DEFAULT_IP_URL = "https://api.ipify.org"


async def public_ip(url: str = DEFAULT_IP_URL, client: httpx.AsyncClient | None = None,
                    timeout: float = 5.0) -> str | None:
    """Return this machine's public IP as seen by ``url`` (plain text or JSON ``{"ip": ...}``), or None."""
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=timeout) as c:
                resp = await c.get(url)
        else:
            resp = await client.get(url, timeout=timeout)
        resp.raise_for_status()
        text = resp.text.strip()
        if text.startswith("{"):
            text = str(resp.json().get("ip", "")).strip()
        return str(ipaddress.ip_address(text))
    except Exception as exc:  # network errors, bad payloads
        log.warning("public IP lookup failed: %s", exc.__class__.__name__)
        return None


async def check_static_ip(expected: str, url: str = DEFAULT_IP_URL,
                          client: httpx.AsyncClient | None = None) -> dict:
    """Compare the public IP with the registered static IP -> ``dict(ok, public_ip, expected, message)``."""
    expected = (expected or "").strip()
    ip = await public_ip(url, client=client)
    if not expected:
        return {"ok": False, "public_ip": ip, "expected": "",
                "message": "No static IP configured - live orders are blocked (paper trading unaffected)."}
    if ip is None:
        return {"ok": False, "public_ip": None, "expected": expected,
                "message": "Could not determine public IP - live orders are blocked until it can be verified."}
    if ip != expected:
        extra = " (IPv6 detected; Angel One requires the registered IPv4 address)" if ":" in ip else ""
        return {"ok": False, "public_ip": ip, "expected": expected,
                "message": f"Public IP {ip} does not match registered static IP {expected}{extra} - "
                           "live orders are blocked."}
    return {"ok": True, "public_ip": ip, "expected": expected,
            "message": "Public IP matches the registered static IP."}
