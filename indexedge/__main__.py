"""Command-line entry point: ``python -m indexedge <command>``.

Commands
  run        start the server (default) and open the browser
  setup      interactive first-time setup (secrets -> OS keyring, static IP, capital, Telegram)
  migrate    create/upgrade the local database
  backtest   run a backtest from the terminal: backtest <strategy_id> [NIFTY|BANKNIFTY] [days]
  check      dry-run broker login + data fetch (never places orders)
"""
from __future__ import annotations

import argparse
import getpass
import logging
import sys
import threading
import webbrowser


def _setup() -> None:
    from indexedge.config.secrets import SECRET_NAMES, secrets_status, set_secret
    from indexedge.config.settings import load_settings

    s = load_settings()
    print("IndexEdge setup. Press Enter to keep the current value / skip.\n")
    status = secrets_status()
    prompts = {
        "ANGEL_API_KEY": "SmartAPI API key",
        "ANGEL_CLIENT_CODE": "Angel One client code",
        "ANGEL_PIN": "Angel One login PIN",
        "ANGEL_TOTP_SECRET": "TOTP secret (the text key shown when enabling TOTP on smartapi.angelone.in)",
        "TELEGRAM_BOT_TOKEN": "Telegram bot token (optional, from @BotFather)",
    }
    for name in SECRET_NAMES:
        mark = "configured" if status[name] else "not set"
        val = getpass.getpass(f"{prompts[name]} [{mark}]: ").strip()
        if val:
            print(f"  saved to {set_secret(name, val)}")
    ip = input(f"Static IP registered on SmartAPI [{s.broker.static_ip or 'not set'}]: ").strip()
    if ip:
        s.broker.static_ip = ip
    cap = input(f"Trading capital in rupees [{s.risk.capital:,.0f}]: ").replace(",", "").strip()
    if cap:
        s.risk.capital = float(cap)
    chat = input(f"Telegram chat id (optional) [{s.notify.telegram_chat_id or 'not set'}]: ").strip()
    if chat:
        s.notify.telegram_chat_id = chat
        s.notify.telegram_enabled = True
    if secrets_status()["ANGEL_API_KEY"]:
        s.app.demo_mode = False
    s.save_user()
    print("\nSaved. Paper trading is the default; switch to Live only from Settings after reviewing paper results.")


def _run(args) -> None:
    import uvicorn

    from indexedge.config.settings import load_settings

    s = load_settings()
    logging.basicConfig(level=s.app.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    url = f"http://localhost:{args.port or s.app.port}"
    if not args.no_browser:
        threading.Timer(2.5, lambda: webbrowser.open(url)).start()
    print(f"IndexEdge running at {url}  (to use your phone: Settings → Phone access → scan the QR code)")
    uvicorn.run("indexedge.app:create_app", factory=True, host=args.host or s.app.host,
                port=args.port or s.app.port, log_level=s.app.log_level.lower())


def _migrate() -> None:
    from indexedge.config.settings import load_settings
    from indexedge.journal.db import Journal

    s = load_settings()
    s.app.data_dir.mkdir(parents=True, exist_ok=True)
    Journal(s.app.data_dir / "indexedge.duckdb").close()
    print("Database is up to date.")


def _backtest(args) -> None:
    import json

    from indexedge.services.lab import StrategyLab

    lab = StrategyLab.default()
    res = lab.backtest(args.strategy, args.underlying, args.days)
    print(json.dumps({"metrics": res["metrics"], "report": res["report"]}, indent=2, default=str))


def _check() -> None:
    import asyncio

    from indexedge.services.diagnostics import broker_dry_run

    print(asyncio.run(broker_dry_run()))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="indexedge")
    sub = p.add_subparsers(dest="cmd")
    r = sub.add_parser("run")
    r.add_argument("--host")
    r.add_argument("--port", type=int)
    r.add_argument("--no-browser", action="store_true")
    sub.add_parser("setup")
    sub.add_parser("migrate")
    sub.add_parser("check")
    b = sub.add_parser("backtest")
    b.add_argument("strategy")
    b.add_argument("underlying", nargs="?", default="NIFTY")
    b.add_argument("days", nargs="?", type=int, default=250)
    args = p.parse_args(argv)
    cmd = args.cmd or "run"
    if cmd == "run":
        if args.cmd is None:
            args = r.parse_args([])
        _run(args)
    elif cmd == "setup":
        _setup()
    elif cmd == "migrate":
        _migrate()
    elif cmd == "backtest":
        _backtest(args)
    elif cmd == "check":
        _check()


if __name__ == "__main__":
    main(sys.argv[1:])
