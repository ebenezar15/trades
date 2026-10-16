"""Pre-market checklist and end-of-day report (template-based, no LLM)."""
from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from indexedge.context.engine import snapshot_to_dict
from indexedge.features.indicators import cpr, daily_ohlc
from indexedge.news.calendar import event_to_dict


def _levels(frame: pd.DataFrame) -> dict[str, float | None]:
    if frame.empty:
        return {}
    daily = daily_ohlc(frame)
    if daily.empty:
        return {}
    lv = cpr(daily)
    row = lv.iloc[-1]
    last = daily.iloc[-1]
    out = {k: round(float(row[k]), 2) for k in ("pivot", "bc", "tc", "r1", "r2", "s1", "s2") if k in row}
    out["width_pct"] = round(float(row.get("width_pct", float("nan"))), 3)
    out.update(prev_high=round(float(last["high"]), 2), prev_low=round(float(last["low"]), 2),
               prev_close=round(float(last["close"]), 2))
    return out


def premarket_report(rt: Any) -> dict[str, Any]:
    ctx = rt.context.latest
    ctx_d = snapshot_to_dict(ctx) if ctx else None
    gap = None
    if ctx_d:
        gift = (ctx_d.get("inputs") or {}).get("gift_nifty") or {}
        gap = gift.get("gap_expectation_pct", gift.get("change_pct"))
    levels = {u: _levels(rt.market.frame(u)) for u in ("NIFTY", "BANKNIFTY")}
    regimes = {}
    for u in ("NIFTY", "BANKNIFTY"):
        try:
            rt.signals.compute_features(u)
        except Exception:  # noqa: BLE001
            pass
        regimes[u] = rt.signals.regime(u)
    events = [event_to_dict(e) for e in rt.calendar.on_day(rt.today())]
    enabled = []
    for sid, strat in rt.strategies.items():
        regs = {r.value for r in strat.regimes} if strat.regimes else None
        fits = regs is None or regimes.get("NIFTY") in regs
        enabled.append({"id": sid, "name": strat.name, "enabled": rt.strategy_enabled(sid), "regime_fit": fits})
    checklist = [
        {"item": "Broker logged in", "ok": bool(rt.angel and rt.angel.connected) or rt.market.demo},
        {"item": "Static IP matches (live only)", "ok": rt.static_ip.get("ok") is not False},
        {"item": "External context fresh", "ok": bool(ctx and ctx.usable)},
        {"item": "No high-impact event in next 2h", "ok": rt.calendar.event_risk(rt.now()) < 0.5},
        {"item": "Risk limits not locked", "ok": not rt.risk.locked},
    ]
    bias = ctx.macro_bias if ctx else 0.0
    lines = [f"🌅 Pre-market {rt.today():%d %b %Y}",
             f"Global bias {bias:+.2f} ({'bullish' if bias > 0.15 else 'bearish' if bias < -0.15 else 'neutral'})"
             + (" · RISK-OFF" if ctx and ctx.risk_off else "")]
    if gap is not None:
        lines.append(f"Expected gap (GIFT Nifty): {gap:+.2f}%")
    nl = levels.get("NIFTY") or {}
    if nl:
        lines.append(f"NIFTY pivot {nl.get('pivot')} · R1 {nl.get('r1')} · S1 {nl.get('s1')} · CPR width "
                     f"{nl.get('width_pct')}%")
    for e in events:
        lines.append(f"📅 {e['title']} ({e.get('impact')})")
    lines += [f"{'✅' if c['ok'] else '❌'} {c['item']}" for c in checklist]
    return {"date": rt.today().isoformat(), "context": ctx_d, "gap_expectation": gap, "key_levels": levels,
            "events_today": events, "enabled_strategies": enabled, "regime": regimes, "checklist": checklist,
            "text": "\n".join(lines)}


def eod_report(rt: Any, day: date) -> dict[str, Any]:
    from indexedge.journal.analytics import discipline_score, performance

    trades = rt.journal.trades(start=day, end=day)
    if not trades.empty:
        trades = trades[(trades["mode"] != "backtest") & (trades["outcome"].fillna("") != "void_restart")]
    sigs = rt.journal.signals(start=day, end=day) if hasattr(rt.journal, "signals") else pd.DataFrame()
    perf = performance(trades, capital=rt.settings.risk.capital) if not trades.empty else {}
    pnl = float(trades["net_pnl"].sum()) if not trades.empty and "net_pnl" in trades else 0.0
    try:
        disc = discipline_score(trades)
    except Exception:  # noqa: BLE001
        disc = None
    by_strategy = {}
    if not trades.empty:
        for sid, g in trades.groupby("strategy_id"):
            by_strategy[sid] = {"trades": len(g), "pnl": round(float(g.get("net_pnl", g["pnl"]).sum()), 2)}
    n_sig = len(sigs) if sigs is not None else 0
    taken = int((sigs["status"] == "taken").sum()) if n_sig and "status" in sigs else 0
    text = "\n".join([
        f"🌙 EOD {day:%d %b %Y}",
        f"Net P&L ₹{pnl:,.0f} on {len(trades)} trade(s) · signals {n_sig} (taken {taken})",
        *(f"• {k}: {v['trades']} trade(s), ₹{v['pnl']:,.0f}" for k, v in by_strategy.items()),
        f"Discipline score: {disc.get('score') if isinstance(disc, dict) else disc}" if disc is not None else "",
    ]).strip()
    return {"date": day.isoformat(), "realised_pnl": round(pnl, 2), "trades": len(trades),
            "signals": n_sig, "signals_taken": taken, "by_strategy": by_strategy, "performance": perf,
            "discipline": disc, "trade_list": trades.to_dict(orient="records") if not trades.empty else [],
            "text": text}
