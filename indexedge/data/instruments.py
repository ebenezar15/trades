"""Angel One instrument master (OpenAPIScripMaster.json) parsing and lookups.

Format quirks handled here (see docs/SMARTAPI_NOTES.md):
* ``strike`` is x100 ("2500000.000000" -> 25000.0); futures/indices carry -1 or 0.
* ``tick_size`` is in paise ("5.000000" -> Rs 0.05); non-positive values fall back to 0.05.
* ``expiry`` is ``DDMMMYYYY`` (e.g. ``28OCT2026``); other common formats are accepted defensively.
* Lot sizes, expiries and strike steps are always derived from the master, never hard-coded.
"""
from __future__ import annotations

import json
import logging
import os
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

import httpx

from indexedge.core.types import Instrument, InstrumentKind, OptionType

log = logging.getLogger(__name__)

DEFAULT_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
DEFAULT_INDEX_TOKENS = {"NIFTY": "99926000", "BANKNIFTY": "99926009", "INDIAVIX": "99926017"}
_INDEX_ALIASES = {
    "NIFTY": {"NIFTY", "NIFTY50"},
    "BANKNIFTY": {"BANKNIFTY", "NIFTYBANK"},
    "INDIAVIX": {"INDIAVIX", "VIX"},
}
_KINDS = {"AMXIDX": InstrumentKind.INDEX, "FUTIDX": InstrumentKind.FUTURE, "OPTIDX": InstrumentKind.OPTION}
_EXPIRY_FORMATS = ("%d%b%Y", "%d%b%y", "%Y-%m-%d", "%d-%b-%Y", "%d-%m-%Y", "%d%m%Y")


def parse_expiry(value: str | None) -> date | None:
    """Parse a master expiry string; returns None when empty / unparseable."""
    if not value:
        return None
    s = str(value).strip()
    for fmt in _EXPIRY_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    log.debug("unparseable expiry %r", value)
    return None


def _norm(name: str) -> str:
    return "".join(ch for ch in str(name).upper() if ch.isalnum())


def _float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def parse_row(row: dict) -> Instrument | None:
    """Convert one master row to an :class:`Instrument` (None for unsupported instrument types)."""
    kind = _KINDS.get(str(row.get("instrumenttype", "")).upper())
    if kind is None:
        return None
    symbol = str(row.get("symbol", ""))
    tick = _float(row.get("tick_size")) / 100.0
    lot = int(_float(row.get("lotsize"), 1.0)) or 1
    strike = opt = None
    if kind is InstrumentKind.OPTION:
        strike = round(_float(row.get("strike")) / 100.0, 2)
        suffix = symbol[-2:].upper()
        if suffix not in ("CE", "PE"):
            return None
        opt = OptionType(suffix)
    expiry = parse_expiry(row.get("expiry")) if kind is not InstrumentKind.INDEX else None
    if kind is not InstrumentKind.INDEX and expiry is None:
        return None
    return Instrument(
        token=str(row.get("token", "")),
        symbol=symbol,
        name=str(row.get("name", "")).strip().upper(),
        exchange=str(row.get("exch_seg", "")).upper(),
        kind=kind,
        lot_size=lot if kind is not InstrumentKind.INDEX else 1,
        tick_size=tick if tick > 0 else 0.05,
        expiry=expiry,
        strike=strike,
        option_type=opt,
    )


