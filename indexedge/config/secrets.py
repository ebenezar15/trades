"""Secrets handling: OS keyring first (Windows Credential Manager), ``.env`` / env vars as fallback.

Secret names: ANGEL_API_KEY, ANGEL_CLIENT_CODE, ANGEL_PIN, ANGEL_TOTP_SECRET, TELEGRAM_BOT_TOKEN.
Never log secret values.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

log = logging.getLogger(__name__)

SERVICE = "IndexEdge"
SECRET_NAMES = ["ANGEL_API_KEY", "ANGEL_CLIENT_CODE", "ANGEL_PIN", "ANGEL_TOTP_SECRET", "TELEGRAM_BOT_TOKEN"]

_env_loaded = False


def _load_env() -> None:
    global _env_loaded
    if not _env_loaded:
        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
        _env_loaded = True


def _keyring():
    try:
        import keyring
        from keyring.backends import fail

        if isinstance(keyring.get_keyring(), fail.Keyring):
            return None
        return keyring
    except Exception:  # pragma: no cover - backend missing
        return None


def get_secret(name: str) -> str | None:
    kr = _keyring()
    if kr is not None:
        try:
            val = kr.get_password(SERVICE, name)
            if val:
                return val
        except Exception:
            log.debug("keyring read failed for %s", name)
    _load_env()
    return os.environ.get(name) or None


def set_secret(name: str, value: str) -> str:
    """Store a secret. Returns the backend used ('keyring' or 'env')."""
    if name not in SECRET_NAMES:
        raise ValueError(f"unknown secret {name}")
    kr = _keyring()
    if kr is not None:
        try:
            kr.set_password(SERVICE, name, value)
            return "keyring"
        except Exception:
            log.warning("keyring write failed; falling back to .env")
    env_path = Path(__file__).resolve().parents[2] / ".env"
    lines = env_path.read_text().splitlines() if env_path.exists() else []
    lines = [ln for ln in lines if not ln.startswith(f"{name}=")]
    lines.append(f"{name}={value}")
    env_path.write_text("\n".join(lines) + "\n")
    os.environ[name] = value
    return "env"


def secrets_status() -> dict[str, bool]:
    """Which secrets are configured (never returns values)."""
    return {n: bool(get_secret(n)) for n in SECRET_NAMES}


def redact(text: str) -> str:
    for n in SECRET_NAMES:
        v = get_secret(n)
        if v and len(v) >= 4:
            text = text.replace(v, "***")
    return text
