"""REST API (contract: docs/superpowers/plans/2026-10-16-indexedge.md, "REST / WebSocket API contract")."""
from __future__ import annotations

import asyncio
import logging
import random
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import FileResponse

from indexedge.api.serialize import candles_json, jsonable, line_json
from indexedge.config.secrets import SECRET_NAMES, secrets_status, set_secret
from indexedge.core.clock import IST
from indexedge.core.types import ExecutionMode
from indexedge.core.underlyings import UNDERLYINGS

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


def rt_of(request: Request):
    return request.app.state.rt


def _u(underlying: str) -> str:
    u = (underlying or "NIFTY").upper()
    if u not in UNDERLYINGS:
        raise HTTPException(400, f"underlying must be one of {UNDERLYINGS}")
    return u


# ====================================================================== status / overview
@router.get("/status")
def status(request: Request):
    return jsonable(rt_of(request).status())


@router.get("/overview")
def overview(request: Request):
    rt = rt_of(request)
    out = []
    for u in UNDERLYINGS:
        ltp = rt.market.ltp.get(u)
        prev = rt.market.prev_close.get(u)
        frame = rt.market.frame(u)
        if ltp is None and not frame.empty:
            ltp = float(frame["close"].iloc[-1])
        if prev is None or (prev == ltp and not frame.empty):
            days = sorted(set(frame.index.date))
            if len(days) >= 2:
                prev = float(frame[frame.index.date == days[-2]]["close"].iloc[-1])
        chg = ((ltp - prev) / prev * 100) if ltp and prev else 0.0
        chain = None
        for (cu, _), (_, payload) in rt.market._chain_cache.items():
            if cu == u:
                chain = payload
        out.append({"underlying": u, "ltp": ltp, "change_pct": round(chg, 2), "regime": rt.signals.regime(u),
                    "vix": round(rt.market.vix, 2) if rt.market.vix else None,
                    "pcr": chain.get("pcr") if chain else None, "max_pain": chain.get("max_pain") if chain else None})
    return jsonable({"indices": out})


@router.get("/candles")
async def candles(request: Request, underlying: str = "NIFTY", tf: str = "5m", limit: int = 500):
    rt = rt_of(request)
    u = _u(underlying)
    feat = rt.signals.features.get(u)
    if feat is None or feat.empty or feat.index[-1] != rt.market.frame(u).index[-1]:
        feat = await asyncio.to_thread(rt.signals.compute_features, u)
    if feat.empty:
        return {"candles": [], "overlays": {"ema9": [], "ema21": [], "vwap": [], "supertrend": []}, "levels": {}}
    if tf != "5m":
        from indexedge.data.store import resample

        bars = resample(rt.market.frame(u), tf).tail(limit)
        return {"candles": candles_json(bars), "overlays": {"ema9": [], "ema21": [], "vwap": [], "supertrend": []},
                "levels": {}}
    view = feat.tail(limit)
    if rt.market.feed == "yahoo" and rt.market.yahoo is not None:
        cur = rt.market.yahoo.forming.get(u)
    else:
        cur = rt.market._agg.current(f"DEMO_{u}") if rt.market.demo else None
    cands = candles_json(view)
    if cur is not None:
        t = int(pd.Timestamp(cur.name).timestamp())
        if not cands or t > cands[-1]["time"]:
            cands.append({"time": t, "open": float(cur["open"]), "high": float(cur["high"]),
                          "low": float(cur["low"]), "close": float(cur["close"]), "volume": 0.0})
    st = view[["supertrend", "st_dir"]].dropna()
    last = view.iloc[-1]
    levels = {k: (None if pd.isna(last.get(k)) else round(float(last[k]), 2))
              for k in ("pivot", "bc", "tc", "r1", "s1", "prev_high", "prev_low", "or_high", "or_low")}
    return {
        "candles": cands,
        "overlays": {
            "ema9": line_json(view["ema9"]), "ema21": line_json(view["ema21"]), "vwap": line_json(view["vwap"]),
            "supertrend": [{"time": int(t.timestamp()), "value": round(float(r.supertrend), 2),
                            "direction": int(r.st_dir)} for t, r in st.iterrows()],
        },
        "levels": levels,
        "markers": _chart_markers(rt, u, int(pd.Timestamp(view.index[0]).timestamp())),
        "trade_lines": _trade_lines(rt, u),
    }