class InstrumentMaster:
    """In-memory index of index / index-future / index-option instruments."""

    def __init__(self, instruments: Iterable[Instrument]) -> None:
        self._by_token: dict[str, Instrument] = {}
        self._by_symbol: dict[str, Instrument] = {}
        self._indices: list[Instrument] = []
        self._futures: dict[str, list[Instrument]] = defaultdict(list)
        self._options: dict[str, dict[date, dict[tuple[float, OptionType], Instrument]]] = defaultdict(
            lambda: defaultdict(dict)
        )
        for inst in instruments:
            self._by_token[inst.token] = inst
            self._by_symbol[inst.symbol.upper()] = inst
            if inst.kind is InstrumentKind.INDEX:
                self._indices.append(inst)
            elif inst.kind is InstrumentKind.FUTURE:
                self._futures[inst.name].append(inst)
            elif inst.kind is InstrumentKind.OPTION and inst.expiry and inst.option_type:
                self._options[inst.name][inst.expiry][(float(inst.strike or 0.0), inst.option_type)] = inst
        for lst in self._futures.values():
            lst.sort(key=lambda i: i.expiry or date.max)

    # ------------------------------------------------------------------ construction
    @classmethod
    def from_json(cls, rows: list[dict]) -> "InstrumentMaster":
        insts = [i for i in (parse_row(r) for r in rows) if i is not None]
        return cls(insts)

    @classmethod
    def load(cls, path: str | Path) -> "InstrumentMaster":
        with open(path, encoding="utf-8") as fh:
            return cls.from_json(json.load(fh))

    @staticmethod
    def download(url: str = DEFAULT_URL, path: str | Path = "OpenAPIScripMaster.json",
                 client: httpx.Client | None = None, timeout: float = 120.0) -> Path:
        return download(url, path, client=client, timeout=timeout)

    def __len__(self) -> int:
        return len(self._by_token)

    # ------------------------------------------------------------------ lookups
    def by_token(self, token: str) -> Instrument | None:
        return self._by_token.get(str(token))

    def by_symbol(self, symbol: str) -> Instrument | None:
        return self._by_symbol.get(symbol.upper())

    def index(self, name: str) -> Instrument:
        key = _norm(name)
        aliases = next((a for a in _INDEX_ALIASES.values() if key in a), {key})
        matches = [i for i in self._indices if _norm(i.name) in aliases or _norm(i.symbol) in aliases]
        if matches:
            matches.sort(key=lambda i: i.exchange != "NSE")
            return matches[0]
        canonical = next((k for k, a in _INDEX_ALIASES.items() if key in a), key)
        token = DEFAULT_INDEX_TOKENS.get(canonical)
        if token is None:
            raise KeyError(f"index {name!r} not in instrument master")
        return self._by_token.get(token) or Instrument(token, canonical, canonical, "NSE", InstrumentKind.INDEX)

    def futures(self, name: str) -> list[Instrument]:
        return list(self._futures.get(name.upper(), []))

    def nearest_future(self, name: str, on: date) -> Instrument | None:
        return next((f for f in self.futures(name) if f.expiry and f.expiry >= on), None)

    def expiries(self, name: str, on: date | None = None) -> list[date]:
        exps = sorted(self._options.get(name.upper(), {}).keys())
        return [e for e in exps if on is None or e >= on]

    def option(self, name: str, expiry: date, strike: float, opt: OptionType | str) -> Instrument:
        ot = opt if isinstance(opt, OptionType) else OptionType(str(opt).upper())
        try:
            return self._options[name.upper()][expiry][(round(float(strike), 2), ot)]
        except KeyError as exc:
            raise KeyError(f"no option {name} {expiry} {strike} {ot.value}") from exc

    def chain(self, name: str, expiry: date) -> list[Instrument]:
        contracts = self._options.get(name.upper(), {}).get(expiry, {})
        return [contracts[k] for k in sorted(contracts, key=lambda k: (k[0], k[1].value))]

    def strikes(self, name: str, expiry: date) -> list[float]:
        return sorted({k[0] for k in self._options.get(name.upper(), {}).get(expiry, {})})

    def atm_strike(self, name: str, spot: float, expiry: date) -> float:
        strikes = self.strikes(name, expiry)
        if not strikes:
            raise KeyError(f"no strikes for {name} {expiry}")
        return min(strikes, key=lambda k: (abs(k - spot), k))

    def strike_step(self, name: str, expiry: date) -> float:
        """Most common gap between listed strikes (ties -> smallest)."""
        strikes = self.strikes(name, expiry)
        diffs = [round(b - a, 2) for a, b in zip(strikes, strikes[1:]) if b > a]
        if not diffs:
            raise KeyError(f"cannot derive strike step for {name} {expiry}")
        counts = Counter(diffs)
        best = max(counts.values())
        return min(d for d, c in counts.items() if c == best)

    def lot_size(self, name: str, on: date | None = None) -> int:
        name = name.upper()
        derivs = list(self._futures.get(name, []))
        for exp_map in self._options.get(name, {}).values():
            derivs.extend(list(exp_map.values())[:1])
        if not derivs:
            raise KeyError(f"no derivatives for {name} in instrument master")
        live = [d for d in derivs if on is None or (d.expiry and d.expiry >= on)] or derivs
        return min(live, key=lambda d: d.expiry or date.max).lot_size


def download(url: str = DEFAULT_URL, path: str | Path = "OpenAPIScripMaster.json",
             client: httpx.Client | None = None, timeout: float = 120.0) -> Path:
    """Download the master to ``path`` atomically (temp file + rename). Raises on HTTP errors."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    own = client is None
    cl = client or httpx.Client(timeout=timeout, follow_redirects=True)
    try:
        with cl.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(tmp, "wb") as fh:
                for chunk in resp.iter_bytes():
                    fh.write(chunk)
        json.loads(tmp.read_text(encoding="utf-8"))  # validate before replacing a good copy
        os.replace(tmp, path)
    finally:
        if own:
            cl.close()
        if tmp.exists():
            tmp.unlink()
    return path
