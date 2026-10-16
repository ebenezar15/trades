from datetime import date, datetime

import pytest

from indexedge.broker.base import BrokerError
from indexedge.broker.paper import MarginModel, PaperBroker
from indexedge.core.clock import IST, SimClock
from indexedge.core.types import (
    Instrument,
    InstrumentKind,
    OptionType,
    OrderRequest,
    OrderStatus,
    OrderType,
    Side,
    Tick,
)

FUT = Instrument("101", "NIFTY27OCT26FUT", "NIFTY", "NFO", InstrumentKind.FUTURE, lot_size=65,
                 expiry=date(2026, 10, 27))
CE = Instrument("202", "NIFTY20OCT2625000CE", "NIFTY", "NFO", InstrumentKind.OPTION, lot_size=65,
                expiry=date(2026, 10, 20), strike=25000.0, option_type=OptionType.CE)
T0 = datetime(2026, 10, 16, 10, 0, tzinfo=IST)


def tick(inst, ltp, sec=0, bid=None, ask=None):
    return Tick(inst.token, T0.replace(second=sec), ltp, bid=bid, ask=ask)


def lim(inst, side, qty, price):
    return OrderRequest(inst, side, qty, OrderType.LIMIT, price)


def sl(inst, side, qty, price, trig):
    return OrderRequest(inst, side, qty, OrderType.SL_LIMIT, price, trigger_price=trig)


@pytest.fixture
def broker():
    b = PaperBroker(SimClock(T0), capital=1_000_000, slippage_ticks=1)
    b.updates = []
    b.on_order_update(lambda o: b.updates.append((o.broker_id, o.status, o.filled_qty)))
    return b


async def test_buy_limit_rests_then_fills_at_or_better(broker):
    await broker.feed_tick(tick(FUT, 25010))
    o = await broker.place_order(lim(FUT, Side.BUY, 65, 25000))
    assert o.status is OrderStatus.OPEN
    await broker.feed_tick(tick(FUT, 25001))
    assert o.status is OrderStatus.OPEN
    await broker.feed_tick(tick(FUT, 24990))
    assert o.status is OrderStatus.FILLED and o.avg_price == pytest.approx(24990.05)
    pos = (await broker.positions())[0]
    assert pos.net_qty == 65 and pos.avg_price == pytest.approx(24990.05)
    assert [u[1] for u in broker.updates] == [OrderStatus.OPEN, OrderStatus.FILLED]
    fills = await broker.trade_book()
    assert fills[0].ts == T0 and fills[0].quantity == 65


async def test_marketable_limit_fills_immediately_never_above_limit(broker):
    await broker.feed_tick(tick(FUT, 25000, ask=25000.5, bid=24999.5))
    o = await broker.place_order(lim(FUT, Side.BUY, 65, 25000.5))
    assert o.status is OrderStatus.FILLED and o.avg_price == 25000.5  # min(limit, ask + slip)
    s = await broker.place_order(lim(FUT, Side.SELL, 65, 24990))
    assert s.status is OrderStatus.FILLED and s.avg_price == pytest.approx(24999.45)  # bid - slip


async def test_round_trip_pnl_and_ltp_updates(broker):
    await broker.feed_tick(tick(FUT, 25000))
    await broker.place_order(lim(FUT, Side.BUY, 130, 25001))
    await broker.feed_tick(tick(FUT, 25050))
    pos = (await broker.positions())[0]
    assert pos.ltp == 25050 and pos.unrealised_pnl == pytest.approx((25050 - 25000.05) * 130)
    await broker.place_order(lim(FUT, Side.SELL, 65, 25040))
    pos = (await broker.positions())[0]
    assert pos.net_qty == 65 and pos.realised_pnl == pytest.approx((25049.95 - 25000.05) * 65)
    await broker.place_order(lim(FUT, Side.SELL, 130, 25040))  # flip short
    assert pos.net_qty == -65 and pos.avg_price == pytest.approx(25049.95)
    f = await broker.funds()
    assert f.net == pytest.approx(1_000_000 + pos.realised_pnl + pos.unrealised_pnl)


async def test_sell_sl_limit_triggers_then_limit(broker):
    await broker.feed_tick(tick(FUT, 25000))
    await broker.place_order(lim(FUT, Side.BUY, 65, 25000))
    stop = await broker.place_order(sl(FUT, Side.SELL, 65, 24945, 24950))
    assert stop.status is OrderStatus.TRIGGER_PENDING
    await broker.feed_tick(tick(FUT, 24960))
    assert stop.status is OrderStatus.TRIGGER_PENDING
    await broker.feed_tick(tick(FUT, 24948))
    assert stop.status is OrderStatus.FILLED and stop.avg_price == pytest.approx(24947.95)
    assert (await broker.positions())[0].net_qty == 0


async def test_sl_limit_gap_through_leaves_order_open(broker):
    await broker.feed_tick(tick(FUT, 25000))
    stop = await broker.place_order(sl(FUT, Side.SELL, 65, 24945, 24950))
    await broker.feed_tick(tick(FUT, 24900))  # gap through trigger and limit
    assert stop.status is OrderStatus.OPEN and stop.filled_qty == 0
    await broker.feed_tick(tick(FUT, 24946))
    assert stop.status is OrderStatus.FILLED