def _chart_markers(rt: Any, u: str, since: int) -> list[dict]:
    """Signals on this underlying as chart markers (bar time = signal bar)."""
    seen, out = set(), []
    rows = [rt.signals.to_json(s) for s in list(rt.signals.recent)]
    try:
        df = rt.journal.signals(start=rt.today() - timedelta(days=5), limit=300)
        rows += [_journal_signal_json(rt, r) for r in (df.to_dict(orient="records") if not df.empty else [])]
    except Exception:
        log.exception("marker journal read failed")
    for d in rows:
        if d.get("underlying") != u or d.get("id") in seen:
            continue
        seen.add(d.get("id"))
        try:
            t = int(pd.Timestamp(d["ts"]).timestamp())
        except Exception:
            continue
        if t < since:
            continue
        long = d.get("side") == "BUY"
        status = d.get("status") or ""
        color = ("#22c55e" if long else "#ef4444") if status in ("active", "taken", "pending_confirm") else "#94a3b8"
        name = d.get("strategy_name") or d.get("strategy_id")
        out.append({"time": t, "position": "belowBar" if long else "aboveBar",
                    "shape": "arrowUp" if long else "arrowDown", "color": color,
                    # label only actionable signals; blocked ones are plain grey arrows (keeps the chart readable)
                    "text": (f"{name}{' ✓' if status == 'taken' else ''}"
                             if status in ("active", "taken", "pending_confirm") else ""),
                    "signal_id": d.get("id"), "status": status})
    out.sort(key=lambda m: m["time"])
    out = out[-60:]
    # keep text only on the latest actionable marker of each strategy (max 3) so labels don't pile up
    keep, names = set(), set()
    for k in range(len(out) - 1, -1, -1):
        m = out[k]
        name = m["text"].rstrip(" ✓")
        if m["text"] and name not in names and len(keep) < 3:
            keep.add(k)
            names.add(name)
    for k, m in enumerate(out):
        if k not in keep:
            m["text"] = ""
    return out


def _trade_lines(rt: Any, u: str) -> list[dict]:
    """Open trades on this underlying as entry / stop / target price lines in index points."""
    lines = []
    for t in rt.trades.open_trades:
        if t.underlying != u:
            continue
        sig = rt.signals.by_id.get(t.signal_id) if t.signal_id else None
        if sig is not None:
            entry, stop, targets, side = sig.raw.entry, sig.raw.stop, list(sig.raw.targets), sig.raw.side.value
        else:
            entry, stop, targets, side = t.entry_price, t.stop, list(t.targets), t.side.value
        strat = rt.strategies.get(t.strategy_id)
        lines.append({"trade_id": t.trade_id, "side": side, "entry": entry, "stop": stop, "targets": targets,
                      "status": "open", "label": getattr(strat, "name", t.strategy_id)})
    return lines


@router.get("/context")
def context(request: Request):
    from indexedge.context.engine import snapshot_to_dict

    snap = rt_of(request).context.latest
    if snap is None:
        return {"ts": None, "macro_bias": 0.0, "risk_off": False, "event_risk": 0.0, "usable": False, "inputs": {},
                "reasons": ["External context not fetched yet"], "stale_required": []}
    return jsonable(snapshot_to_dict(snap))


# ====================================================================== signals
def _journal_signal_json(rt: Any, r: dict) -> dict:
    """Signal row from the journal (e.g. after a restart) in the SignalJSON shape."""
    strat = rt.strategies.get(r.get("strategy_id"))
    inst = r.get("instrument")
    ci = r.get("probability_ci")
    return {
        "id": r.get("id"), "strategy_id": r.get("strategy_id"),
        "strategy_name": getattr(strat, "name", r.get("strategy_id")), "underlying": r.get("underlying"),
        "side": r.get("side"), "ts": r.get("ts"), "entry": r.get("entry"), "stop": r.get("stop"),
        "targets": r.get("targets") or [], "probability": r.get("probability"),
        "probability_ci": list(ci) if ci else None, "sample_size": r.get("sample_size") or 0,
        "expected_rr": r.get("expected_rr") or 0, "ev_per_trade": r.get("ev_per_trade") or 0,
        "quantity": r.get("quantity") or 0, "instrument": inst if isinstance(inst, dict) else None,
        "badges": r.get("badges") or [], "reasons": r.get("reasons") or [],
        "context_reasons": r.get("context_reasons") or [], "news_reasons": r.get("news_reasons") or [],
        "top_features": r.get("top_features") or [], "regime": r.get("regime") or "unknown",
        "status": "expired" if r.get("status") in ("active", "pending_confirm") else r.get("status"),
        "blocked_reason": r.get("blocked_reason") or "",
    }


