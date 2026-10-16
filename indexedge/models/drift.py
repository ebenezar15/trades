"""Live calibration drift monitor.

Tracks the Brier score of the last ``window`` live (or paper) trades and compares it with the band expected from
the model's out-of-sample validation: ``brier_oos ± z * sd(squared error) / sqrt(window)``. Live Brier *above*
the band means the model is worse than validated -> ``ok=False`` (the app auto-disables the model). Doing better
than the band is not drift. Until ``window`` outcomes have been seen the monitor reports ``ok=True`` (warming up).
"""
from __future__ import annotations

import math
from collections import deque
from typing import Any


class DriftMonitor:
    def __init__(self, window: int = 30, expected_brier: float | None = None, sq_err_std: float | None = None,
                 z: float = 1.96) -> None:
        self.window = int(window)
        self.expected_brier = expected_brier
        self.sq_err_std = sq_err_std
        self.z = z
        self._buf: deque[tuple[float, int]] = deque(maxlen=self.window)

    @classmethod
    def from_bundle(cls, bundle: Any, window: int = 30, z: float = 1.96) -> DriftMonitor:
        m = getattr(bundle, "metrics", {}) or {}
        b, sd = m.get("brier"), m.get("brier_sq_err_std")
        ok = b is not None and math.isfinite(b)
        return cls(window, float(b) if ok else None, float(sd) if ok and sd is not None else None, z)

    @property
    def expected_band(self) -> tuple[float, float] | None:
        if self.expected_brier is None:
            return None
        half = self.z * (self.sq_err_std or 0.0) / math.sqrt(max(self.window, 1))
        return (max(0.0, self.expected_brier - half), min(1.0, self.expected_brier + half))

    def update(self, prob: float, outcome: int | bool) -> None:
        if prob is None or not math.isfinite(float(prob)):
            return
        self._buf.append((min(max(float(prob), 0.0), 1.0), int(bool(outcome))))

    def reset(self) -> None:
        self._buf.clear()

    def status(self) -> dict[str, Any]:
        n = len(self._buf)
        live = sum((p - y) ** 2 for p, y in self._buf) / n if n else None
        band = self.expected_band
        out: dict[str, Any] = {
            "ok": True, "live_brier": live, "expected_band": band, "n": n, "window": self.window,
            "mean_prob": sum(p for p, _ in self._buf) / n if n else None,
            "live_win_rate": sum(y for _, y in self._buf) / n if n else None, "reason": "",
        }
        if band is None:
            out["reason"] = "no validation baseline"
        elif n < self.window:
            out["reason"] = f"warming up ({n}/{self.window} outcomes)"
        elif live is not None and live > band[1]:
            out["ok"] = False
            out["reason"] = f"live Brier {live:.3f} above expected band {band[0]:.3f}-{band[1]:.3f}"
        else:
            out["reason"] = "within expected band"
        return out
