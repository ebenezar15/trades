"""Look-ahead guard: signals up to bar T are identical on df[:k] and on the full frame, for every strategy."""
from __future__ import annotations

import math

import pytest
from strat_helpers import extra_for

from indexedge.features.engine import build_features
from indexedge.strategies.registry import STRATEGY_IDS, get_strategy

_FEAT_CACHE: dict[int, object] = {}


def _feat(df, k):
    if k not in _FEAT_CACHE:
        _FEAT_CACHE[k] = build_features(df.iloc[:k])
    return _FEAT_CACHE[k]


def _key(s):
    return (s.ts, s.side, round(s.entry, 6), round(s.stop, 6), tuple(round(t, 6) for t in s.targets),
            tuple(s.reasons), s.time_exit, s.instrument_hint)


@pytest.mark.parametrize("sid", STRATEGY_IDS)
def test_truncation_safe(sid, nifty60):
    df = nifty60
    n = len(df)
    full_feat = _feat(df, n)
    strat = get_strategy(sid)
    full = strat.scan(full_feat, "NIFTY", extra_for(sid, full_feat.index))
    compared = 0
    # cut points inside sessions and inside 15-minute bins (k bars => last bar is k-1)
    for k in (n // 3 + 7, n // 2 + 2, (2 * n) // 3 + 4, n - 38, n - 1):
        part_feat = _feat(df, k)
        t_last = part_feat.index[-1]
        part = strat.scan(part_feat, "NIFTY", extra_for(sid, part_feat.index))
        exp = [s for s in full if s.ts <= t_last]
        assert [_key(s) for s in part] == [_key(s) for s in exp], f"{sid}: mismatch at k={k}"
        for a, b in zip(part, exp):
            assert a.features.keys() == b.features.keys()
            for key, val in a.features.items():
                assert math.isclose(val, b.features[key], rel_tol=1e-9, abs_tol=1e-9), key
        compared += len(exp)
    assert compared > 0, f"{sid}: no signals to compare — test would be vacuous"
