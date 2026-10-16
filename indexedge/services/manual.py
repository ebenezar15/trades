"""Manual execution: the user places orders by hand in Angel One (e.g. on the PC); IndexEdge prepares the exact
order ticket, journals the trade when told it was placed, tracks it live and alerts when the stop or target is hit.

Levels are planned on the index (the strategy's view) and translated to the option premium with the option's
Black-Scholes delta, so both are shown: "exit if NIFTY < 25,100" and "SL-L trigger ₹142.30 on the option".
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import Any

from indexedge.core import bus as topics
from indexedge.core.clock import IST, parse_hhmm
from indexedge.core.types import InstrumentKind, Signal
from indexedge.journal.models import TradeRecord

log = logging.getLogger(__name__)
TICK = 0.05


def _tick(x: float, mode: str = "nearest") -> float:
    n = x / TICK
    n = math.ceil(n - 1e-9) if mode == "up" else math.floor(n + 1e-9) if mode == "down" else round(n)
    return round(max(TICK, n * TICK), 2)


class ManualDesk:
    def __init__(self, rt: Any) -> None:
        self.rt = rt
        self.open: dict[str, dict[str, Any]] = {}        # trade_id -> state
        self._ltp_cache: dict[str, tuple[float, float]] = {}

    # ------------------------------------------------------------------ restore
    def load_open(self) -> None:
        try:
            df = self.rt.journal.trades(status="open", mode="manual")
        except Exception:  # noqa: BLE001
            return
        for r in (df.to_dict(orient="records") if not df.empty else []):
            ctx = r.get("context") or {}
            self.open[r["id"]] = {"row": r, "index_levels": ctx.get("index_levels") or {},
                                  "signal_side": ctx.get("signal_side", r.get("side")),
                                  "token": ctx.get("token"), "exchange": ctx.get("exchange", "NFO"),
                                  "alerted": set(ctx.get("alerted", [])), "ltp": None, "alert": None}

    # ------------------------------------------------------------------ prices
    async def ltp(self, sig_or_state: Any) -> float | None:
        rt = self.rt
        inst = getattr(sig_or_state, "instrument", None)
        token = inst.token if inst is not None else sig_or_state.get("token")
        if not token:
            return None
        cached = self._ltp_cache.get(token)
        if cached and (datetime.now().timestamp() - cached[1]) < 4:
            return cached[0]
        px = None
        if rt.angel is not None and rt.angel.connected and inst is not None:
            try:
                px = await rt.angel.get_ltp(inst.exchange, inst.token, inst.symbol)
            except Exception as exc:  # noqa: BLE001
                log.debug("ltp failed: %s", exc)
        if px is None and inst is not None:
            px = rt.market.demo_price(inst)          # model price from the live index level
        if px:
            self._ltp_cache[token] = (float(px), datetime.now().timestamp())
        return px

    def _delta(self, sig: Signal) -> float:
        inst = sig.instrument
        if inst is None or inst.kind is not InstrumentKind.OPTION or not inst.strike or not inst.expiry:
            return 1.0
        from indexedge.features.options import bs_greeks

        spot = self.rt.market.ltp.get(sig.raw.underlying) or sig.raw.entry
        exp = datetime.combine(inst.expiry, datetime.min.time(), IST) + timedelta(hours=15, minutes=30)
        t = max((exp - self.rt.clock.now()).total_seconds() / (365 * 86400), 1 / (365 * 24))
        iv = (self.rt.market.vix or 13.5) / 100
        try:
            d = abs(float(bs_greeks(spot, inst.strike, t, 0.065, iv, inst.option_type.value)["delta"]))
        except Exception:  # noqa: BLE001
            d = 0.5
        return min(max(d, 0.15), 0.95)

    # ------------------------------------------------------------------ ticket
    async def ticket(self, sig: Signal) -> dict[str, Any]:
        raw, inst = sig.raw, sig.instrument
        if inst is None:
            raise ValueError("No tradable instrument for this signal (instrument master not loaded)")
        is_opt = inst.kind is InstrumentKind.OPTION
        lot = int(inst.lot_size or 1)
        qty = int(sig.quantity or lot)
        lots = max(1, qty // lot)
        qty = lots * lot
        ltp = await self.ltp(sig) or 0.0
        risk_pts = raw.risk_points
        reward_pts = abs(raw.targets[0] - raw.entry) if raw.targets else 2 * risk_pts
        if is_opt:                       # long CE for bullish, long PE for bearish signals
            side = "BUY"
            delta = self._delta(sig)
            limit = _tick(ltp * 1.004 + TICK, "up")
            stop_trigger = _tick(max(TICK, ltp - delta * risk_pts), "down")
            stop_limit = _tick(max(TICK, stop_trigger * 0.97), "down")
            target = _tick(ltp + delta * reward_pts, "nearest")
            basis = "option"
            notes = [f"Premium levels use the option delta ≈ {delta:.2f}; the index levels below are the plan.",
                     "Place the stop-loss as a separate SL-L SELL order right after your buy fills."]
        else:
            side = raw.side.value
            basis_pts = (ltp - (self.rt.market.ltp.get(raw.underlying) or raw.entry)) if ltp else 0.0
            sgn = 1 if side == "BUY" else -1
            limit = _tick((ltp or raw.entry) + sgn * 1.0, "up" if sgn > 0 else "down")
            stop_trigger = _tick(raw.stop + basis_pts)
            stop_limit = _tick(stop_trigger - sgn * max(1.0, stop_trigger * 0.002))
            target = _tick(raw.targets[0] + basis_pts) if raw.targets else None
            basis = "future"
            notes = ["Futures levels = index levels + current basis."]
        exit_side = "SELL" if side == "BUY" else "BUY"
        und = raw.underlying
        idx_dir = "below" if raw.side.value == "BUY" else "above"
        steps = [
            f"Angel One → search {inst.symbol} → {side}.",
            f"Product INTRADAY, order type LIMIT, quantity {qty} ({lots} lot{'s' if lots > 1 else ''} × {lot}), "
            f"price ₹{limit:,.2f}.",
            f"After it fills: place {exit_side} SL-L — trigger ₹{stop_trigger:,.2f}, limit ₹{stop_limit:,.2f}.",
            f"Target ₹{target:,.2f}" + (f" (or exit when {und} reaches {raw.targets[0]:,.1f})" if raw.targets else "")
            if target else "Exit on the index target.",
            f"Exit immediately if {und} closes {idx_dir} {raw.stop:,.1f} — IndexEdge will alert you.",
            "Tap “I placed this trade” so IndexEdge can track it.",
        ]
        return {"signal_id": sig.id, "strategy_name": getattr(self.rt.strategies.get(raw.strategy_id), "name",
                                                              raw.strategy_id),
                "underlying": und, "exchange": inst.exchange, "symbol": inst.symbol, "token": inst.token,
                "side": side, "exit_side": exit_side, "lots": lots, "lot_size": lot, "quantity": qty,
                "product": "INTRADAY", "order_type": "LIMIT", "ltp": round(ltp, 2), "limit_price": limit,
                "stop_trigger": stop_trigger, "stop_limit": stop_limit, "target": target, "premium_basis": basis,
                "index_levels": {"entry": raw.entry, "stop": raw.stop, "targets": list(raw.targets),
                                 "direction": raw.side.value},
                "price_source": "angel_one" if (self.rt.angel and self.rt.angel.connected) else "model",
                "notes": notes + ([] if self.rt.angel and self.rt.angel.connected else
                                  ["Option price is estimated (Angel One not connected) — use the live price in "
                                   "Angel One."]),
                "steps": steps}

    # ------------------------------------------------------------------ entry / exit
    async def enter(self, sig: Signal, fill_price: float, quantity: int) -> str:
        t = await self.ticket(sig)
        if fill_price <= 0 or quantity <= 0:
            raise ValueError("Fill price and quantity must be positive")
        scale = fill_price / t["limit_price"] if t["limit_price"] else 1.0
        rec = TradeRecord(
            signal_id=sig.id, strategy_id=sig.raw.strategy_id, underlying=sig.raw.underlying,
            instrument_symbol=t["symbol"], side=t["side"], qty=int(quantity), entry_ts=self.rt.clock.now(),
            entry_price=float(fill_price), stop=round(t["stop_trigger"] * scale, 2) if t["premium_basis"] == "option"
            else t["stop_trigger"], targets=[t["target"]] if t["target"] else [], mode="manual",
            planned_entry=t["limit_price"], regime=sig.regime.value if hasattr(sig.regime, "value") else str(sig.regime),
            reasons=list(sig.reasons),
            context={"index_levels": t["index_levels"], "signal_side": sig.raw.side.value, "token": t["token"],
                     "exchange": t["exchange"], "ticket": t, "alerted": []})
        tid = self.rt.journal.open_trade(rec)
        self.rt.journal.update_signal_status(sig.id, "taken", "placed manually on Angel One")
        sig.status = "taken"
        self.open[tid] = {"row": {**rec.__dict__, "id": tid}, "index_levels": t["index_levels"],
                          "signal_side": sig.raw.side.value, "token": t["token"], "exchange": t["exchange"],
                          "alerted": set(), "ltp": None, "alert": None, "instrument": sig.instrument}
        await self.rt.agent.log("trade", f"Manual trade recorded: {t['side']} {quantity} {t['symbol']} @ "
                                f"₹{fill_price:,.2f} ({t['strategy_name']})", "trade")
        return tid

    async def exit(self, trade_id: str, exit_price: float, reason: str = "manual") -> None:
        st = self.open.pop(trade_id, None)
        row = st["row"] if st else self.rt.journal.trade(trade_id)
        if row is None:
            raise KeyError("trade not found")
        sgn = 1 if row["side"] == "BUY" else -1
        qty = int(row["qty"])
        pnl = sgn * (float(exit_price) - float(row["entry_price"])) * qty
        kind = "option" if str(row["instrument_symbol"]).endswith(("CE", "PE")) else "future"
        costs = 0.0
        try:
            costs = float(self.rt.composer.cost_fn(kind, qty, float(row["entry_price"]), float(exit_price)))
        except Exception:  # noqa: BLE001
            pass
        self.rt.journal.close_trade(trade_id, self.rt.clock.now(), float(exit_price), reason, pnl, costs)
        await self.rt.agent.log("trade", f"Manual trade closed: {row['instrument_symbol']} @ ₹{exit_price:,.2f}, "
                                f"P&L ₹{pnl - costs:,.0f} after costs", "trade")

    # ------------------------------------------------------------------ monitoring
    async def check(self) -> None:
        """Called every few seconds: update prices and alert once per level."""
        rt = self.rt
        now = rt.clock.now()
        cutoff = parse_hhmm(rt.settings.broker.intraday_cutoff)
        sq_min = rt.settings.execution.square_off_minutes_before_cutoff
        for tid, st in list(self.open.items()):
            lv = st["index_levels"] or {}
            u = st["row"].get("underlying")
            spot = rt.market.ltp.get(u)
            inst = st.get("instrument") or (rt.master.by_token(st["token"]) if st.get("token") else None)
            if inst is not None:
                st["instrument"] = inst
                px = await self.ltp(type("S", (), {"instrument": inst})())
                if px:
                    st["ltp"] = px
            if spot is None or not lv:
                continue
            long_idx = lv.get("direction", st["signal_side"]) == "BUY"
            alerts = []
            if lv.get("stop") is not None and ((spot <= lv["stop"]) if long_idx else (spot >= lv["stop"])):
                alerts.append(("stop", "STOP HIT — exit now on Angel One",
                               f"{u} {spot:,.1f} crossed the stop {lv['stop']:,.1f}. Exit "
                               f"{st['row']['instrument_symbol']} now (the SL-L order should trigger)."))
            for k, tg in enumerate(lv.get("targets") or []):
                if (spot >= tg) if long_idx else (spot <= tg):
                    alerts.append((f"t{k + 1}", f"TARGET {k + 1} REACHED",
                                   f"{u} {spot:,.1f} reached target {tg:,.1f}. Book profit or trail your stop on "
                                   f"{st['row']['instrument_symbol']}."))
            sq = (datetime.combine(now.date(), cutoff, IST) - timedelta(minutes=sq_min)).time()
            if now.time() >= sq:
                alerts.append(("eod", "SQUARE OFF NOW",
                               f"Angel One auto-squares intraday F&O at {rt.settings.broker.intraday_cutoff}. Exit "
                               f"{st['row']['instrument_symbol']} now."))
            for key, title, body in alerts:
                st["alert"] = title
                if key in st["alerted"]:
                    continue
                st["alerted"].add(key)
                await rt.bus.publish(topics.ALERT, {"level": "critical" if key in ("stop", "eod") else "info",
                                                    "title": title, "message": body})

    def open_payload(self) -> list[dict[str, Any]]:
        out = []
        for tid, st in self.open.items():
            r = st["row"]
            ltp = st.get("ltp")
            sgn = 1 if r["side"] == "BUY" else -1
            pnl = round(sgn * (ltp - float(r["entry_price"])) * int(r["qty"]), 2) if ltp else None
            out.append({"id": tid, "signal_id": r.get("signal_id"), "strategy_id": r.get("strategy_id"),
                        "underlying": r.get("underlying"), "instrument_symbol": r.get("instrument_symbol"),
                        "side": r["side"], "qty": int(r["qty"]), "entry_price": float(r["entry_price"]),
                        "entry_ts": r.get("entry_ts"), "stop": r.get("stop"), "targets": r.get("targets") or [],
                        "mode": "manual", "status": "open", "ltp": ltp, "pnl": pnl, "alert": st.get("alert"),
                        "index_levels": st.get("index_levels")})
        return out
