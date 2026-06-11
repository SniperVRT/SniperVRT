"""Copy-trade meta-allocator settings."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CopyTradeSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- Hyperliquid (US-legal: non-custodial perp DEX, vaults = copy primitive)
    # The wallet address is the funder; private key signs EIP-712 actions.
    hyperliquid_wallet_address: str = ""
    hyperliquid_private_key: str = ""      # 0x-prefixed hex; NEVER commit
    hyperliquid_api_base: str = "https://api.hyperliquid.xyz"
    hyperliquid_testnet: bool = True       # MUST flip to False for live

    # ---- Bitget / Bybit (kept for reference; both fail US geo gate currently)
    bitget_api_key: str = ""
    bitget_api_secret: str = ""
    bitget_passphrase: str = ""
    bybit_api_key: str = ""
    bybit_api_secret: str = ""

    # ---- Capital allocation --------------------------------------------------
    total_capital_usdt: float = 500.0
    max_masters: int = 10                   # max simultaneous copy subscriptions
    min_allocation_usdt: float = 20.0       # floor per master
    max_allocation_pct: float = 0.30        # max 30% to any single master
    min_allocation_pct: float = 0.05        # min 5% if allocated at all

    # ---- Ranking thresholds --------------------------------------------------
    min_track_record_days: int = 30         # ignore masters with < 30 days
    min_sharpe: float = 0.3                 # min Sharpe ratio to consider
    max_mdd_pct: float = 0.40              # reject masters with > 40% max drawdown
    min_win_rate: float = 0.40              # floor win rate (loose — Sharpe does the work)
    min_followers: int = 5                  # social proof floor (avoids brand-new fakes)

    # ---- Scoring weights (must sum to ~1) ------------------------------------
    weight_sharpe: float = 0.40
    weight_calmar: float = 0.25
    weight_win_rate: float = 0.10
    weight_profit_factor: float = 0.15
    weight_consistency: float = 0.10       # % of profitable weeks

    # ---- Rebalance schedule --------------------------------------------------
    leaderboard_poll_minutes: int = 60      # how often to refresh master list
    rebalance_minutes: int = 240            # how often to rebalance allocations
    pnl_poll_minutes: int = 15              # how often to pull subscription PnL

    # ---- Risk limits ---------------------------------------------------------
    max_daily_loss_pct: float = 0.05        # stop all copying if portfolio down >5% today
    max_total_drawdown_pct: float = 0.15    # emergency stop if portfolio down >15% ever
    max_delta_per_rebalance_pct: float = 0.50  # cap single-tick capital shift
    emergency_force_unwind: bool = False     # if drawdown gate trips: force withdraw

    # ---- Vault filters (added after live-readiness gap analysis) -------------
    min_vault_tvl_usdt: float = 50_000.0    # micro-vaults have unreliable APR
    max_leader_commission: float = 0.15      # skip vaults charging > 15% perf fee
    correlation_threshold: float = 0.70      # dedup vaults with daily-ret corr >= this

    # ---- Storage -------------------------------------------------------------
    db_path: Path = Path("./data/copy_trade.db")
    log_level: str = "INFO"

    def ensure_dirs(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)


_settings: CopyTradeSettings | None = None


def get_settings() -> CopyTradeSettings:
    global _settings
    if _settings is None:
        _settings = CopyTradeSettings()
        _settings.ensure_dirs()
    return _settings