async def test_buy_sl_limit_and_validation(broker):
    await broker.feed_tick(tick(FUT, 25000))
    o = await broker.place_order(sl(FUT, Side.BUY, 65, 25060, 25050))
    await broker.feed_tick(tick(FUT, 25055))
    assert o.status is OrderStatus.FILLED and o.avg_price == pytest.approx(25055.05)
    bad = await broker.place_order(sl(FUT, Side.BUY, 65, 25040, 25050))  # limit below trigger
    assert bad.status is OrderStatus.REJECTED and "limit" in bad.message
    breached = await broker.place_order(sl(FUT, Side.SELL, 65, 25050, 25060))  # trigger above LTP
    assert breached.status is OrderStatus.REJECTED and "breached" in breached.message


async def test_partial_fills(broker):
    broker.max_fill_qty_per_tick = 65
    await broker.feed_tick(tick(FUT, 25010))
    o = await broker.place_order(lim(FUT, Side.BUY, 195, 25000))
    await broker.feed_tick(tick(FUT, 24995))
    assert o.status is OrderStatus.PARTIAL and o.filled_qty == 65 and o.pending_qty == 130
    await broker.feed_tick(tick(FUT, 24995))
    c = await broker.cancel_order(o)
    assert c.status is OrderStatus.CANCELLED and c.filled_qty == 130
    assert (await broker.positions())[0].net_qty == 130


async def test_modify_and_cancel(broker):
    await broker.feed_tick(tick(FUT, 25010))
    o = await broker.place_order(lim(FUT, Side.BUY, 65, 24990))
    m = await broker.modify_order(o, 25015)  # chase: becomes marketable
    assert m is o and o.status is OrderStatus.FILLED and o.request.price == 25015
    same = await broker.cancel_order(o)  # filled before cancel: unchanged
    assert same.status is OrderStatus.FILLED
    again = await broker.modify_order(o, 25020)
    assert again.status is OrderStatus.FILLED and "cannot modify" in again.message
    with pytest.raises(BrokerError):
        await broker.cancel_order(type(o)(o.request, "NOPE"))


async def test_rejects_forbidden_order_type_and_bad_lot(broker):
    req = lim(FUT, Side.BUY, 65, 25000)
    object.__setattr__(req, "order_type", "MARKET")
    o = await broker.place_order(req)
    assert o.status is OrderStatus.REJECTED and "not allowed" in o.message
    with pytest.raises(ValueError):
        OrderRequest(FUT, Side.BUY, 65, "MARKET", 25000)
    o = await broker.place_order(lim(FUT, Side.BUY, 50, 25000))
    assert o.status is OrderStatus.REJECTED and "lot" in o.message


async def test_margin_checks():
    b = PaperBroker(SimClock(T0), capital=200_000)
    await b.feed_tick(tick(FUT, 25000))
    o = await b.place_order(lim(FUT, Side.BUY, 65, 24000))  # 12% * 24000 * 65 = 187,200
    assert o.status is OrderStatus.OPEN
    f = await b.funds()
    assert f.used_margin == pytest.approx(187_200) and f.available_cash == pytest.approx(12_800)
    o2 = await b.place_order(lim(FUT, Side.BUY, 65, 24000))
    assert o2.status is OrderStatus.REJECTED and "insufficient funds" in o2.message
    # a sell that reduces an existing position needs no margin
    await b.cancel_order(o)
    await b.place_order(lim(FUT, Side.BUY, 65, 25001))
    exit_ = await b.place_order(lim(FUT, Side.SELL, 65, 26000))
    assert exit_.status is OrderStatus.OPEN
    # option buy needs premium; option sell needs 15% of strike notional
    m = MarginModel()
    assert m.required(CE, Side.BUY, 65, 120) == pytest.approx(7_800)
    assert m.required(CE, Side.SELL, 65, 120) == pytest.approx(0.15 * 25000 * 65)
    small = PaperBroker(SimClock(T0), capital=100_000)
    await small.feed_tick(tick(CE, 120))
    assert (await small.place_order(lim(CE, Side.SELL, 65, 119))).status is OrderStatus.REJECTED
    assert (await small.place_order(lim(CE, Side.BUY, 65, 121))).status is OrderStatus.FILLED


async def test_tick_subscription_and_ltp(broker):
    got = []

    async def handler(t):
        got.append(t.token)

    await broker.subscribe_ticks([("NFO", FUT.token)], handler)
    with pytest.raises(BrokerError):
        await broker.get_ltp("NFO", FUT.token)
    await broker.feed_tick(tick(FUT, 25000))
    await broker.feed_tick(tick(CE, 100))
    assert got == [FUT.token] and await broker.get_ltp("NFO", FUT.token) == 25000
    await broker.connect()
    assert broker.connected
    assert (await broker.get_candles("NSE", "1", "5m", T0, T0)).empty