@router.get("/signals")
def signals(request: Request, status: str | None = None, limit: int = 100, underlying: str | None = None):
    rt = rt_of(request)
    items, seen = [], set()
    for sig in list(rt.signals.recent):
        d = rt.signals.to_json(sig)
        seen.add(d["id"])
        items.append(d)
    if len(items) < limit:            # older / pre-restart signals from the journal
        try:
            df = rt.journal.signals(start=rt.today() - timedelta(days=3), limit=limit)
            for r in (df.to_dict(orient="records") if not df.empty else []):
                if r.get("id") not in seen:
                    items.append(_journal_signal_json(rt, r))
        except Exception:
            log.exception("journal signals read failed")
    out = []
    for d in items:
        if status and d.get("status") != status:
            continue
        if underlying and d.get("underlying") != underlying.upper():
            continue
        out.append(d)
        if len(out) >= limit:
            break
    return jsonable({"signals": out})


@router.post("/signals/{sid}/confirm")
async def confirm(request: Request, sid: str):
    return jsonable(await rt_of(request).confirm_signal(sid))


@router.post("/signals/{sid}/skip")
async def skip(request: Request, sid: str):
    return jsonable(await rt_of(request).skip_signal(sid))


# ====================================================================== positions / kill switch
@router.get("/positions")
async def positions(request: Request):
    rt = rt_of(request)
    try:
        pos = await rt.exec_broker.positions()
    except Exception as exc:
        log.warning("positions failed: %s", exc)
        pos = []
    plist = [{"symbol": p.instrument.symbol, "qty": p.net_qty, "avg_price": p.avg_price, "ltp": p.ltp,
              "pnl": round(p.unrealised_pnl + p.realised_pnl, 2)} for p in pos if p.net_qty or p.realised_pnl]
    open_trades = [t.to_dict() for t in rt.trades.open_trades] + rt.manual.open_payload()
    return jsonable({"positions": plist, "open_trades": open_trades})


@router.post("/kill-switch")
async def kill_switch(request: Request):
    res = await rt_of(request).kill_switch()
    return jsonable({"ok": bool(res.get("ok", True)), "cancelled": len(res.get("cancelled", []) or []) if isinstance(res.get("cancelled"), list)
                     else res.get("cancelled", 0),
                     "squared_off": len(res.get("squared_off", []) or []) if isinstance(res.get("squared_off"), list)
                     else res.get("squared_off", 0), "detail": res})


# ====================================================================== option chain
@router.get("/option-chain")
async def option_chain(request: Request, underlying: str = "NIFTY", expiry: str | None = None):
    rt = rt_of(request)
    exp = date.fromisoformat(expiry) if expiry else None
    return jsonable(await rt.market.option_chain(_u(underlying), exp))


# ====================================================================== news / events
@router.get("/news")
def news(request: Request, limit: int = 50):
    rt = rt_of(request)
    items = rt.news.latest(limit)
    if not items:
        df = rt.journal.news(limit)
        return jsonable({"items": df.to_dict(orient="records") if not df.empty else []})
    return jsonable({"items": [{"id": i.id, "ts": i.ts, "source": i.source, "title": i.title, "url": i.url,
                                "sentiment": i.sentiment, "relevance": i.relevance, "impact": i.impact}
                               for i in items]})


@router.get("/events")
def events(request: Request, days: int = 14):
    from indexedge.news.calendar import event_to_dict

    rt = rt_of(request)
    ev = [event_to_dict(e) for e in rt.calendar.upcoming(days)]
    b = rt.calendar.in_blackout()
    return jsonable({"events": ev, "blackout": {"active": b is not None, "event": event_to_dict(b) if b else None},
                     "event_risk": rt.calendar.event_risk()})


# ====================================================================== trades / journal
def _trade_rows(df: pd.DataFrame) -> list[dict]:
    return jsonable(df.to_dict(orient="records")) if df is not None and not df.empty else []


@router.get("/trades")
def trades(request: Request, start: str | None = None, end: str | None = None, strategy: str | None = None,
           mode: str | None = None, status: str | None = None):
    rt = rt_of(request)
    df = rt.journal.trades(start=date.fromisoformat(start) if start else None,
                           end=date.fromisoformat(end) if end else None, strategy=strategy or None,
                           mode=mode or None, status=status or None)
    if not df.empty:
        df = df[df["mode"] != "backtest"]
    rows = _trade_rows(df.sort_values("entry_ts", ascending=False) if not df.empty else df)
    live = {t.trade_id: t for t in rt.trades.open_trades}
    for r in rows:
        mt = live.get(r.get("id"))
        if mt is not None:
            r["ltp"] = mt.last_ltp
    return {"trades": rows}


