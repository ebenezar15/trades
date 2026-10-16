from datetime import date, time

import pandas as pd
import pytest

from indexedge.core.clock import IST
from indexedge.data.store import CandleStore, resample
from indexedge.data.synthetic import generate_intraday


@pytest.fixture
def df5():
    return generate_intraday(days=6, interval_min=5, seed=11, start=date(2025, 12, 29))


def test_roundtrip_across_years(tmp_path, df5):
    store = CandleStore(tmp_path)
    store.save("NIFTY", "5m", df5)
    assert sorted(p.name for p in (tmp_path / "NIFTY" / "5m").iterdir()) == ["2025.parquet", "2026.parquet"]
    out = store.load("NIFTY", "5m")
    pd.testing.assert_frame_equal(out, df5, check_freq=False)
    assert str(out.index.tz) == str(IST)


def test_append_dedupe_later_wins(tmp_path, df5):
    store = CandleStore(tmp_path)
    store.save("NIFTY", "5m", df5.iloc[:100])
    changed = df5.iloc[90:200].copy()
    changed.loc[changed.index[0], "close"] = 1.0
    store.save("NIFTY", "5m", changed)
    out = store.load("NIFTY", "5m")
    assert len(out) == 200 and not out.index.has_duplicates
    assert out.loc[df5.index[90], "close"] == 1.0
    assert store.last_ts("NIFTY", "5m") == df5.index[199]


def test_load_filters(tmp_path, df5):
    store = CandleStore(tmp_path)
    store.save("NIFTY", "5m", df5)
    day = store.load("NIFTY", "5m", start=date(2026, 1, 1), end=date(2026, 1, 1))
    assert set(day.index.date) == {date(2026, 1, 1)} and len(day) == 75
    part = store.load("NIFTY", "5m", start="2026-01-02 10:00", end=pd.Timestamp("2026-01-02 10:30", tz=IST))
    assert len(part) == 7
    assert store.load("NIFTY", "1m").empty and store.load("FOO", "5m").empty


def test_naive_index_is_localised(tmp_path, df5):
    store = CandleStore(tmp_path)
    store.save("X", "5m", df5.tz_localize(None))
    assert store.load("X", "5m").index[0] == df5.index[0]


def test_resample_session_aware(df5):
    m15 = resample(df5, "15m")
    direct = generate_intraday(days=6, interval_min=15, seed=11, start=date(2025, 12, 29))
    pd.testing.assert_frame_equal(m15, direct, check_freq=False)
    h = resample(df5, "1h")
    assert list(h.index.time[:7]) == [time(x, 15) for x in range(9, 16)]
    assert h.index[6].time() == time(15, 15)
    d = resample(df5, "1d")
    assert len(d) == 6 and d.index[0] == pd.Timestamp("2025-12-29", tz=IST)
    assert d.volume.sum() == pytest.approx(df5.volume.sum())
    assert CandleStore.resample(df5.iloc[:0], "5m").empty
    with pytest.raises(ValueError):
        resample(df5, "7m")


def test_available(tmp_path, df5):
    store = CandleStore(tmp_path)
    store.save("NIFTY", "5m", df5)
    store.save("BANKNIFTY", "1d", resample(df5, "1d"))
    av = store.available()
    assert [(a[0], a[1], a[4]) for a in av] == [("BANKNIFTY", "1d", 6), ("NIFTY", "5m", len(df5))]
    assert av[1][2] == df5.index[0] and av[1][3] == df5.index[-1]
