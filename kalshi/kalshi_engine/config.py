"""Centralised settings loaded from environment / `.env`."""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ExecutionMode(str, Enum):
    SIGNAL = "signal"      # only suggest; never trade
    PAPER = "paper"        # simulate fills against the book
    LIVE = "live"          # real orders, still gated by manual approval


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- Kalshi connection ---------------------------------------------------
    kalshi_api_key_id: str = ""
    kalshi_private_key_path: Path = Path("./secrets/kalshi_private_key.pem")
    kalshi_api_base: str = "https://api.elections.kalshi.com/trade-api/v2"

    # ---- Execution -----------------------------------------------------------
    execution_mode: ExecutionMode = ExecutionMode.SIGNAL

    # ---- Bankroll + risk caps (USD) -----------------------------------------
    bankroll_usd: float = 100.0
    max_position_usd: float = 5.0
    max_open_positions: int = 3
    daily_loss_stop_usd: float = 10.0
    weekly_loss_stop_usd: float = 30.0
    cooldown_minutes_after_loss: int = 30

    # ---- Signal quality gates -----------------------------------------------
    min_edge: float = Field(0.05, ge=0.0, le=1.0)
    min_confidence: float = Field(0.6, ge=0.0, le=1.0)
    max_spread_cents: int = 4
    min_volume_24h: int = 500
    min_minutes_to_close: int = 15
    max_minutes_to_close: int = 60 * 24 * 30  # 30 days

    # ---- Portfolio governance ----------------------------------------------
    max_open_exposure_usd: float = 15.0           # sum of all open notional
    max_category_exposure_usd: float = 10.0
    max_strategy_exposure_usd: float = 10.0
    max_portfolio_drawdown_usd: float = 25.0
    max_data_staleness_minutes: int = 10
    max_consecutive_losses: int = 2

    # ---- Live execution ----------------------------------------------------
    live_enabled: bool = False                    # MUST be true AND manual approve
    live_dry_run: bool = True                     # preview only by default
    live_require_manual_approval: bool = True

    # ---- News ingestion ----------------------------------------------------
    news_fetch_timeout_s: float = 10.0
    news_max_items_per_feed: int = 50

    # ---- Storage / logging ---------------------------------------------------
    db_path: Path = Path("./data/kalshi.db")
    log_level: str = "INFO"

    def ensure_dirs(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.kalshi_private_key_path.parent.mkdir(parents=True, exist_ok=True)


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
        _settings.ensure_dirs()
    return _settings