@router.get("/trades/{tid}")
def trade_detail(request: Request, tid: str):
    from indexedge.journal.analysis import post_trade_analysis

    rt = rt_of(request)
    t = rt.journal.trade(tid)
    if t is None:
        raise HTTPException(404, "trade not found")
    analysis = None
    if t.get("status") == "closed":
        try:
            frame = rt.market.frame(t.get("underlying") or "NIFTY")
            et, xt = pd.Timestamp(t["entry_ts"]), pd.Timestamp(t["exit_ts"])
            bars = frame[(frame.index >= et.floor("5min")) & (frame.index <= xt)]
            sym = str(t.get("instrument_symbol") or "")
            # index candles can't measure an option's excursions: use the trade's own recorded MFE/MAE
            analysis = post_trade_analysis(t, None if sym.endswith(("CE", "PE")) else bars)
        except Exception:
            log.exception("post-trade analysis failed")
    notes = rt.journal.notes(tid)
    return jsonable({**t, "analysis": analysis,
                     "notes": notes.to_dict(orient="records") if not notes.empty else []})


@router.post("/trades/{tid}/notes")
def add_note(request: Request, tid: str, body: dict = Body(...)):
    rt = rt_of(request)
    nid = rt.journal.add_note(tid, str(body.get("text", "")), list(body.get("tags") or []),
                              str(body.get("emotion") or ""))
    return {"ok": True, "id": nid}


@router.get("/export")
def export(request: Request, fmt: str = "csv"):
    from indexedge.journal.export import export_journal

    if fmt not in ("csv", "xlsx"):
        raise HTTPException(400, "fmt must be csv or xlsx")
    rt = rt_of(request)
    out = Path(tempfile.gettempdir()) / f"indexedge_journal_{rt.today():%Y%m%d}.{fmt}"
    path = export_journal(rt.journal, out, fmt)
    return FileResponse(path, filename=Path(path).name)


@router.get("/analytics")
def analytics(request: Request, mode: str | None = None):
    from indexedge.journal.analytics import discipline_score, monthly_summaries, performance, weekly_summaries

    rt = rt_of(request)
    df = rt.journal.trades(mode=mode or None)
    if not df.empty:
        df = df[(df["mode"] != "backtest") & (df["outcome"].fillna("") != "void_restart")]
    perf = performance(df, capital=rt.settings.risk.capital)
    for key, val in list(perf.items()):      # field names the UI expects
        if key.startswith("by_") and isinstance(val, dict):
            for row in val.values():
                if isinstance(row, dict):
                    row.setdefault("pnl", row.get("net_pnl") or 0.0)
                    row.setdefault("expectancy_r", row.get("avg_r"))
    perf.setdefault("expectancy_r", perf.get("avg_r"))
    try:
        perf["discipline"] = discipline_score(df, rt.settings.risk.max_trades_per_day,
                                              rt.settings.risk.cooldown_after_loss_min,
                                              rt.settings.risk.revenge_size_multiplier)
        perf["discipline_score"] = perf["discipline"].get("score")
        perf["weekly"] = weekly_summaries(df)
        perf["monthly"] = monthly_summaries(df)
    except Exception:
        log.exception("analytics extras failed")
    if not df.empty:
        perf.setdefault("total_pnl", float(df["net_pnl"].sum()))
    sig = rt.journal.signals()
    if not sig.empty and "status" in sig:
        perf["skipped_signals"] = int((sig["status"] == "skipped").sum())
        perf["signals_total"] = len(sig)
    return jsonable(perf)


# ====================================================================== strategies / backtests / models
@router.get("/strategies")
def strategies(request: Request, underlying: str = "NIFTY"):
    rt = rt_of(request)
    reps = rt.reports.get(_u(underlying), {})
    out = []
    for sid, s in rt.strategies.items():
        info = s.info()
        rep = reps.get(sid)
        if rep is not None:
            rep = {**rep, "metrics": rep.get("metrics") or rep.get("summary") or {}}
        out.append({
            "id": sid, "name": s.name, "description": s.description, "timeframe": s.timeframe,
            "regimes": info.get("regimes"), "enabled": rt.strategy_enabled(sid, _u(underlying)),
            "enabled_source": "user" if sid in rt.settings.strategies.enabled else "auto",
            "execution_mode": rt.execution_mode(sid).value,
            "params": {k: v for k, v in s.params.items() if isinstance(v, (int, float, str, bool))},
            "report": rep, "learn": s.learn, "backtest_quality": s.backtest_quality,
            "instrument_hint": s.instrument_hint,
        })
    return jsonable({"strategies": out, "reports_meta": rt.report_meta,
                     "reports_job": rt.jobs.get("reports")})


@router.post("/strategies/revalidate")
async def revalidate(request: Request):
    rt = rt_of(request)
    if (rt.jobs.get("reports") or {}).get("status") == "running":
        return {"ok": False, "message": "validation already running"}
    asyncio.create_task(rt.refresh_reports())
    return {"ok": True, "message": "validation started"}


