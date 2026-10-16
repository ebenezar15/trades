"""Typed application settings.

Load order (later wins): built-in defaults -> ``config/default.yaml`` -> ``<data_dir>/settings.yaml``
(written by the UI) -> environment variables prefixed ``INDEXEDGE_`` (nested with ``__``).
Secrets are NOT stored here; see :mod:`indexedge.config.secrets`.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from indexedge.core.types import ExecutionMode, Mode

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_YAML = ROOT / "config" / "default.yaml"


class AppSettings(BaseModel):
    data_dir: Path = ROOT / "data"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    demo_mode: bool = True            # synthetic/offline data when broker isn't configured
    disclaimer_accepted: bool = False


class BrokerSettings(BaseModel):
    static_ip: str = ""               # IP registered with SmartAPI app
    ip_check_url: str = "https://api.ipify.org"
    login_time: str = "08:30"
    intraday_cutoff: str = "15:20"    # Angel One F&O intraday auto square-off (from 2026-08-03)
    exchange_index: str = "NSE"
    exchange_fo: str = "NFO"
    instrument_master_url: str = (
        "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
    )
    index_tokens: dict[str, str] = Field(
        default_factory=lambda: {"NIFTY": "99926000", "BANKNIFTY": "99926009", "INDIAVIX": "99926017"}
    )


class RiskSettings(BaseModel):
    capital: float = 200_000.0
    risk_per_trade_pct: float = 1.0
    max_daily_loss_pct: float = 3.0
    max_trades_per_day: int = 5
    max_consecutive_losses: int = 3
    max_open_positions: int = 2
    max_lots_per_index: dict[str, int] = Field(default_factory=lambda: {"NIFTY": 4, "BANKNIFTY": 4})
    no_trade_first_min: int = 5
    no_trade_last_min: int = 20
    cooldown_after_loss_min: int = 10
    revenge_size_multiplier: float = 1.0    # next trade after a loss may not exceed previous size x this
    stale_feed_seconds: int = 10
    lock_edits_during_market: bool = True


class ExecutionSettings(BaseModel):
    mode: Mode = Mode.PAPER
    default_execution_mode: ExecutionMode = ExecutionMode.SIGNAL_ONLY
    limit_buffer_ticks: int = 4             # marketable-limit: LTP +/- buffer
    chase_interval_s: float = 3.0
    chase_max_attempts: int = 5
    sl_limit_buffer_pct: float = 0.5        # SL-L limit beyond trigger
    max_orders_per_second: float = Field(default=8.0, gt=0, le=8.0)   # SEBI: stay below 10 OPS
    square_off_minutes_before_cutoff: int = 5
    paper_slippage_ticks: int = 1
    preferred_instrument: str = "option"    # option / future
    option_moneyness: str = "ATM"           # ATM / ITM1 / OTM1


class StrategySettings(BaseModel):
    enabled: dict[str, bool] = Field(default_factory=dict)          # id -> enabled for live signals
    execution_mode: dict[str, ExecutionMode] = Field(default_factory=dict)
    params: dict[str, dict[str, Any]] = Field(default_factory=dict)
    expiry_strategies_enabled: bool = False
    # Validation thresholds (spec §6)
    min_profit_factor: float = 1.3
    min_trades_validated: int = 100
    min_trades_any: int = 50
    max_drawdown_r: float = 15.0
    high_win_rate_threshold: float = 0.80


class ContextInput(BaseModel):
    symbol: str                   # data-source symbol, e.g. yfinance ticker
    label: str
    weight: float = 1.0           # sign: +1 move up is bullish for Nifty, -1 inverse (e.g. VIX, USDINR, crude)
    required: bool = False
    max_age_min: int = 30         # pre-market inputs can be older (overnight)


class ContextSettings(BaseModel):
    refresh_seconds: int = 60
    risk_off_threshold: float = -0.5
    vix_risk_off_level: float = 22.0
    inputs: dict[str, ContextInput] = Field(
        default_factory=lambda: {
            "gift_nifty": ContextInput(symbol="GIFTNIFTY", label="GIFT Nifty", weight=2.0, required=False,
                                       max_age_min=30),
            "sp500_fut": ContextInput(symbol="ES=F", label="S&P 500 fut", weight=1.0, required=True,
                                      max_age_min=24 * 60),
            "nasdaq_fut": ContextInput(symbol="NQ=F", label="Nasdaq 100 fut", weight=0.7, max_age_min=24 * 60),
            "nikkei": ContextInput(symbol="^N225", label="Nikkei 225", weight=0.5, max_age_min=24 * 60),
            "hang_seng": ContextInput(symbol="^HSI", label="Hang Seng", weight=0.5, max_age_min=24 * 60),
            "usdinr": ContextInput(symbol="INR=X", label="USD/INR", weight=-0.8, max_age_min=24 * 60),
            "dxy": ContextInput(symbol="DX-Y.NYB", label="Dollar index", weight=-0.4, max_age_min=24 * 60),
            "us10y": ContextInput(symbol="^TNX", label="US 10Y yield", weight=-0.3, max_age_min=24 * 60),
            "brent": ContextInput(symbol="BZ=F", label="Brent crude", weight=-0.5, max_age_min=24 * 60),
            "india_vix": ContextInput(symbol="^INDIAVIX", label="India VIX", weight=-1.0, required=True,
                                      max_age_min=24 * 60),
        }
    )


class NewsSource(BaseModel):
    name: str
    url: str
    kind: str = "rss"
    enabled: bool = True


class NewsSettings(BaseModel):
    poll_seconds: int = 120
    use_finbert: bool = False
    sources: list[NewsSource] = Field(default_factory=list)
    high_impact_pause: bool = True
    blackout_before_min: int = 15
    blackout_after_min: int = 15


class NotifySettings(BaseModel):
    telegram_enabled: bool = False
    telegram_chat_id: str = ""
    notify_signals: bool = True
    notify_high_impact_news: bool = True
    notify_eod: bool = True


class ModelSettings(BaseModel):
    horizon_bars: int = 24                  # label horizon (5m bars)
    retrain_cron: str = "sat 10:00"
    min_samples: int = 50
    drift_window_trades: int = 30


class Settings(BaseModel):
    app: AppSettings = Field(default_factory=AppSettings)
    broker: BrokerSettings = Field(default_factory=BrokerSettings)
    risk: RiskSettings = Field(default_factory=RiskSettings)
    execution: ExecutionSettings = Field(default_factory=ExecutionSettings)
    strategies: StrategySettings = Field(default_factory=StrategySettings)
    context: ContextSettings = Field(default_factory=ContextSettings)
    news: NewsSettings = Field(default_factory=NewsSettings)
    notify: NotifySettings = Field(default_factory=NotifySettings)
    models: ModelSettings = Field(default_factory=ModelSettings)
    heavyweights: dict[str, dict[str, float]] = Field(default_factory=dict)  # underlying -> {symbol: weight}
    holidays: list[str] = Field(default_factory=list)                      # YYYY-MM-DD

    # ------------------------------------------------------------------ persistence
    @property
    def user_file(self) -> Path:
        return Path(self.app.data_dir) / "settings.yaml"

    def save_user(self) -> None:
        path = self.user_file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(self.model_dump(mode="json"), sort_keys=False))


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _env_overrides(prefix: str = "INDEXEDGE_") -> dict:
    out: dict = {}
    for key, val in os.environ.items():
        if not key.startswith(prefix):
            continue
        parts = key[len(prefix):].lower().split("__")
        cur = out
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = yaml.safe_load(val) if val else val
    return out


def load_settings(default_yaml: Path | None = DEFAULT_YAML, user_yaml: Path | None = None) -> Settings:
    data: dict = {}
    if default_yaml and Path(default_yaml).exists():
        data = yaml.safe_load(Path(default_yaml).read_text()) or {}
    env = _env_overrides()
    data = _deep_merge(data, env)
    base = Settings(**data)
    user = Path(user_yaml) if user_yaml else base.user_file
    if user.exists():
        data = _deep_merge(data, yaml.safe_load(user.read_text()) or {})
        data = _deep_merge(data, env)
    return Settings(**data)
