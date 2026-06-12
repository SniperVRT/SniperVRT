"""Abstract base for platform connectors."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class MasterStats:
    """Normalised master-trader stats fetched from a platform."""
    uid: str
    platform: str
    nickname: str
    roi_7d: float | None       # fraction (0.12 = 12%)
    roi_30d: float | None
    roi_all: float | None
    mdd: float | None          # max drawdown as negative fraction (-0.15 = -15%)
    win_rate: float | None     # fraction
    total_trades: int | None
    followers: int | None
    aum_usdt: float | None
    avg_holding_h: float | None
    sharpe: float | None       # if platform exposes it
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


class PlatformConnector(Protocol):
    """Every platform connector must implement this interface."""

    def leaderboard(self, *, page: int = 1, page_size: int = 50,
                    period: str = "ALL") -> list[MasterStats]:
        """Return a page of master traders sorted by platform's default ranking."""
        ...

    def subscribe(self, master_uid: str, *, allocated_usdt: float) -> str:
        """Subscribe the follower account to a master. Returns subscription id."""
        ...

    def unsubscribe(self, master_uid: str, subscription_id: str) -> bool:
        """Unsubscribe from a master. Returns True on success."""
        ...

    def subscription_pnl(self, subscription_id: str) -> dict[str, Any]:
        """Return current PnL for a subscription."""
        ...

    def account_balance(self) -> float:
        """Return available USDT balance of the follower account."""
        ...