@router.post("/strategies/{sid}")
def update_strategy(request: Request, sid: str, body: dict = Body(...)):
    rt = rt_of(request)
    if sid not in rt.strategies:
        raise HTTPException(404, "unknown strategy")
    st = rt.settings.strategies
    if "enabled" in body:
        old = rt.strategy_enabled(sid)
        st.enabled[sid] = bool(body["enabled"])
        rt.journal.audit_setting(f"strategies.enabled.{sid}", old, st.enabled[sid])
    if "execution_mode" in body:
        mode = ExecutionMode(body["execution_mode"])
        st.execution_mode[sid] = mode
    if "params" in body and isinstance(body["params"], dict):
        from indexedge.strategies.registry import get_strategy

        params = {**st.params.get(sid, {}), **body["params"]}
        try:
            rt.strategies[sid] = get_strategy(sid, params)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        st.params[sid] = params
    rt.settings.save_user()
    rt.refresh_composer_settings()
    rt.composer.strategies = rt.strategies
    return {"ok": True}


@router.post("/backtest")
async def backtest(request: Request, body: dict = Body(...)):
    rt = rt_of(request)
    sid = body.get("strategy_id")
    if sid not in rt.strategies:
        raise HTTPException(404, "unknown strategy")
    days = int(body.get("days") or 250)
    params = rt.settings.strategies.params.get(sid)
    res = await asyncio.to_thread(rt.lab.backtest, sid, _u(body.get("underlying", "NIFTY")), max(20, min(days, 1500)),
                                  params)
    res["trades"] = res.get("trades", [])[-500:]
    return jsonable(res)


@router.post("/models/retrain")
async def retrain(request: Request):
    rt = rt_of(request)
    asyncio.create_task(rt.retrain_models())
    return {"ok": True, "message": "retraining started"}


@router.get("/models/{sid}")
def model(request: Request, sid: str):
    rt = rt_of(request)
    bundle = rt.registry.get(sid)
    if bundle is None:
        return {"metrics": None, "reliability": [], "message": "No model trained yet (needs ≥50 historical setups)"}
    m = dict(getattr(bundle, "metrics", {}) or {})
    rel = m.pop("reliability", None) or m.pop("reliability_bins", None) or []
    m["usable"] = bool(getattr(bundle, "usable", False))
    return jsonable({"metrics": m, "reliability": rel})


# ====================================================================== replay / learn
def _replay_frame(rt: Any, u: str) -> pd.DataFrame:
    return rt.market.frame(u)


@router.get("/replay/days")
def replay_days(request: Request, underlying: str = "NIFTY"):
    rt = rt_of(request)
    return {"days": [d.isoformat() for d in _complete_days(rt, _u(underlying))[-120:]][::-1],
            "data_source": rt.market.data_source}


def _complete_days(rt: Any, u: str) -> list[date]:
    """Sessions available for replay/quiz: today's session is excluded while it is still in progress."""
    days = sorted(set(_replay_frame(rt, u).index.date))
    if days and days[-1] == rt.today() and rt.clock.is_market_open():
        days = days[:-1]
    return days


def _day_session(rt: Any, u: str, day: date):
    """Features for [day - 5 sessions, day] so indicators are warmed up; returns (feat, day_mask)."""
    from indexedge.features.engine import build_features

    frame = _replay_frame(rt, u)
    days = sorted(set(frame.index.date))
    if day not in days:
        raise HTTPException(404, "no data for that day")
    k = days.index(day)
    sub = frame[(frame.index.date >= days[max(0, k - 6)]) & (frame.index.date <= day)]
    feat = build_features(sub)
    return feat, feat.index.date == day


@router.get("/replay")
async def replay(request: Request, underlying: str = "NIFTY", day: str | None = None):
    from indexedge.backtest.simulate import simulate_exit

    rt = rt_of(request)
    u = _u(underlying)
    d = date.fromisoformat(day) if day else _complete_days(rt, u)[-1]
    feat, mask = await asyncio.to_thread(_day_session, rt, u, d)
    day_feat = feat[mask]
    pos = {ts: i for i, ts in enumerate(day_feat.index)}
    sigs, outcomes = [], []
    for sid, strat in rt.strategies.items():
        try:
            found = strat.scan(feat, u, rt.market.oi_extra(u))
        except Exception:
            continue
        for s in found:
            i = pos.get(pd.Timestamp(s.ts))
            if i is None:
                continue
            sig_id = f"{sid}-{i}"
            sigs.append({"id": sig_id, "index": i, "strategy_id": sid, "strategy_name": strat.name,
                         "underlying": u, "side": s.side.value, "ts": s.ts, "entry": s.entry, "stop": s.stop,
                         "targets": s.targets, "reasons": s.reasons, "badges": [], "context_reasons": [],
                         "news_reasons": [], "top_features": [], "regime": str(day_feat["regime"].iloc[i])})
            out = simulate_exit(s, day_feat.iloc[i + 1:])
            if out is not None:
                outcomes.append({"signal_id": sig_id, "index": i, "outcome": out.outcome,
                                 "r": round(out.r_multiple, 2),
                                 "exit_index": pos.get(pd.Timestamp(out.exit_ts)), "exit_price": out.exit_price})
    sigs.sort(key=lambda x: x["index"])
    return jsonable({"day": d, "candles": candles_json(day_feat), "signals": sigs, "outcomes": outcomes,
                     "overlays": {"ema9": line_json(day_feat["ema9"]), "ema21": line_json(day_feat["ema21"]),
                                  "vwap": line_json(day_feat["vwap"])},
                     "data_source": rt.market.data_source})


