"""`python -m indexedge check`: dry-run Angel One login + data fetch. Never places orders."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from indexedge.broker.static_ip import check_static_ip
from indexedge.config.secrets import get_secret, secrets_status
from indexedge.config.settings import load_settings
from indexedge.core.clock import Clock


async def broker_dry_run() -> str:
    s = load_settings()
    lines: list[str] = []
    status = secrets_status()
    lines.append("Secrets: " + ", ".join(f"{k}={'set' if v else 'MISSING'}" for k, v in status.items()))
    if s.broker.static_ip:
        ip: dict[str, Any] = await check_static_ip(s.broker.static_ip, s.broker.ip_check_url)
        lines.append(f"Static IP: {'OK' if ip.get('ok') else 'MISMATCH'} — {ip.get('message')}")
    else:
        lines.append("Static IP: not configured (needed only for live orders)")
    if not all(status[k] for k in ("ANGEL_API_KEY", "ANGEL_CLIENT_CODE", "ANGEL_PIN", "ANGEL_TOTP_SECRET")):
        lines.append("Broker: skipped — enter your SmartAPI details with `python -m indexedge setup`")
        return "\n".join(lines)

    from indexedge.broker.angel_one import AngelOneBroker

    clock = Clock()
    broker = AngelOneBroker(s, get_secret, clock, dry_run=True, order_updates=False)
    try:
        await broker.connect()
        lines.append("Login: OK")
        from indexedge.core.underlyings import INFO, UNDERLYINGS, index_tokens

        toks = index_tokens(s.broker.index_tokens)
        for u in UNDERLYINGS:
            ltp = await broker.get_ltp(INFO[u].index_exchange, toks[u], INFO[u].index_symbol)
            lines.append(f"{u} LTP: {ltp:,.2f}")
        tok = toks["NIFTY"]
        end = clock.now()
        df = await broker.get_candles("NSE", tok, "5m", end - timedelta(days=5), end)
        lines.append(f"Historical 5m candles (5 days): {len(df)} rows")
        f = await broker.funds()
        lines.append(f"Funds available: ₹{f.available_cash:,.0f}")
        lines.append("Dry run complete — no orders were placed.")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"FAILED: {exc}")
    finally:
        try:
            await broker.disconnect()
        except Exception:  # noqa: BLE001
            pass
    return "\n".join(lines)
