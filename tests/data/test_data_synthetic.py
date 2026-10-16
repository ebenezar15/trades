from datetime import date, datetime, time

import numpy as np
import pandas as pd
import pytest

from indexedge.core.clock import IST
from indexedge.data.synthetic import (
    generate_daily,
    generate_intraday,
    generate_option_chain,
    synthetic_master_rows,
    trading_days,
)


@pytest.fixture(scope="module")
def nifty():
    return generate_intraday("NIFTY", days=120, interval_min=5, seed=3)


def test_intraday_shape_and_session(nifty):
    assert list(nifty.columns) == ["open", "high", "low", "close", "volume"]
    assert str(nifty.index.tz) == str(IST)
    assert len(nifty) == 120 * 75
    times = set(nifty.index.time)
    assert min(times) == time(9, 15) and max(times) == time(15, 25)
    assert all(d.weekday() < 5 for d in nifty.index.date)
    assert nifty.index.is_monotonic_increasing and not nifty.index.has_duplicates
    assert all(nifty[c].dtype == float for c in nifty.columns)


def test_ohlc_consistency_and_ticks(nifty):
    assert (nifty.high >= nifty[["open", "close"]].max(axis=1)).all()
    assert (nifty.low <= nifty[["open", "close"]].min(axis=1)).all()
    assert (nifty.volume > 0).all()
    assert np.allclose((nifty.close / 0.05).round(), nifty.close / 0.05)


def test_deterministic_by_seed():
    a = generate_intraday(days=5, seed=1)
    b = generate_intraday(days=5, seed=1)
    c = generate_intraday(days=5, seed=2)
    pd.testing.assert_frame_equal(a, b)
    assert not a.close.equals(c.close)


@pytest.mark.parametrize("und,base,vol", [("NIFTY", 25000, 0.009), ("BANKNIFTY", 56000, 0.011)])
def test_levels_and_daily_vol(und, base, vol):
    vols = []
    for seed in range(3):
        d = generate_daily(und, days=400, seed=seed)
        vols.append(np.log(d.close).diff().std())
        assert 0.9 * base < d.open.iloc[0] < 1.1 * base
    assert abs(np.mean(vols) - vol) / vol < 0.15


def test_gaps_and_regimes(nifty):
    daily = nifty.groupby(nifty.index.date).agg({"open": "first", "close": "last"})
    gaps = (daily.open / daily.close.shift() - 1).dropna()
    assert (gaps.abs() > 0.001).mean() > 0.3  # overnight gaps exist
    assert set(nifty.attrs["regimes"].values()) >= {"trend_up", "trend_down", "range"}


def test_u_shaped_volume(nifty):
    by_t = nifty.volume.groupby(nifty.index.time).mean()
    midday = by_t[(by_t.index >= time(11, 30)) & (by_t.index <= time(13, 30))].mean()
    assert by_t[time(9, 15)] > 2 * midday
    assert by_t[time(15, 25)] > 1.5 * midday


def test_holidays_and_weekends_skipped():
    hol = {date(2025, 1, 2)}
    df = generate_intraday(days=3, start=date(2025, 1, 1), holidays=hol)
    assert sorted(set(df.index.date)) == [date(2025, 1, 1), date(2025, 1, 3), date(2025, 1, 6)]
    assert trading_days(date(2025, 1, 4), 1) == [date(2025, 1, 6)]


def test_other_intervals_and_daily_consistency():
    h = generate_intraday(days=2, interval_min=60, seed=5)
    assert list(h.index.time[:7]) == [time(x, 15) for x in range(9, 16)]
    m15 = generate_intraday(days=4, interval_min=15, seed=9)
    d = generate_daily(days=4, seed=9)
    assert np.allclose(d.close.values, m15.groupby(m15.index.date).close.last().values)
    assert d.index[0] == pd.Timestamp("2025-01-01", tz=IST)