@router.get("/learn/quiz")
async def quiz(request: Request, underlying: str = "NIFTY"):
    from indexedge.backtest.simulate import simulate_exit

    rt = rt_of(request)
    u = _u(underlying)
    days = _complete_days(rt, u)[-100:]
    rng = random.Random()
    for _ in range(25):
        d = rng.choice(days)
        feat, mask = await asyncio.to_thread(_day_session, rt, u, d)
        day_feat = feat[mask]
        pos = {ts: i for i, ts in enumerate(day_feat.index)}
        cands = []
        for sid, strat in rt.strategies.items():
            if strat.backtest_quality != "full":
                continue
            for s in strat.scan(feat, u):
                i = pos.get(pd.Timestamp(s.ts))
                if i is not None and i < len(day_feat) - 3:
                    cands.append((strat, s, i))
        if not cands:
            continue
        strat, s, i = rng.choice(cands)
        out = simulate_exit(s, day_feat.iloc[i + 1:])
        if out is None:
            continue
        good = out.r_multiple > 0
        answer = "take" if good else "skip"
        expl = (f"{strat.name} {s.side.value} at {s.entry:,.1f} (stop {s.stop:,.1f}, target "
                f"{s.targets[0]:,.1f}). Outcome: {out.outcome} for {out.r_multiple:+.2f}R. "
                + ("Taking it was right." if good else "Skipping it was right.")
                + " Reasons the setup fired: " + "; ".join(s.reasons[:3]))
        return jsonable({"day": d, "upto_index": i, "candles": candles_json(day_feat),
                         "question": f"{strat.name}: {s.side.value} {u} at {s.entry:,.1f}, stop {s.stop:,.1f}, "
                                     f"target {s.targets[0]:,.1f}. Would you take this trade?",
                         "choices": [{"id": "take", "label": "Take the trade"}, {"id": "skip", "label": "Skip it"}],
                         "answer_id": answer, "explanation": expl, "strategy_id": strat.id,
                         "signal": {"side": s.side.value, "entry": s.entry, "stop": s.stop, "targets": s.targets,
                                    "reasons": s.reasons}})
    raise HTTPException(404, "no quiz setups found in recent history")


# ====================================================================== reports
@router.get("/premarket")
def premarket(request: Request):
    from indexedge.services.reports import premarket_report

    rep = premarket_report(rt_of(request))
    gap = rep.get("gap_expectation")
    rep["gap_expectation"] = ({"underlying": "NIFTY", "pct": gap, "text": f"GIFT Nifty implies {gap:+.2f}% gap"}
                              if isinstance(gap, (int, float)) else None)
    rep["enabled_strategies"] = [{"id": e["id"], "name": e["name"], "suits_regime": e["regime_fit"],
                                  "enabled": e["enabled"]} for e in rep["enabled_strategies"]]
    return jsonable(rep)


@router.get("/eod")
def eod(request: Request, day: str | None = None):
    from indexedge.services.reports import eod_report

    rt = rt_of(request)
    d = date.fromisoformat(day) if day else rt.today()
    rep = eod_report(rt, d)
    rep.update(day=rep["date"], pnl=rep["realised_pnl"], summary=rep["text"])
    tl = rep.get("trade_list") or []
    rep["wins"] = sum(1 for t in tl if (t.get("net_pnl") or 0) > 0)
    rep["losses"] = sum(1 for t in tl if (t.get("net_pnl") or 0) < 0)
    return jsonable(rep)


