"""Forbidden-order-type guard (static + runtime) and the confirmation queue."""
from __future__ import annotations

import io
import tokenize
from datetime import timedelta
from pathlib import Path

import pytest
from exec_harness import at

from indexedge.core.clock import SimClock
from indexedge.core.types import Instrument, InstrumentKind, OrderRequest, OrderType, Side
from indexedge.execution.confirm import PendingConfirmations
from indexedge.execution.guard import FORBIDDEN_VALUES, ForbiddenOrderError, assert_allowed_order
from indexedge.journal.db import Journal

PKG = Path(__file__).resolve().parents[2] / "indexedge"
INST = Instrument("1", "NIFTYFUT", "NIFTY", "NFO", InstrumentKind.FUTURE, lot_size=65)
# (relative path, literal) pairs that are allowed, e.g. a SmartAPI status-mapping table. Keep empty if possible.
ALLOWLIST: set[tuple[str, str]] = set()


def test_assert_allowed_order_accepts_limit_and_sl_limit():
    assert_allowed_order(OrderRequest(INST, Side.BUY, 65, OrderType.LIMIT, 100.0))
    assert_allowed_order(OrderRequest(INST, Side.SELL, 65, OrderType.SL_LIMIT, 99.0, trigger_price=99.5))


@pytest.mark.parametrize("bad", ["MARKET", "IOC", "AMO", "STOPLOSS_MARKET"])
def test_assert_allowed_order_rejects_forbidden_types(bad):
    req = OrderRequest(INST, Side.BUY, 65, OrderType.LIMIT, 100.0)
    req.order_type = bad          # bypass the dataclass check, as a buggy caller might
    with pytest.raises(ForbiddenOrderError):
        assert_allowed_order(req)


def test_assert_allowed_order_rejects_non_positive_price():
    req = OrderRequest(INST, Side.BUY, 65, OrderType.LIMIT, 100.0)
    req.price = 0
    with pytest.raises(ForbiddenOrderError):
        assert_allowed_order(req)


def test_order_type_enum_has_only_allowed_members():
    assert {t.name for t in OrderType} == {"LIMIT", "SL_LIMIT"}


def _string_literals(path: Path):
    src = path.read_text(encoding="utf-8")
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.STRING:
            body = tok.string.lstrip("rbuRBUfF")
            for q in ('"""', "'''", '"', "'"):
                if body.startswith(q) and body.endswith(q) and len(body) >= 2 * len(q):
                    body = body[len(q):-len(q)]
                    break
            yield tok.start[0], body.strip()


def test_no_forbidden_order_literals_in_package_source():
    """No string literal anywhere in indexedge/ equals MARKET / STOPLOSS_MARKET / IOC / AMO.

    Comments and docstrings that merely *mention* these words are fine (they are never an exact literal).
    """
    violations = []
    for path in sorted(PKG.rglob("*.py")):
        rel = str(path.relative_to(PKG))
        for line, lit in _string_literals(path):
            if lit.upper() in FORBIDDEN_VALUES and (rel, lit) not in ALLOWLIST:
                violations.append(f"{rel}:{line}: {lit!r}")
    assert not violations, "forbidden order literals:\n" + "\n".join(violations)


def test_execution_code_references_only_allowed_order_types():
    import re

    refs = set()
    for path in (PKG / "execution").rglob("*.py"):
        refs |= set(re.findall(r"OrderType\.(\w+)", path.read_text()))
    assert refs and refs <= {"LIMIT", "SL_LIMIT"}


# ------------------------------------------------------------------------------------- confirmations
class _Sig:
    def __init__(self, id):
        self.id, self.status = id, "active"


def test_pending_confirm_skip_and_expire():
    clock = SimClock(at(10, 0))
    j = Journal(":memory:")
    q = PendingConfirmations(clock, ttl_s=60, journal=j)
    a, b, c = _Sig("a"), _Sig("b"), _Sig("c")
    for s in (a, b, c):
        q.add(s)
    assert len(q) == 3 and a.status == "pending_confirm"
    assert q.confirm("a") is a and a.status == "active"
    assert q.confirm("a") is None                       # only once
    assert q.skip("b") and b.status == "skipped" and not q.skip("b")
    clock.advance(seconds=59)
    assert q.expire() == [] and "c" in q
    clock.advance(seconds=1)
    assert q.confirm("c") is None and c.status == "expired"
    assert len(q) == 0
    j.close()


def test_expire_with_explicit_now():
    clock = SimClock(at(10, 0))
    q = PendingConfirmations(clock)
    s = _Sig("x")
    q.add(s)
    assert q.expire(at(10, 0) + timedelta(seconds=61)) == [s]
