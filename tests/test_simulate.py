from datetime import datetime

import pandas as pd

from indexedge.backtest.simulate import simulate_exit
from indexedge.core.clock import IST
from indexedge.core.types import Side, StrategySignal


def _bars(rows, start="2026-01-05 09:20"):
    idx = pd.date_range(start, periods=len(rows), freq="5min", tz=IST)
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx).assign(volume=1.0)


def _sig(side=Side.BUY, entry=100.0, stop=95.0, targets=(110.0,), time_exit=None):
    return StrategySignal("t", "NIFTY", datetime(2026, 1, 5, 9, 15, tzinfo=IST), side, entry, stop,
                          list(targets), [], time_exit=time_exit)


def test_target_hit_long():
    out = simulate_exit(_sig(), _bars([[100, 104, 99, 103], [103, 111, 102, 110]]))
    assert out.outcome == "target" and out.r_multiple == 2.0 and out.bars_held == 2


def test_stop_first_when_both_touched():
    out = simulate_exit(_sig(), _bars([[100, 111, 94, 100]]))
    assert out.outcome == "stop" and out.r_multiple == -1.0


def test_short_and_gap_through_stop_fills_at_open():
    sig = _sig(Side.SELL, entry=100, stop=105, targets=[90])
    out = simulate_exit(sig, _bars([[100, 101, 99, 100], [108, 109, 107, 108]]))
    assert out.outcome == "stop" and out.exit_price == 108 and out.r_multiple < -1


def test_breakeven_after_t1():
    sig = _sig(targets=[105, 115])
    out = simulate_exit(sig, _bars([[100, 106, 100, 105], [105, 106, 99, 100]]))
    assert out.outcome == "stop" and out.points == 0


def test_time_exit_and_empty():
    sig = _sig(time_exit="09:25")
    out = simulate_exit(sig, _bars([[100, 101, 99, 100.5], [100.5, 102, 100, 101]]))
    assert out.outcome == "time" and out.exit_price == 101
    assert simulate_exit(sig, pd.DataFrame(columns=["open", "high", "low", "close"])) is None


def test_intraday_only():
    rows = [[100, 101, 99, 100]] * 3
    bars = pd.concat([_bars(rows), _bars([[100, 120, 99, 119]], start="2026-01-06 09:15")])
    out = simulate_exit(_sig(), bars)
    assert out.outcome == "eod" and out.exit_ts.date().day == 5