# ====================================================================== settings / secrets / mode
@router.get("/settings")
def get_settings(request: Request):
    rt = rt_of(request)
    data = rt.settings.model_dump(mode="json")
    data["app"]["disclaimer_accepted"] = bool(rt.journal.get_setting("disclaimer_accepted", False))
    data["secrets"] = secrets_status()
    data["can_edit_limits"] = rt.risk.can_edit_limits()
    data["live_confirm_text"] = "I UNDERSTAND THE RISK"
    from indexedge.api.auth import is_local, lan_ip

    if is_local(request.client.host if request.client else None, request.headers):
        port = request.url.port or rt.settings.app.port
        data["phone_url"] = f"http://{lan_ip()}:{port}/?token={request.app.state.access_token}"
        data["lan_url"] = f"http://{lan_ip()}:{port}"
        auth = request.app.state.auth
        if not auth.password_set:
            data["setup_code"] = auth.setup_code          # only ever shown on the PC itself
    return jsonable(data)


LOCKED_SECTIONS = {"risk"}
EDITABLE_SECTIONS = {"risk", "notify", "news", "context", "execution", "broker", "app", "models"}


@router.put("/settings")
def put_settings(request: Request, body: dict = Body(...)):
    from indexedge.config.settings import Settings, _deep_merge

    rt = rt_of(request)
    bad = set(body) - EDITABLE_SECTIONS
    if bad:
        raise HTTPException(400, f"cannot edit: {sorted(bad)}")
    if set(body) & LOCKED_SECTIONS and not rt.risk.can_edit_limits():
        raise HTTPException(409, "Risk limits can only be changed outside market hours (09:00–15:30)")
    if "execution" in body and "mode" in body["execution"]:
        raise HTTPException(400, "Use POST /api/mode to change paper/live mode")
    current = rt.settings.model_dump(mode="json")
    merged = _deep_merge(current, body)
    try:
        new = Settings(**merged)
    except Exception as exc:
        raise HTTPException(400, f"invalid settings: {exc}") from exc
    for section in body:
        old = getattr(rt.settings, section).model_dump(mode="json") if hasattr(getattr(rt.settings, section),
                                                                             "model_dump") else None
        setattr(rt.settings, section, getattr(new, section))
        rt.journal.audit_setting(section, old, getattr(new, section).model_dump(mode="json"))
    rt.settings.save_user()
    rt.apply_settings()
    if "notify" in body:
        rt.telegram.chat_id = rt.settings.notify.telegram_chat_id if rt.settings.notify.telegram_enabled else ""
    return {"ok": True, "restart_required": bool(set(body) & {"app", "broker"})}


@router.post("/secrets")
def post_secret(request: Request, body: dict = Body(...)):
    name, value = body.get("name"), (body.get("value") or "").strip()
    if name not in SECRET_NAMES:
        raise HTTPException(400, "unknown secret")
    if not value:
        raise HTTPException(400, "empty value")
    backend = set_secret(name, value)
    rt = rt_of(request)
    rt.secrets = secrets_status()
    return {"ok": True, "stored_in": backend,
            "message": "Saved. Restart IndexEdge to connect to Angel One." if name.startswith("ANGEL") else "Saved."}


@router.post("/mode")
async def set_mode(request: Request, body: dict = Body(...)):
    res = await rt_of(request).set_mode(str(body.get("mode", "paper")), str(body.get("confirm_text", "")))
    if not res.get("ok"):
        raise HTTPException(400, res.get("error", "mode change refused"))
    return res


@router.post("/disclaimer/accept")
def accept_disclaimer(request: Request):
    rt = rt_of(request)
    rt.journal.set_setting("disclaimer_accepted", True)
    rt.journal.set_setting("disclaimer_accepted_at", datetime.now(IST).isoformat())
    return {"ok": True}


@router.post("/login")
async def login(request: Request):
    res = await rt_of(request).login_now()
    return jsonable(res)


@router.post("/static-ip/check")
async def static_ip(request: Request):
    return jsonable(await rt_of(request).check_ip())


@router.get("/health")
def health(request: Request):
    rt = rt_of(request)
    h = rt.health()
    ws = h.get("ws_status")
    h["ws_status"] = ws.get("tick", "unknown") if isinstance(ws, dict) else str(ws)
    h["jobs"] = [{"name": j.get("id"), **j} for j in h.get("jobs", [])]
    h["order_rate"] = h.get("order_rate") or 0
    return jsonable(h)


@router.get("/calendar/expiries")
def expiries(request: Request, underlying: str = "NIFTY"):
    rt = rt_of(request)
    try:
        return {"expiries": [d.isoformat() for d in rt.master.expiries(_u(underlying), rt.today())[:8]]}
    except Exception:
        return {"expiries": []}


@router.get("/ping")
def ping():
    return {"ok": True, "ts": (datetime.now(IST) + timedelta(0)).isoformat()}


__all__ = ["Query", "router"]


