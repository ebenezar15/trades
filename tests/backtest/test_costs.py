import pytest

from indexedge.backtest.costs import RATES_VERIFIED_ON, CostModel
from indexedge.core.types import Side


def test_futures_buy_and_sell_charges():
    cm = CostModel()
    # 65 x 25,000 = 16.25 lakh turnover
    assert cm.charges("future", Side.BUY, 65, 25000) == pytest.approx(91.19025)
    assert cm.charges("future", "SELL", 65, 25000) == pytest.approx(871.19025)   # STT 0.05% on sell


def test_option_charges_use_premium_and_sell_side_stt():
    cm = CostModel()
    t = 65 * 100.0
    txn, sebi = t * 0.0003503, t * 1e-6
    gst = 0.18 * (20 + txn + sebi)
    assert cm.charges("option", Side.SELL, 65, 100) == pytest.approx(20 + t * 0.0015 + txn + sebi + gst)
    assert cm.charges("option", Side.BUY, 65, 100) == pytest.approx(20 + txn + sebi + gst + t * 0.00003)


def test_round_trip_includes_slippage_and_side():
    cm = CostModel()
    base = cm.round_trip("future", 65, 25000, 25100, Side.BUY, include_slippage=False)
    assert base == pytest.approx(cm.charges("future", "BUY", 65, 25000) + cm.charges("future", "SELL", 65, 25100))
    full = cm.round_trip("future", 65, 25000, 25100, Side.BUY)
    slip = 65 * (0.05 + 25000 * 0.00005) + 65 * (0.05 + 25100 * 0.00005)
    assert full - base == pytest.approx(slip)
    assert cm.slippage("option", 65, 200) == pytest.approx(65 * 200 * 0.005)
    # a short trade pays STT on the entry (sell) leg
    short = cm.round_trip("future", 65, 25000, 25000, Side.SELL, include_slippage=False)
    assert short == pytest.approx(base - cm.charges("future", "SELL", 65, 25100) + cm.charges("future", "SELL", 65, 25000))


def test_zero_qty_and_bad_kind():
    cm = CostModel()
    assert cm.charges("future", "BUY", 0, 25000) == 0.0
    with pytest.raises(ValueError):
        cm.charges("swap", "BUY", 1, 1)
    assert RATES_VERIFIED_ON.startswith("2026")
