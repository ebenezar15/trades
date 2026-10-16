"""Indian index F&O transaction costs (NSE) + slippage.

Rates verified 2026-10-16 against public broker summaries of the Union Budget 2026-27 (Finance Act 2026),
which raised STT on F&O **with effect from 2026-04-01**:

* STT (sell side only): futures 0.05% of turnover (was 0.02%); options 0.15% of premium turnover (was 0.10%).
  Sources: 5paisa "STT hike on F&O to take effect from April 1", Motilal Oswal learning centre "Union Budget
  2026-27: STT update", Outlook Money (Feb 2026).
* NSE transaction charges: futures 0.00173%, options 0.03503% of (premium) turnover, both sides
  (NSE circular effective 2024-10-01; unchanged as far as verified).
* SEBI turnover fee: ₹10 per crore (0.000001 × turnover), both sides.
* GST: 18% on (brokerage + exchange transaction charges + SEBI fee).
* Stamp duty (buy side only): futures 0.002%, options 0.003%.
* Brokerage: ₹20 per executed order (flat, typical discount broker incl. Angel One).

Slippage (IndexEdge convention, per order/side, deliberately conservative): futures 1 tick + 0.005% of
price; options 0.5% of premium. All rates are constructor arguments so they can be updated without code
changes.
"""
from __future__ import annotations

from dataclasses import dataclass

from indexedge.core.types import Side

RATES_VERIFIED_ON = "2026-10-16"


@dataclass(frozen=True)
class CostModel:
    brokerage_per_order: float = 20.0
    stt_future_sell: float = 0.0005          # 0.05 %  (from 2026-04-01)
    stt_option_sell: float = 0.0015          # 0.15 % of premium (from 2026-04-01)
    txn_future: float = 0.0000173            # 0.00173 %
    txn_option: float = 0.0003503            # 0.03503 % of premium
    sebi_per_crore: float = 10.0
    gst: float = 0.18
    stamp_future_buy: float = 0.00002        # 0.002 %
    stamp_option_buy: float = 0.00003        # 0.003 %
    tick: float = 0.05
    slippage_future_pct: float = 0.00005     # 0.005 % of price per side (+ 1 tick) ≈ 1.3 pts on NIFTY
    slippage_option_pct: float = 0.005       # 0.5 % of premium per side

    @staticmethod
    def _side(side: Side | str) -> Side:
        return side if isinstance(side, Side) else Side(str(side).upper())

    def charges(self, instrument_kind: str, side: Side | str, qty: int, price: float) -> float:
        """Statutory charges + brokerage for one executed order (₹). ``price`` is the premium for options."""
        kind = _kind(instrument_kind)
        sd = self._side(side)
        turnover = abs(qty) * max(price, 0.0)
        if turnover <= 0:
            return 0.0
        is_opt = kind == "option"
        stt = turnover * (self.stt_option_sell if is_opt else self.stt_future_sell) if sd is Side.SELL else 0.0
        txn = turnover * (self.txn_option if is_opt else self.txn_future)
        sebi = turnover * self.sebi_per_crore / 1e7
        gst = self.gst * (self.brokerage_per_order + txn + sebi)
        stamp = turnover * (self.stamp_option_buy if is_opt else self.stamp_future_buy) if sd is Side.BUY else 0.0
        return self.brokerage_per_order + stt + txn + sebi + gst + stamp

    def slippage(self, instrument_kind: str, qty: int, price: float) -> float:
        """Expected slippage for one order in ₹."""
        if _kind(instrument_kind) == "option":
            return abs(qty) * max(price, 0.0) * self.slippage_option_pct
        return abs(qty) * (self.tick + max(price, 0.0) * self.slippage_future_pct)

    def round_trip(self, kind: str, qty: int, entry: float, exit: float, entry_side: Side | str = Side.BUY,
                   include_slippage: bool = True) -> float:
        """Total cost (₹) of entering at ``entry`` and exiting at ``exit`` (charges + optional slippage)."""
        sd = self._side(entry_side)
        cost = self.charges(kind, sd, qty, entry) + self.charges(kind, sd.opposite(), qty, exit)
        if include_slippage:
            cost += self.slippage(kind, qty, entry) + self.slippage(kind, qty, exit)
        return cost


def _kind(kind: str) -> str:
    k = str(kind).lower()
    if k in ("future", "futures", "fut", "futidx"):
        return "future"
    if k in ("option", "options", "opt", "optidx", "straddle"):
        return "option"
    raise ValueError(f"unknown instrument kind {kind!r}")
