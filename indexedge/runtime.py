"""Application runtime: builds every service, wires them through the event bus and runs the daily
schedule. One instance per process; the FastAPI app holds it as ``app.state.rt``."""
from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from indexedge.broker.paper import PaperBroker
from indexedge.broker.rate_limiter import RateLimiter
from indexedge.broker.static_ip import check_static_ip
from indexedge.config.secrets import get_secret, secrets_status
from indexedge.config.settings import Settings, load_settings
from indexedge.core import bus as topics
from indexedge.core.bus import EventBus
from indexedge.core.clock import IST, Clock, parse_hhmm
from indexedge.core.types import ExecutionMode, Mode, Signal
from indexedge.data.instruments import InstrumentMaster
from indexedge.data.store import CandleStore
from indexedge.data.synthetic import synthetic_master_rows
from indexedge.journal.db import Journal
from indexedge.risk.engine import RiskEngine

log = logging.getLogger(__name__)

LIVE_CONFIRM_TEXT = "I UNDERSTAND THE RISK"


class LogBuffer(logging.Handler):
    """Keeps the last N log lines for the Health page."""

    def __init__(self, n: int = 200) -> None:
        super().__init__(level=logging.INFO)
        self.lines: deque[str] = deque(maxlen=n)
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            from indexedge.config.secrets import redact

            self.lines.append(redact(self.format(record)))
        except Exception:  # noqa: BLE001
            pass