def test_option_chain_smile_parity_and_oi():
    spot, now = 25012.0, datetime(2026, 10, 16, 10, 0, tzinfo=IST)
    exp = date(2026, 10, 20)
    ch = generate_option_chain(spot, exp, now, seed=4)
    assert set(ch.option_type) == {"CE", "PE"}
    assert {"strike", "ltp", "oi", "oi_change", "volume", "iv"} <= set(ch.columns)
    assert (ch.ltp >= 0.05).all() and (ch.oi % 65 == 0).all()
    ce = ch[ch.option_type == "CE"].set_index("strike")
    pe = ch[ch.option_type == "PE"].set_index("strike")
    # put skew: deep OTM put IV above ATM IV
    assert pe.iv.loc[24400] > pe.iv.loc[25000]
    # rough put-call parity at ATM (iv differs slightly between CE/PE so allow tolerance)
    t = (datetime(2026, 10, 20, 15, 30, tzinfo=IST) - now).total_seconds() / (365 * 86400)
    parity = ce.ltp.loc[25000] - pe.ltp.loc[25000] - (spot - 25000 * np.exp(-0.065 * t))
    assert abs(parity) < 5
    # OI concentrated at round strikes: biggest call wall above spot, biggest put wall below, on 100s
    assert ce.oi.idxmax() > spot and pe.oi.idxmax() < spot
    assert ce.oi.idxmax() % 100 == 0 and pe.oi.idxmax() % 100 == 0


def test_master_rows_format():
    rows = synthetic_master_rows(date(2026, 10, 16))
    idx = {r["token"]: r for r in rows if r["instrumenttype"] == "AMXIDX"}
    assert set(idx) == {"99926000", "99926009", "99919000", "99926017"}
    assert idx["99919000"]["name"] == "SENSEX" and idx["99919000"]["exch_seg"] == "BSE"
    assert idx["99926000"]["name"] == "NIFTY" and idx["99926000"]["symbol"] == "Nifty 50"
    opt = next(r for r in rows if r["instrumenttype"] == "OPTIDX" and r["name"] == "NIFTY")
    assert opt["strike"].endswith(".000000") and float(opt["strike"]) > 1_000_000  # x100
    assert opt["tick_size"] == "5.000000" and opt["lotsize"] == "65" and opt["exch_seg"] == "NFO"
    assert len(opt["expiry"]) == 9 and opt["expiry"][2:5].isupper()
    nifty_exps = {datetime.strptime(r["expiry"], "%d%b%Y").date() for r in rows
                  if r["name"] == "NIFTY" and r["instrumenttype"] == "OPTIDX"}
    assert all(e.weekday() == 1 for e in nifty_exps)  # Tuesdays
    assert date(2026, 10, 20) in nifty_exps
    bn = {datetime.strptime(r["expiry"], "%d%b%Y").date() for r in rows
          if r["name"] == "BANKNIFTY" and r["instrumenttype"] == "OPTIDX"}
    assert date(2026, 10, 27) in bn and len(bn) == 3  # monthly, last Tuesday
    assert all(r["lotsize"] == "30" for r in rows if r["name"] == "BANKNIFTY" and r["instrumenttype"] != "AMXIDX")
    sx = [r for r in rows if r["name"] == "SENSEX" and r["instrumenttype"] != "AMXIDX"]
    assert sx and all(r["exch_seg"] == "BFO" and r["lotsize"] == "20" for r in sx)
    sx_exps = {datetime.strptime(r["expiry"], "%d%b%Y").date() for r in sx if r["instrumenttype"] == "OPTIDX"}
    assert all(e.weekday() == 3 for e in sx_exps) and date(2026, 10, 22) in sx_exps   # weekly Thursdays


def test_master_expiry_holiday_shift():
    rows = synthetic_master_rows(date(2026, 10, 16), holidays={date(2026, 10, 27)})
    bn = {r["expiry"] for r in rows if r["name"] == "BANKNIFTY" and r["instrumenttype"] == "FUTIDX"}
    assert "26OCT2026" in bn and "27OCT2026" not in bn
