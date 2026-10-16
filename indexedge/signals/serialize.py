"""``Signal`` → SignalJSON (REST/WS API contract in the implementation plan)."""
from __future__ import annotations

import math
from typing import Any

from indexedge.core.types import Instrument, Signal

SIGNAL_JSON_KEYS = (
    "id", "strategy_id", "strategy_name", "underlying", "side", "ts", "entry", "stop", "targets", "probability",
    "probability_ci", "sample_size", "expected_rr", "ev_per_trade", "quantity", "instrument", "badges",
    "reasons", "context_reasons", "news_reasons", "top_features", "regime", "status", "blocked_reason",
)


def _num(x: Any) -> float | None:
    if x is None:
        return None
    x = float(x)
    return x if math.isfinite(x) else None


def instrument_to_json(inst: Instrument | None) -> dict[str, Any] | None:
    if inst is None:
        return None
    return {
        "symbol": inst.symbol,
        "token": inst.token,
        "name": inst.name,
        "exchange": inst.exchange,
        "kind": "option" if inst.kind.value == "OPTIDX" else "future" if inst.kind.value == "FUTIDX"
        else inst.kind.value.lower(),
        "lot_size": inst.lot_size,
        "expiry": inst.expiry.isoformat() if inst.expiry else None,
        "strike": inst.strike,
        "option_type": inst.option_type.value if inst.option_type else None,
    }


def signal_to_json(sig: Signal, strategy_name: str | None = None) -> dict[str, Any]:
    raw = sig.raw
    ci = sig.probability_ci
    return {
        "id": sig.id,
        "strategy_id": raw.strategy_id,
        "strategy_name": strategy_name or raw.strategy_id,
        "underlying": raw.underlying,
        "side": raw.side.value,
        "ts": raw.ts.isoformat(),
        "entry": _num(raw.entry),
        "stop": _num(raw.stop),
        "targets": [_num(t) for t in raw.targets],
        "probability": _num(sig.probability),
        "probability_ci": [_num(ci[0]), _num(ci[1])] if ci is not None else None,
        "sample_size": int(sig.sample_size),
        "expected_rr": _num(sig.expected_rr) or 0.0,
        "ev_per_trade": _num(sig.ev_per_trade) or 0.0,
        "quantity": int(sig.quantity),
        "instrument": instrument_to_json(sig.instrument),
        "badges": list(sig.badges),
        "reasons": list(sig.reasons),
        "context_reasons": list(sig.context_reasons),
        "news_reasons": list(sig.news_reasons),
        "top_features": [[str(n), _num(v)] for n, v in sig.top_features],
        "regime": sig.regime.value,
        "status": sig.status,
        "blocked_reason": sig.blocked_reason,
    }