class Runtime:
    def __init__(self, settings: Settings | None = None, *, start_background: bool = True) -> None:
        self.settings = settings or load_settings()
        s = self.settings
        self.data_dir = Path(s.app.data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        holidays = {date.fromisoformat(h) for h in s.holidays}
        self.clock = Clock(holidays)
        self.bus = EventBus()
        self.logbuf = LogBuffer()
        logging.getLogger().addHandler(self.logbuf)
        self.journal = Journal(self.data_dir / "indexedge.duckdb")
        self.store = CandleStore(self.data_dir / "candles")
        self.start_background = start_background
        self.secrets = secrets_status()
        self.broker_configured = all(self.secrets[k] for k in
                                     ("ANGEL_API_KEY", "ANGEL_CLIENT_CODE", "ANGEL_PIN", "ANGEL_TOTP_SECRET"))
        self.demo = bool(s.app.demo_mode or not self.broker_configured)
        self.static_ip: dict[str, Any] = {"ok": None, "public_ip": None, "expected": s.broker.static_ip,
                                          "message": "not checked yet"}
        self.reports: dict[str, dict[str, dict]] = {}
        self.report_meta: dict[str, Any] = {}
        self.broker_errors = 0
        self.jobs: dict[str, dict[str, Any]] = {}
        self._tasks: list[asyncio.Task] = []
        self.scheduler = None
        self.angel = None
        self.limiter = RateLimiter(s.execution.max_orders_per_second)

    # ================================================================== construction
    async def start(self) -> None:
        s = self.settings
        self.master = await self._load_master()
        if not self.demo:
            from indexedge.broker.angel_one import AngelOneBroker

            self.angel = AngelOneBroker(s, get_secret, self.clock, master=self.master,
                                        dry_run=s.execution.mode is not Mode.LIVE,
                                        order_limiter=self.limiter)
            try:
                await self.angel.connect()
            except Exception as exc:  # noqa: BLE001
                log.error("Angel One login failed: %s — continuing with demo data", exc)
                self.broker_errors += 1
        data_broker = self.angel if (self.angel is not None and self.angel.connected) else None

        from indexedge.services.market import MarketData

        self.market = MarketData(s, self.clock, self.bus, self.store, self.master, data_broker,
                                 demo=data_broker is None)
        await self.market.load_history()
        if self.market.demo:
            # demo instrument master centred on the (synthetic) current prices so ATM strikes exist
            spots = {u: self.market.prev_close[u] for u in ("NIFTY", "BANKNIFTY") if u in self.market.prev_close}
            self.master = InstrumentMaster.from_json(
                synthetic_master_rows(self.clock.today(), holidays=self.clock.holidays, spots=spots))
            self.market.master = self.master

        # brokers: paper always available; live only when explicitly enabled
        self.paper = PaperBroker(self.clock, s.risk.capital, s.execution.paper_slippage_ticks)
        await self.paper.connect()
        self.market.add_tick_sink(self.paper.feed_tick)

        self._build_analytics()
        self._build_execution()
        self._wire_bus()
        self._load_reports_cache()
        for u in ("NIFTY", "BANKNIFTY"):
            try:
                await asyncio.to_thread(self.signals.compute_features, u)
            except Exception:
                log.exception("initial feature build failed for %s", u)
        if self.start_background:
            await self.market.start()
            self._tasks.append(asyncio.create_task(self.context.run()))
            self._tasks.append(asyncio.create_task(self.news.run()))
            self._tasks.append(asyncio.create_task(self._timer_loop()))
            if self.telegram.configured:
                self._tasks.append(asyncio.create_task(self.telegram.poll_callbacks(self._on_telegram)))
            self._start_scheduler()
            self._tasks.append(asyncio.create_task(self._initial_jobs()))
        log.info("IndexEdge started (demo=%s, mode=%s, data=%s)", self.demo, s.execution.mode.value,
                 self.market.data_source)

    async def _load_master(self) -> InstrumentMaster:
        path = self.data_dir / "OpenAPIScripMaster.json"
        if not self.demo:
            try:
                fresh = path.exists() and datetime.fromtimestamp(path.stat().st_mtime).date() == date.today()
                if not fresh:
                    await asyncio.to_thread(InstrumentMaster.download, self.settings.broker.instrument_master_url,
                                            path)
                return await asyncio.to_thread(InstrumentMaster.load, path)
            except Exception as exc:  # noqa: BLE001
                log.error("instrument master download failed: %s", exc)
                if path.exists():
                    return await asyncio.to_thread(InstrumentMaster.load, path)
        return InstrumentMaster.from_json(synthetic_master_rows(self.clock.today(), holidays=self.clock.holidays))

    def _build_analytics(self) -> None:
        from indexedge.context.engine import ContextEngine
        from indexedge.context.providers import GiftNiftyProvider, SyntheticProvider, YFinanceProvider
        from indexedge.models.registry import ModelRegistry
        from indexedge.news.calendar import EventCalendar
        from indexedge.news.service import NewsService
        from indexedge.notify.telegram import TelegramNotifier
        from indexedge.services.lab import StrategyLab
        from indexedge.signals.composer import SignalComposer
        from indexedge.strategies.registry import all_strategies

        s = self.settings
        self.calendar = EventCalendar(s, self.clock)
        try:
            self.calendar.set_expiries({u: self.master.expiries(u, self.clock.today()) for u in ("NIFTY", "BANKNIFTY")})
        except Exception:  # noqa: BLE001
            log.warning("could not load expiries into calendar")
        if self.market.demo:
            synth = SyntheticProvider(seed=int(self.clock.today().strftime("%Y%m%d")))
            if "NIFTY" in self.market.prev_close:
                synth.anchor(s.context.inputs["gift_nifty"].symbol if "gift_nifty" in s.context.inputs
                             else "GIFTNIFTY", self.market.prev_close["NIFTY"])
            providers = [synth]
        else:
            providers = [YFinanceProvider()]
            gift_url = getattr(s.context, "gift_nifty_url", "")
            if gift_url:
                providers.append(GiftNiftyProvider(gift_url))
        self.context = ContextEngine(providers, s, self.bus, self.clock, event_risk_fn=self.calendar.event_risk,
                                     prev_close_fn=lambda: self.market.prev_close.get("NIFTY"))
        self.news = NewsService(s, self.bus, self.clock, store_cb=self.journal.record_news)
        self.telegram = TelegramNotifier(lambda: get_secret("TELEGRAM_BOT_TOKEN"),
                                         s.notify.telegram_chat_id if s.notify.telegram_enabled else "")
        self.registry = ModelRegistry(self.data_dir / "models")
        self.risk = RiskEngine(s.risk, self.clock, self.journal, cutoff=s.broker.intraday_cutoff)
        self.strategies = all_strategies(s.strategies.params)
        self._apply_expiry_dates()
        self.lab = StrategyLab(s, store=self.store, lot_sizes=self._lot_sizes())
        self.composer = SignalComposer(self._effective_settings(), self.registry, self.risk, self.calendar,
                                       self.master, strategies=self.strategies)

        from indexedge.services.signal_engine import SignalEngine

        self.signals = SignalEngine(self)

    def _lot_sizes(self) -> dict[str, int]:
        out = {}
        for u in ("NIFTY", "BANKNIFTY"):
            try:
                out[u] = int(self.master.lot_size(u, self.clock.today()))
            except Exception:  # noqa: BLE001
                pass
        return out

    def _apply_expiry_dates(self) -> None:
        strat = self.strategies.get("expiry_straddle")
        if strat is None or "expiry_dates" not in strat.params:
            return
        try:
            strat.params["expiry_dates"] = set(self.master.expiries("NIFTY")) | set(
                self.master.expiries("NIFTY", None))
        except Exception:  # noqa: BLE001
            pass

    def _build_execution(self) -> None:
        from indexedge.execution.confirm import PendingConfirmations
        from indexedge.execution.orders import OrderManager
        from indexedge.execution.trades import TradeManager

        s = self.settings
        live = s.execution.mode is Mode.LIVE and self.angel is not None and self.angel.connected
        self.exec_broker = self.angel if live else self.paper
        if live:
            self.angel.dry_run = False
        self.orders = OrderManager(self.exec_broker, self.limiter, s, self.journal, bus=self.bus)
        self.trades = TradeManager(self.orders, self.exec_broker, self.journal, self.risk, self.clock, self.bus, s,
                                   calendar=self.calendar)
        self.market.add_tick_sink(self.trades.on_tick)
        self.confirmations = PendingConfirmations(self.clock, journal=self.journal)

    def _wire_bus(self) -> None:
        self.bus.subscribe(topics.CANDLE, self.signals.on_candle)
        self.bus.subscribe(topics.ALERT, self._on_alert)
        self.bus.subscribe(topics.NEWS, self._on_news)

    # ================================================================== settings helpers
    def _effective_settings(self) -> Settings:
        """Settings with the *effective* enabled map: the user's choice, else 'validated on real data'."""
        eff = self.settings.model_copy(deep=True)
        enabled: dict[str, bool] = {}
        nifty = self.reports.get("NIFTY", {})
        for sid in getattr(self, "strategies", {}):
            if sid in self.settings.strategies.enabled:
                enabled[sid] = bool(self.settings.strategies.enabled[sid])
            else:
                rep = nifty.get(sid) or {}
                enabled[sid] = rep.get("status") == "validated" and rep.get("data_source") != "synthetic"
        eff.strategies.enabled = enabled
        return eff

    def refresh_composer_settings(self) -> None:
        self.composer.settings = self._effective_settings()

    def strategy_enabled(self, sid: str) -> bool:
        return bool(self._effective_settings().strategies.enabled.get(sid))

    def execution_mode(self, sid: str) -> ExecutionMode:
        m = self.settings.strategies.execution_mode.get(sid)
        return ExecutionMode(m) if m else self.settings.execution.default_execution_mode

    # ================================================================== reports (strategy validation)
    def _reports_path(self) -> Path:
        return self.data_dir / "strategy_reports.json"

    def _load_reports_cache(self) -> None:
        p = self._reports_path()
        if p.exists():
            try:
                blob = json.loads(p.read_text())
                self.reports = blob.get("reports", {})
                self.report_meta = blob.get("meta", {})
                self.refresh_composer_settings()
            except Exception:  # noqa: BLE001
                log.warning("could not read cached strategy reports")

    async def refresh_reports(self, days: int = 250) -> None:
        log.info("Validating strategies on %s days of history (runs in background)…", days)
        self.jobs["reports"] = {"status": "running", "started": self.clock.now().isoformat()}
        try:
            reports = {}
            for u in ("NIFTY", "BANKNIFTY"):
                reports[u] = await asyncio.to_thread(self.lab.report_all, u, days)
            self.reports = reports
            self.report_meta = {"ts": self.clock.now().isoformat(), "days": days,
                                "data_source": self.lab.data_source("NIFTY", days)}
            self._reports_path().write_text(json.dumps({"reports": reports, "meta": self.report_meta},
                                                       default=str))
            self.refresh_composer_settings()
            self.jobs["reports"] = {"status": "ok", "finished": self.clock.now().isoformat()}
        except Exception as exc:
            log.exception("strategy validation failed")
            self.jobs["reports"] = {"status": "error", "error": str(exc)}

    async def retrain_models(self) -> None:
        from indexedge.services.training import train_all

        self.jobs["retrain"] = {"status": "running", "started": self.clock.now().isoformat()}
        try:
            data = {u: self.lab.candles(250, (u,))[u] for u in ("NIFTY", "BANKNIFTY")}
            res = await asyncio.to_thread(train_all, data, self.strategies, self.settings, self.registry)
            self.jobs["retrain"] = {"status": "ok", "finished": self.clock.now().isoformat(),
                                    "models": {k: (v.get("n") if isinstance(v, dict) else None)
                                               for k, v in (res or {}).items()}}
        except Exception as exc:
            log.exception("model retrain failed")
            self.jobs["retrain"] = {"status": "error", "error": str(exc)}

    # ================================================================== execution
    async def execute_signal(self, sig: Signal) -> dict:
        if sig.instrument is not None:
            await self.market.subscribe_instrument(sig.instrument)
            if self.market.demo and sig.instrument.token not in self.paper._ticks:
                from indexedge.core.types import Tick

                await self.paper.feed_tick(Tick(sig.instrument.token, self.clock.now(),
                                                self.market.demo_price(sig.instrument)))
        try:
            res = await self.trades.execute(sig)
        except Exception as exc:
            log.exception("execution failed")
            await self.bus.publish(topics.ALERT, {"level": "error", "title": "Execution failed",
                                                  "message": str(exc)})
            return {"ok": False, "error": str(exc)}
        ok = bool(res.ok)
        sig.status = "taken" if ok else "blocked"
        if not ok:
            sig.blocked_reason = res.reason or sig.blocked_reason
        await self.bus.publish(topics.SIGNAL, self.signals.to_json(sig))
        return {"ok": ok, "reason": res.reason, "trade_id": res.trade_id}

    async def confirm_signal(self, sid: str) -> dict:
        sig = self.confirmations.confirm(sid)
        if sig is None:
            return {"ok": False, "error": "Signal not pending (expired or already handled)"}
        return await self.execute_signal(sig)

    async def skip_signal(self, sid: str) -> dict:
        ok = self.confirmations.skip(sid)
        sig = self.signals.by_id.get(sid)
        if sig is not None:
            sig.status = "skipped"
            if not ok:
                self.journal.update_signal_status(sid, "skipped", "user skipped")
            await self.bus.publish(topics.SIGNAL, self.signals.to_json(sig))
        return {"ok": True}

    async def kill_switch(self) -> dict:
        res = await self.trades.kill_switch("Kill switch activated by user")
        await self.notify(f"🛑 KILL SWITCH activated. {res}")
        return {"ok": True, **(res if isinstance(res, dict) else {})}

    async def set_mode(self, mode: str, confirm_text: str = "") -> dict:
        target = Mode(mode)
        if target is Mode.LIVE:
            if confirm_text.strip() != LIVE_CONFIRM_TEXT:
                return {"ok": False, "error": f'Type exactly "{LIVE_CONFIRM_TEXT}" to enable live trading'}
            if self.angel is None or not self.angel.connected:
                return {"ok": False, "error": "Angel One is not connected — log in first"}
            if self.static_ip.get("ok") is not True:
                return {"ok": False, "error": f"Static IP check not passed: {self.static_ip.get('message')}"}
            if self.risk.locked:
                return {"ok": False, "error": f"Trading locked today: {self.risk.lock_reason}"}
            if self.trades.open_trades:
                return {"ok": False, "error": "Close open paper trades before switching to live"}
        elif self.trades.open_trades:
            return {"ok": False, "error": "Close open trades before switching mode"}
        old_mode = self.settings.execution.mode.value
        self.settings.execution.mode = target
        self.settings.save_user()
        self.journal.audit_setting("execution.mode", old_mode, target.value, source="ui")
        self._build_execution()
        await self.notify(f"Mode switched to {target.value.upper()}")
        return {"ok": True, "mode": target.value}

    # ================================================================== notifications
    async def notify(self, text: str, buttons: list[tuple[str, str]] | None = None) -> None:
        if self.telegram.configured:
            try:
                await self.telegram.send(text, buttons)
            except Exception:  # noqa: BLE001
                log.warning("telegram send failed")

    async def notify_signal(self, sig: Signal, payload: dict, confirm: bool) -> None:
        if not self.settings.notify.notify_signals:
            return
        p = payload.get("probability")
        prob = f"{p * 100:.0f}% (n={payload.get('sample_size')})" if p is not None else "insufficient data"
        text = (f"📈 {payload.get('strategy_name')} — {sig.raw.side.value} {sig.raw.underlying}\n"
                f"Entry {sig.raw.entry:,.1f}  SL {sig.raw.stop:,.1f}  T {', '.join(f'{t:,.1f}' for t in sig.raw.targets)}\n"
                f"Probability {prob} · R:R {sig.expected_rr:.2f} · EV ₹{sig.ev_per_trade:,.0f}\n"
                + "\n".join(f"• {r}" for r in sig.reasons[:4]))
        buttons = [("✅ Confirm", f"confirm:{sig.id}"), ("❌ Skip", f"skip:{sig.id}")] if confirm else None
        await self.notify(text, buttons)

    async def _on_telegram(self, data: str) -> None:
        action, _, sid = data.partition(":")
        if action == "confirm":
            res = await self.confirm_signal(sid)
            await self.notify(f"Confirm {sid}: {'executed' if res.get('ok') else res.get('error') or res}")
        elif action == "skip":
            await self.skip_signal(sid)

    async def _on_alert(self, payload: dict) -> None:
        await self.notify(f"⚠️ {payload.get('title')}\n{payload.get('message') or payload.get('body') or ''}")

    async def _on_news(self, item: Any) -> None:
        if getattr(item, "impact", "") == "high" and self.settings.notify.notify_high_impact_news:
            await self.notify(f"📰 HIGH IMPACT: {item.title}\n{item.url}")

    # ================================================================== scheduler
    def _start_scheduler(self) -> None:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        s = self.settings
        sch = AsyncIOScheduler(timezone=IST)
        lt = parse_hhmm(s.broker.login_time)
        sch.add_job(self.daily_login, "cron", day_of_week="mon-fri", hour=lt.hour, minute=lt.minute,
                    id="daily_login", misfire_grace_time=1800)
        sch.add_job(self.premarket_job, "cron", day_of_week="mon-fri", hour=8, minute=50, id="premarket",
                    misfire_grace_time=1800)
        sch.add_job(self.eod_job, "cron", day_of_week="mon-fri", hour=15, minute=45, id="eod",
                    misfire_grace_time=3600)
        sch.add_job(self.check_ip, "interval", hours=1, id="static_ip")
        sch.add_job(self.weekly_job, "cron", day_of_week="sat", hour=10, minute=0, id="weekly",
                    misfire_grace_time=6 * 3600)
        sch.start()
        self.scheduler = sch

    async def _initial_jobs(self) -> None:
        await self.check_ip()
        await self.context.refresh()
        if not self.reports:
            await self.refresh_reports()
        if self.trades.open_trades or not self.market.demo:
            try:
                await self.trades.reconcile(self.master)
            except Exception:
                log.exception("startup reconcile failed")

    async def _timer_loop(self) -> None:
        while True:
            await asyncio.sleep(2.0)
            try:
                await self.market.flush()
                await self.trades.on_timer()
                for sig in self.confirmations.expire(self.clock.now()):
                    await self.bus.publish(topics.SIGNAL, self.signals.to_json(sig))
                age = self.market.feed_age_s()
                if (self.clock.is_market_open() and age is not None and age > 60 and self.trades.open_trades
                        and not self.trades.safe_mode):
                    await self.trades.enter_safe_mode(f"Market data feed stale for {age:.0f}s")
                elif self.trades.safe_mode and age is not None and age < 5:
                    self.trades.exit_safe_mode()
                await self.bus.publish(topics.HEALTH, self.health(brief=True))
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("timer loop error")

    async def daily_login(self) -> None:
        if self.angel is None:
            self.jobs["daily_login"] = {"status": "skipped", "reason": "broker not configured (demo mode)"}
            return
        try:
            await self.angel.connect()
            await asyncio.to_thread(InstrumentMaster.download, self.settings.broker.instrument_master_url,
                                    self.data_dir / "OpenAPIScripMaster.json")
            self.master = await asyncio.to_thread(InstrumentMaster.load, self.data_dir / "OpenAPIScripMaster.json")
            self.jobs["daily_login"] = {"status": "ok", "at": self.clock.now().isoformat()}
        except Exception as exc:  # noqa: BLE001
            self.broker_errors += 1
            self.jobs["daily_login"] = {"status": "error", "error": str(exc)}
            await self.bus.publish(topics.ALERT, {"level": "error", "title": "Daily login failed — use 'Login for today'",
                                                  "message": str(exc)})
        await self.check_ip()

    async def login_now(self) -> dict:
        if not self.broker_configured:
            return {"ok": False, "error": "Enter your SmartAPI key, client code, PIN and TOTP secret in Settings first"}
        if self.angel is None:
            return {"ok": False, "error": "Restart IndexEdge to switch from demo data to Angel One"}
        await self.daily_login()
        return {"ok": self.jobs.get("daily_login", {}).get("status") == "ok", **self.jobs.get("daily_login", {})}

    async def check_ip(self) -> dict:
        exp = self.settings.broker.static_ip
        if not exp:
            self.static_ip = {"ok": None, "public_ip": None, "expected": "",
                              "message": "No static IP configured (needed only for live orders)"}
        else:
            try:
                self.static_ip = await check_static_ip(exp, self.settings.broker.ip_check_url)
            except Exception as exc:  # noqa: BLE001
                self.static_ip = {"ok": False, "public_ip": None, "expected": exp, "message": str(exc)}
        if self.angel is not None:
            block = None if self.static_ip.get("ok") else f"static IP check failed: {self.static_ip.get('message')}"
            if self.settings.execution.mode is Mode.LIVE:
                self.angel.block_orders(block)
        return self.static_ip

    async def premarket_job(self) -> None:
        from indexedge.services.reports import premarket_report

        rep = premarket_report(self)
        self.jobs["premarket"] = {"status": "ok", "at": self.clock.now().isoformat()}
        await self.notify(rep.get("text", "Pre-market checklist ready"))

    async def eod_job(self) -> None:
        from indexedge.services.reports import eod_report

        rep = eod_report(self, self.clock.today())
        try:
            self.journal.record_equity(self.clock.today(), self.settings.risk.capital + rep["realised_pnl"],
                                       rep["realised_pnl"], rep["trades"])
        except Exception:
            log.exception("record equity failed")
        self.jobs["eod"] = {"status": "ok", "at": self.clock.now().isoformat()}
        if self.settings.notify.notify_eod:
            await self.notify(rep.get("text", "EOD report ready"))

    async def weekly_job(self) -> None:
        await self.refresh_reports()
        await self.retrain_models()

    # ================================================================== status / health
    def status(self) -> dict:
        s = self.settings
        positions = []
        try:
            positions = list(self.paper._positions.values()) if self.exec_broker is self.paper else []
        except Exception:  # noqa: BLE001
            positions = []
        age = self.market.feed_age_s()
        return {
            "mode": s.execution.mode.value,
            "demo_mode": self.market.demo,
            "data_source": self.market.data_source,
            "market_open": self.clock.is_market_open(),
            "broker": {"connected": bool(self.angel and self.angel.connected), "name": "angel_one" if self.angel else "demo",
                       "logged_in": bool(self.angel and getattr(self.angel, "logged_in", False)),
                       "configured": self.broker_configured},
            "static_ip": self.static_ip,
            "feed": {"last_tick_age_s": age, "ws": (self.angel.ws_status.get("tick") if self.angel else
                                                   ("demo" if self.clock.is_market_open() else "idle"))},
            "risk": self.risk.status(positions),
            "safe_mode": {"active": self.trades.safe_mode, "reason": self.trades.safe_mode_reason},
            "disclaimer_accepted": bool(self.journal.get_setting("disclaimer_accepted", False)),
            "server_time": self.clock.now().isoformat(),
        }

    def health(self, brief: bool = False) -> dict:
        out = {
            "ws_status": (str(self.angel.ws_status.get("tick", "unknown")) if self.angel else
                          ("demo" if self.clock.is_market_open() else "idle")),
            "ws_detail": (dict(self.angel.ws_status) if self.angel else {}),
            "last_tick_age_s": self.market.feed_age_s(),
            "latency_ms": None,
            "broker_errors": self.broker_errors + (getattr(self.angel, "error_count", 0) if self.angel else 0),
            "order_rate": self.limiter.recent_count() if hasattr(self.limiter, "recent_count") else None,
            "safe_mode": self.trades.safe_mode,
            "context_usable": bool(self.context.latest and self.context.latest.usable),
        }
        if brief:
            return out
        jobs = []
        if self.scheduler is not None:
            for j in self.scheduler.get_jobs():
                jobs.append({"id": j.id, "next_run": j.next_run_time.isoformat() if j.next_run_time else None,
                             **self.jobs.get(j.id, {})})
        for k, v in self.jobs.items():
            if not any(j["id"] == k for j in jobs):
                jobs.append({"id": k, **v})
        out["jobs"] = jobs
        out["news_sources"] = self.news.source_health()
        out["log_tail"] = list(self.logbuf.lines)[-80:]
        return out

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        if self.scheduler is not None:
            self.scheduler.shutdown(wait=False)
        for svc in (self.context, self.news, self.telegram):
            try:
                svc.stop()
            except Exception:  # noqa: BLE001
                pass
        await self.market.stop()
        try:
            await self.news.aclose()
            await self.telegram.aclose()
        except Exception:  # noqa: BLE001
            pass
        if self.angel is not None:
            try:
                await self.angel.disconnect()
            except Exception:  # noqa: BLE001
                pass
        self.journal.close()
        logging.getLogger().removeHandler(self.logbuf)

    # convenience for timestamps in API
    def today(self) -> date:
        return self.clock.today()

    def now(self) -> datetime:
        return self.clock.now()

    def days_back(self, n: int) -> date:
        return self.clock.today() - timedelta(days=n)
