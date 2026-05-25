"""Central configuration. Loads YAML + environment variables. Live trading is LOCKED by default."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = ROOT / "configs"
DATA_DIR = ROOT / "data"
STATE_DIR = ROOT / "runtime_state"
REPORT_DIR = ROOT / "reports"
LOG_DIR = ROOT / "logs"

for d in (DATA_DIR, STATE_DIR, REPORT_DIR, LOG_DIR, DATA_DIR / "candles"):
    d.mkdir(parents=True, exist_ok=True)


class RiskLimits(BaseModel):
    max_position_size_pct: float = 0.10            # max 10% of equity per position
    max_open_positions: int = 1                    # single BTC position by default
    max_daily_loss_pct: float = 0.03               # halt after -3% day
    max_total_drawdown_pct: float = 0.20           # halt after -20% from peak
    consecutive_loss_cooldown: int = 3             # bars/cooldown after N losing trades
    min_confidence: float = 0.55                   # strategy signal confidence floor
    volatility_shutdown_atr_pct: float = 0.08      # halt if ATR/price > 8%
    min_data_quality: float = 0.95                 # require >=95% candle integrity


class ExecutionConfig(BaseModel):
    taker_fee: float = 0.0006                       # 6 bps
    maker_fee: float = 0.0002                       # 2 bps
    slippage_bps: float = 5.0                       # 5 bps
    spread_bps: float = 2.0
    partial_fill_prob: float = 0.0                  # disabled for now


class DataConfig(BaseModel):
    default_symbol: str = "BTC/USDT"
    default_timeframe: str = "1h"
    default_exchange: str = "binance"
    candles_back: int = 2000
    allow_synthetic_fallback: bool = True           # if no network, synthesize


class PaperConfig(BaseModel):
    starting_equity: float = 10_000.0
    tick_seconds: int = 30                          # how often the paper loop ticks
    enable_on_start: bool = False


class LiveConfig(BaseModel):
    """Live trading is locked by default. Never auto-enable."""
    locked: bool = True
    require_human_unlock: bool = True
    require_governance_approval: bool = True
    exchange: str = "binance"
    sandbox: bool = True


class AppConfig(BaseModel):
    risk: RiskLimits = RiskLimits()
    execution: ExecutionConfig = ExecutionConfig()
    data: DataConfig = DataConfig()
    paper: PaperConfig = PaperConfig()
    live: LiveConfig = LiveConfig()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="SNIPER_", extra="ignore")

    db_url: str = f"sqlite:///{(STATE_DIR / 'platform.db').as_posix()}"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    # Live keys MUST come from env, never repo
    exchange_api_key: str = ""
    exchange_api_secret: str = ""
    # Hard kill switch — env override
    kill_switch: bool = False


_settings: Settings | None = None
_app_config: AppConfig | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open() as f:
        return yaml.safe_load(f) or {}


def get_config() -> AppConfig:
    """Compose runtime config from defaults + configs/default.yaml + configs/risk.yaml."""
    global _app_config
    if _app_config is not None:
        return _app_config
    merged: dict[str, Any] = {}
    for name in ("default.yaml", "risk.yaml", "execution.yaml", "live.yaml"):
        merged_part = _load_yaml(CONFIG_DIR / name)
        for k, v in merged_part.items():
            merged.setdefault(k, v) if not isinstance(v, dict) else merged.setdefault(k, {}).update(v) if isinstance(merged.get(k), dict) else merged.update({k: v})
    try:
        _app_config = AppConfig(**merged) if merged else AppConfig()
    except Exception:
        _app_config = AppConfig()
    return _app_config


def reload_config() -> AppConfig:
    global _app_config
    _app_config = None
    return get_config()
