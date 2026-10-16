"""Free remote access: a Cloudflare "quick tunnel" (no account) gives the PC a temporary
``https://<random>.trycloudflare.com`` address, so the iPad can reach IndexEdge from anywhere.

The address changes every time the tunnel starts; it is shown in Settings and sent to Telegram. A password must
be set first — every request through the tunnel needs a login.
"""
from __future__ import annotations

import asyncio
import logging
import platform
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

RELEASE = "https://github.com/cloudflare/cloudflared/releases/latest/download/"
# the public link looks like https://<words>.trycloudflare.com — never the api. host seen in error messages
URL_RE = re.compile(r"https://(?!api\.)[a-z0-9]+(?:-[a-z0-9]+)+\.trycloudflare\.com")


def asset_name() -> str | None:
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "amd64"
    if sys.platform.startswith("win"):
        return f"cloudflared-windows-{arch}.exe"
    if sys.platform.startswith("linux"):
        return f"cloudflared-linux-{arch}"
    return None          # macOS: install with `brew install cloudflared`


class Tunnel:
    def __init__(self, data_dir: Path, port: int, on_url: Any = None) -> None:
        self.dir = Path(data_dir) / "bin"
        self.port = port
        self.on_url = on_url                 # async callback(url)
        self.status = "off"
        self.url: str | None = None
        self.message = ""
        self.enabled = False
        self._proc: subprocess.Popen | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------ binary
    def binary(self) -> Path | None:
        import shutil

        found = shutil.which("cloudflared")
        if found:
            return Path(found)
        name = asset_name()
        if name is None:
            return None
        return self.dir / ("cloudflared.exe" if name.endswith(".exe") else "cloudflared")

    async def ensure_binary(self) -> Path:
        import shutil

        installed = shutil.which("cloudflared")
        if installed:                        # user-installed copy: use as-is, never overwrite
            return Path(installed)
        path = self.binary()
        if path is None:
            raise RuntimeError("Install cloudflared first (macOS: brew install cloudflared)")
        if path.exists() and path.stat().st_size > 5_000_000:
            return path
        import httpx

        self.message = "Downloading Cloudflare connector (one time, ~40 MB)…"
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        async with httpx.AsyncClient(follow_redirects=True, timeout=120) as c:
            async with c.stream("GET", RELEASE + str(asset_name())) as r:
                r.raise_for_status()
                with tmp.open("wb") as f:
                    async for chunk in r.aiter_bytes():
                        f.write(chunk)
        if tmp.stat().st_size < 5_000_000:
            tmp.unlink(missing_ok=True)
            raise RuntimeError("cloudflared download looks incomplete")
        tmp.replace(path)
        if not sys.platform.startswith("win"):
            path.chmod(0o755)
        return path

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self.enabled = True
        self._loop = asyncio.get_running_loop()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._supervise())

    async def stop(self) -> None:
        self.enabled = False
        if self._task:
            self._task.cancel()
        self._kill()
        self.status, self.url, self.message = "off", None, ""

    def _kill(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None

    async def _supervise(self) -> None:
        backoff = 5
        while self.enabled:
            try:
                self.status, self.url = "starting", None
                path = await self.ensure_binary()
                self.message = "Starting secure tunnel…"
                await asyncio.to_thread(self._run, path)        # blocks until the process exits
                if self.enabled:
                    self.status = "error"
                    self.message = self.message if self.message.startswith("Tunnel stopped") \
                        else "Tunnel stopped — restarting…"
            except asyncio.CancelledError:
                self._kill()
                raise
            except Exception as exc:  # noqa: BLE001
                self.status, self.message = "error", str(exc)
                log.warning("tunnel error: %s", exc)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 300)

    def _run(self, path: Path) -> None:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._proc = subprocess.Popen(
            [str(path), "tunnel", "--no-autoupdate", "--url", f"http://localhost:{self.port}"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, creationflags=flags)
        assert self._proc.stdout is not None
        last_err = ""
        for line in self._proc.stdout:
            low = line.lower()
            if "failed" in low or " err " in low:
                last_err = line.strip()[-200:]
            m = URL_RE.search(line)
            if m and m.group(0) != self.url:
                self.url = m.group(0)
                self.status, self.message = "on", "Reachable from anywhere (login required)"
                log.info("remote access at %s", self.url)
                if self.on_url and self._loop:
                    asyncio.run_coroutine_threadsafe(self.on_url(self.url), self._loop)
        self._proc.wait()
        self.url = None
        if last_err:
            self.message = f"Tunnel stopped: {last_err} — retrying"

    def info(self, password_set: bool) -> dict[str, Any]:
        return {"enabled": self.enabled, "status": self.status, "url": self.url, "message": self.message,
                "password_set": password_set, "checked_at": time.time()}


__all__ = ["Tunnel", "asset_name", "URL_RE", "threading"]