# ====================================================================== Angel One connection wizard
@router.get("/broker/setup")
async def broker_setup(request: Request):
    from indexedge.services.broker_setup import setup_info

    return jsonable(await setup_info(rt_of(request)))


@router.post("/broker/test")
async def broker_test(request: Request):
    from indexedge.services.broker_setup import connection_test

    return jsonable(await connection_test(rt_of(request)))


@router.post("/broker/connect")
async def broker_connect(request: Request):
    """Test the login, then restart the runtime on Angel One real-time data (no app restart needed)."""
    from indexedge.services.broker_setup import connection_test

    rt = rt_of(request)
    res = await connection_test(rt)
    login_ok = any(s["name"].startswith("Login") and s["ok"] for s in res["steps"])
    if not login_ok:
        return jsonable({"ok": False, "message": "Login failed — fix the step marked ✕ and try again",
                         "steps": res["steps"]})
    rt.settings.app.demo_mode = False
    rt.settings.save_user()
    await request.app.state.reload()
    new = rt_of(request)
    ok = bool(new.angel and new.angel.connected)
    return jsonable({"ok": ok, "steps": res["steps"],
                     "message": "Connected — real-time Angel One data is live" if ok
                     else "Login worked in the test but the live connection failed; see Health → log"})


# ====================================================================== agent
@router.get("/agent")
def agent_status(request: Request):
    rt = rt_of(request)
    watching = []
    for sid, strat in rt.strategies.items():
        for u in UNDERLYINGS:
            w = rt.agent.watch.get((sid, u), {})
            watching.append({"strategy_id": sid, "name": strat.name, "underlying": u,
                             "enabled": rt.strategy_enabled(sid, u),
                             "execution_mode": rt.configured_execution_mode(sid).value,
                             "last_eval_ts": w.get("last_eval_ts"),
                             "state": w.get("state") or ("disabled" if not rt.strategy_enabled(sid, u) else "no_setup"),
                             "detail": w.get("detail") or "Waiting for the next 5-minute candle"})
    return jsonable({
        "enabled": rt.agent_enabled,
        "auto_strategies": [sid for sid in rt.strategies
                            if rt.configured_execution_mode(sid) is ExecutionMode.AUTO],
        "mode": rt.settings.execution.mode.value, "data_source": rt.market.data_source,
        "market_open": rt.clock.is_market_open(), "next_bar_in_s": rt.agent.next_bar_in_s(),
        "watching": watching, "log": list(rt.agent.entries)[:300],
    })


@router.post("/agent")
async def agent_switch(request: Request, body: dict = Body(...)):
    return jsonable(await rt_of(request).set_agent(bool(body.get("enabled"))))


@router.post("/agent/auto-all")
async def agent_auto_all(request: Request):
    return jsonable(await rt_of(request).set_all_auto())


# ====================================================================== manual execution (trade on Angel One yourself)
def _signal(rt: Any, sid: str):
    sig = rt.signals.by_id.get(sid)
    if sig is None:
        raise HTTPException(404, "Signal not found (it may be from before the last restart)")
    return sig


@router.get("/signals/{sid}/ticket")
async def ticket(request: Request, sid: str):
    rt = rt_of(request)
    try:
        return jsonable(await rt.manual.ticket(_signal(rt, sid)))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/signals/{sid}/manual-entry")
async def manual_entry(request: Request, sid: str, body: dict = Body(...)):
    rt = rt_of(request)
    try:
        tid = await rt.manual.enter(_signal(rt, sid), float(body.get("fill_price") or 0),
                                    int(body.get("quantity") or 0))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    await rt.bus.publish("signal", rt.signals.to_json(rt.signals.by_id[sid]))
    return {"ok": True, "trade_id": tid}


@router.post("/trades/{tid}/manual-exit")
async def manual_exit(request: Request, tid: str, body: dict = Body(...)):
    rt = rt_of(request)
    px = float(body.get("exit_price") or 0)
    if px <= 0:
        raise HTTPException(400, "Exit price must be positive")
    try:
        await rt.manual.exit(tid, px)
    except KeyError as exc:
        raise HTTPException(404, "trade not found") from exc
    return {"ok": True}


# ====================================================================== free remote access
@router.get("/remote")
def remote_status(request: Request):
    rt = rt_of(request)
    return jsonable(rt.tunnel.info(request.app.state.auth.password_set))


@router.post("/remote")
async def remote_switch(request: Request, body: dict = Body(...)):
    rt = rt_of(request)
    res = await rt.set_remote(bool(body.get("enabled")), request.app.state.auth.password_set)
    if not res.get("ok"):
        raise HTTPException(400, res.get("error", "could not change remote access"))
    return jsonable(res)
