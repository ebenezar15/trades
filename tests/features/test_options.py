from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from indexedge.core.clock import IST
from indexedge.features import options as op


def test_bs_price_reference_values():
    assert op.bs_price(100, 100, 1.0, 0.05, 0.2, "CE") == pytest.approx(10.4506, abs=1e-4)
    assert op.bs_price(100, 100, 1.0, 0.05, 0.2, "PE") == pytest.approx(5.5735, abs=1e-4)


@pytest.mark.parametrize("S,K,T,r,s", [(100, 100, 1, 0.05, 0.2), (24000, 24500, 7 / 365, 0.065, 0.13),
                                       (50, 40, 0.3, 0.0, 0.5)])
def test_put_call_parity(S, K, T, r, s):
    c = op.bs_price(S, K, T, r, s, "CE")
    p = op.bs_price(S, K, T, r, s, "PE")
    assert c - p == pytest.approx(S - K * np.exp(-r * T), abs=1e-8)


def test_bs_expired_returns_intrinsic():
    assert op.bs_price(110, 100, 0.0, 0.05, 0.2, "CE") == pytest.approx(10.0)
    assert op.bs_price(90, 100, 0.0, 0.05, 0.2, "PE") == pytest.approx(10.0)
    assert op.bs_price(90, 100, 0.0, 0.05, 0.2, "CE") == 0.0


def test_bs_greeks_reference():
    g = op.bs_greeks(100, 100, 1.0, 0.05, 0.2, "CE")
    assert g["delta"] == pytest.approx(0.63683, abs=1e-4)
    assert g["gamma"] == pytest.approx(0.018762, abs=1e-5)
    assert g["vega"] == pytest.approx(0.37524, abs=1e-4)          # per 1 vol point
    assert g["theta"] == pytest.approx(-6.4140 / 365, abs=1e-5)   # per calendar day
    p = op.bs_greeks(100, 100, 1.0, 0.05, 0.2, "PE")
    assert p["delta"] == pytest.approx(g["delta"] - 1)
    assert p["theta"] == pytest.approx(-1.6579 / 365, abs=1e-5)
    # vega ~ finite difference of price per 1 vol point
    fd = op.bs_price(100, 100, 1, 0.05, 0.21, "CE") - op.bs_price(100, 100, 1, 0.05, 0.19, "CE")
    assert g["vega"] == pytest.approx(fd / 2, rel=1e-3)


@pytest.mark.parametrize("opt", ["CE", "PE"])
@pytest.mark.parametrize("K,T,sigma", [(95, 0.5, 0.15), (100, 1.0, 0.2), (120, 0.05, 0.6), (99, 2 / 365, 0.12)])
def test_implied_vol_round_trip(opt, K, T, sigma):
    price = op.bs_price(100, K, T, 0.065, sigma, opt)
    if price < 1e-6:
        pytest.skip("price too small to invert")
    assert op.implied_vol(price, 100, K, T, 0.065, opt) == pytest.approx(sigma, abs=1e-5)


def test_implied_vol_no_solution_is_nan():
    assert np.isnan(op.implied_vol(0.01, 150, 100, 0.5, 0.05, "CE"))  # below intrinsic
    assert np.isnan(op.implied_vol(500, 100, 100, 0.5, 0.05, "CE"))   # above S
    assert np.isnan(op.implied_vol(5, 100, 100, 0.0, 0.05, "CE"))     # expired


@pytest.fixture
def chain():
    rows = [(100, "CE", 10), (110, "CE", 20), (120, "CE", 50), (100, "PE", 40), (110, "PE", 20), (120, "PE", 5)]
    return pd.DataFrame([{"strike": k, "option_type": t, "ltp": 1.0, "oi": oi, "oi_change": 0, "volume": 0}
                         for k, t, oi in rows])


def test_pcr_and_max_pain_hand_chain(chain):
    assert op.pcr(chain) == pytest.approx(65 / 80)
    # pain: K=100 -> 300, K=110 -> 150, K=120 -> 400
    assert op.max_pain(chain) == 110


def test_oi_walls(chain):
    w = op.oi_walls(chain, n=2)
    assert w == {"resistance": [120.0, 110.0], "support": [100.0, 110.0]}


def test_empty_chain():
    e = pd.DataFrame(columns=["strike", "option_type", "ltp", "oi", "oi_change", "volume"])
    assert np.isnan(op.pcr(e)) and np.isnan(op.max_pain(e))
    assert op.oi_walls(e) == {"resistance": [], "support": []}
    assert op.enrich_chain(e, 100, date(2026, 1, 8), datetime(2026, 1, 5, 10, tzinfo=IST)).empty


@pytest.mark.parametrize("price,oi,expected", [
    (1, 1, "long_buildup"), (-1, 1, "short_buildup"), (1, -1, "short_covering"), (-1, -1, "long_unwinding"),
    (0, 1, "neutral"), (1, 0, "neutral"), (float("nan"), 1, "neutral"), (None, 1, "neutral"),
])
def test_oi_buildup_table(price, oi, expected):
    assert op.oi_buildup(price, oi) == expected


def test_enrich_chain_recovers_iv_and_greeks():
    now = datetime(2026, 1, 5, 9, 30, tzinfo=IST)
    expiry = date(2026, 1, 13)
    T = op.year_fraction(expiry, now)
    spot = 24000.0
    rows = []
    for k in (23800, 24000, 24200):
        for t in ("CE", "PE"):
            rows.append({"strike": k, "option_type": t, "ltp": op.bs_price(spot, k, T, 0.065, 0.14, t),
                         "oi": 1000, "oi_change": 50 if t == "CE" else -50, "volume": 10,
                         "ltp_change": 2.0})
    out = op.enrich_chain(pd.DataFrame(rows), spot, expiry, now)
    np.testing.assert_allclose(out["iv"], 14.0, atol=1e-3)
    atm_ce = out[(out.strike == 24000) & (out.option_type == "CE")].iloc[0]
    g = op.bs_greeks(spot, 24000, T, 0.065, 0.14, "CE")
    assert atm_ce.delta == pytest.approx(g["delta"], abs=1e-5)
    assert set(out["buildup"]) == {"long_buildup", "short_covering"}
    assert T == pytest.approx((8 * 24 - 9.5 + 15.5) / 24 / 365)


def test_enrich_chain_without_price_change_is_neutral(chain):
    out = op.enrich_chain(chain, 110, date(2026, 1, 8), datetime(2026, 1, 5, 10, tzinfo=IST))
    assert (out["buildup"] == "neutral").all()
    assert {"iv", "delta", "gamma", "theta", "vega"} <= set(out.columns)
