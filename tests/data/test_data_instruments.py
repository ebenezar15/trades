import json
from datetime import date

import httpx
import pytest

from indexedge.core.types import InstrumentKind, OptionType
from indexedge.data.instruments import InstrumentMaster, download, parse_expiry, parse_row
from indexedge.data.synthetic import synthetic_master_rows

ON = date(2026, 10, 16)


@pytest.fixture(scope="module")
def master():
    return InstrumentMaster.from_json(synthetic_master_rows(ON, spots={"NIFTY": 25010, "BANKNIFTY": 56040}))


def test_index_lookup(master):
    n = master.index("NIFTY")
    assert n.token == "99926000" and n.kind is InstrumentKind.INDEX and n.exchange == "NSE"
    assert master.index("Nifty Bank").token == "99926009"
    assert master.index("india vix").token == "99926017"
    with pytest.raises(KeyError):
        master.index("FOO")


def test_index_fallback_without_rows():
    m = InstrumentMaster.from_json([])
    assert m.index("BANKNIFTY").token == "99926009"


def test_futures_sorted_and_nearest(master):
    futs = master.futures("NIFTY")
    assert len(futs) == 3 and [f.expiry for f in futs] == sorted(f.expiry for f in futs)
    near = master.nearest_future("NIFTY", ON)
    assert near.expiry == date(2026, 10, 27) and near.symbol == "NIFTY27OCT26FUT"
    assert master.nearest_future("NIFTY", date(2026, 10, 28)).expiry.month == 11
    assert master.nearest_future("NIFTY", date(2030, 1, 1)) is None


def test_expiries_options_and_conversions(master):
    exps = master.expiries("NIFTY", ON)
    assert exps[0] == date(2026, 10, 20) and all(e.weekday() == 1 for e in exps)
    assert master.expiries("BANKNIFTY", ON) == [date(2026, 10, 27), date(2026, 11, 24), date(2026, 12, 29)]
    opt = master.option("NIFTY", date(2026, 10, 20), 25000, "CE")
    assert opt.strike == 25000.0 and opt.option_type is OptionType.CE
    assert opt.tick_size == pytest.approx(0.05) and opt.lot_size == 65 and opt.exchange == "NFO"
    assert master.by_token(opt.token) is opt
    assert master.by_symbol(opt.symbol) is opt
    with pytest.raises(KeyError):
        master.option("NIFTY", date(2026, 10, 20), 25025, OptionType.PE)


def test_chain_atm_step_lot(master):
    exp = date(2026, 10, 20)
    chain = master.chain("NIFTY", exp)
    assert len(chain) == 61 * 2 and chain[0].strike <= chain[-1].strike
    assert master.strike_step("NIFTY", exp) == 50
    assert master.strike_step("BANKNIFTY", date(2026, 10, 27)) == 100
    assert master.atm_strike("NIFTY", 25024.0, exp) == 25000
    assert master.atm_strike("NIFTY", 25026.0, exp) == 25050
    assert master.lot_size("NIFTY") == 65 and master.lot_size("BANKNIFTY", ON) == 30


def test_parse_defensive():
    assert parse_expiry("28OCT2026") == date(2026, 10, 28)
    assert parse_expiry("28Oct2026") == date(2026, 10, 28)
    assert parse_expiry("2026-10-28") == date(2026, 10, 28)
    assert parse_expiry("") is None and parse_expiry("garbage") is None
    row = {"token": "1", "symbol": "NIFTY28OCT2625000CE", "name": "NIFTY", "expiry": "28OCT2026",
           "strike": "2500000.000000", "lotsize": "65", "instrumenttype": "OPTIDX", "exch_seg": "NFO",
           "tick_size": "-1"}
    inst = parse_row(row)
    assert inst.strike == 25000 and inst.tick_size == 0.05
    assert parse_row({**row, "instrumenttype": "OPTSTK"}) is None
    assert parse_row({**row, "expiry": "bad"}) is None


def test_load_and_download(tmp_path):
    rows = synthetic_master_rows(ON)
    payload = json.dumps(rows).encode()
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=payload))
    path = download("https://example.invalid/master.json", tmp_path / "m.json", client=httpx.Client(transport=transport))
    m = InstrumentMaster.load(path)
    assert len(m) == len(rows)
    bad = httpx.MockTransport(lambda req: httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        download("https://example.invalid/m", tmp_path / "m.json", client=httpx.Client(transport=bad))
    assert InstrumentMaster.load(path).index("NIFTY")  # old copy kept intact
