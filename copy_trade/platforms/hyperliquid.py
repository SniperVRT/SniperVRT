"""Hyperliquid vault connector — US-legal copy-trade primitive.

Hyperliquid is a non-custodial perpetuals DEX. Its **vault** system IS the
copy-trade primitive:
  - Anyone can create a vault (master trader)
  - Anyone can deposit USDC into a vault (follower)
  - Depositors automatically share PnL pro-rata
  - Withdraw whenever (subject to vault's lockup, typically 1 day)

So "subscribe to a master" = `vault_usd_transfer(vault, is_deposit=True, usd=...)`
And "unsubscribe" = `vault_usd_transfer(vault, is_deposit=False, usd=...)`

We use the official `hyperliquid-python-sdk` which handles EIP-712 signing.

Auth: an Ethereum private key (the wallet that holds USDC + signs actions).
Generate a fresh key for this bot — DO NOT use a wallet holding other assets.

Docs: https://hyperliquid.gitbook.io/hyperliquid-docs/
"""

from __future__ import annotations

import random
import time
from typing import Any

import structlog
from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants

from ..config import CopyTradeSettings, get_settings
from .base import MasterStats

log = structlog.get_logger("copy_trade.hyperliquid")


def _api_url(testnet: bool) -> str:
    return constants.TESTNET_API_URL if testnet else constants.MAINNET_API_URL


class HyperliquidConnector:
    """Wraps Hyperliquid SDK for vault-based copy trading."""

    def __init__(self, settings: CopyTradeSettings | None = None):
        self.s = settings or get_settings()
        base = _api_url(self.s.hyperliquid_testnet)
        self.info = Info(base, skip_ws=True)
        # Exchange is only built lazily when we need to sign actions.
        self._exchange: Exchange | None = None

    @property
    def exchange(self) -> Exchange:
        if self._exchange is None:
            if not self.s.hyperliquid_private_key:
                raise RuntimeError("hyperliquid_private_key not set")
            wallet = Account.from_key(self.s.hyperliquid_private_key)
            self._exchange = Exchange(
                wallet=wallet,
                base_url=_api_url(self.s.hyperliquid_testnet),
                account_address=self.s.hyperliquid_wallet_address or None,
            )
        return self._exchange

    # ---- read-only: leaderboard + vault details ------------------------------
    def vault_summaries(self) -> list[dict[str, Any]]:
        """List all public vaults with their summary stats.

        Returns a list of dicts; each has at minimum:
          vaultAddress, name, leader, tvl, apr, days_since_creation
        Field names depend on the live API response — we pass them through.
        """
        return self._post_info({"type": "vaultSummaries"})

    def vault_details(self, vault_address: str) -> dict[str, Any]:
        """Detailed stats for one vault — PnL history, MDD, drawdown, etc."""
        return self._post_info({
            "type": "vaultDetails",
            "vaultAddress": vault_address,
        })

    def leaderboard(self, *, page: int = 1, page_size: int = 100,
                    period: str = "ALL", enrich: bool = False,
                    enrich_top_n: int = 50) -> list[MasterStats]:
        """Fetch vaults as MasterStats.

        `enrich=True` calls vaultDetails for the top `enrich_top_n` by TVL
        to pull APR, MDD (computed from accountValueHistory), and follower
        count. Without enrichment, only `nickname`, `uid`, `aum_usdt` populate.
        """
        summaries = self.vault_summaries() or []
        # Filter out closed vaults
        open_vaults = [
            v for v in summaries
            if isinstance(v, dict) and not v.get("isClosed", False)
        ]
        # Sort by TVL desc for enrichment priority
        open_vaults.sort(key=lambda v: _float(v.get("tvl")) or 0.0, reverse=True)
        start = (page - 1) * page_size
        end = start + page_size
        result: list[MasterStats] = []
        for i, v in enumerate(open_vaults[start:end]):
            try:
                m = _parse_vault_summary(v)
                if enrich and i < enrich_top_n:
                    m = self.enrich_with_details(m)
                result.append(m)
            except Exception as e:  # noqa: BLE001
                log.warning("hl_parse_failed", v=v.get("vaultAddress"), err=str(e))
        return result

    def all_leaderboard(self, *, max_pages: int = 5,
                        enrich_top_n: int = 50) -> list[MasterStats]:
        """Iterate all vault pages with per-vault enrichment for the top N."""
        seen: set[str] = set()
        masters: list[MasterStats] = []
        total_enriched = 0
        for page in range(1, max_pages + 1):
            batch = self.leaderboard(
                page=page, page_size=100,
                enrich=(total_enriched < enrich_top_n),
                enrich_top_n=enrich_top_n - total_enriched,
            )
            if not batch:
                break
            for m in batch:
                if m.uid and m.uid not in seen:
                    seen.add(m.uid)
                    masters.append(m)
                    if m.roi_all is not None:
                        total_enriched += 1
        return masters

    def enrich_with_details(self, master: MasterStats) -> MasterStats:
        """Augment a summary-level master with full vault history stats."""
        try:
            d = self.vault_details(master.uid)
            return _merge_details(master, d)
        except Exception as e:  # noqa: BLE001
            log.warning("hl_enrich_failed", uid=master.uid, err=str(e))
            return master

    # ---- authenticated: deposit / withdraw / equity --------------------------
    def follow(self, vault_address: str, *, allocated_usdt: float) -> dict[str, Any]:
        """Subscribe = deposit USDC into the vault.

        Hyperliquid expects integer micro-USDC. We convert here.
        Returns the SDK response dict.
        """
        usd_int = int(round(allocated_usdt * 1_000_000))  # 6 decimals
        result = self._with_retry(
            lambda: self.exchange.vault_usd_transfer(
                vault_address=vault_address, is_deposit=True, usd=usd_int,
            )
        )
        log.info("hl_deposit", vault=vault_address, usdt=allocated_usdt, result=result)
        return result

    def unfollow(self, vault_address: str, *, withdraw_usdt: float) -> dict[str, Any]:
        """Unsubscribe = withdraw USDC from the vault."""
        usd_int = int(round(withdraw_usdt * 1_000_000))
        result = self._with_retry(
            lambda: self.exchange.vault_usd_transfer(
                vault_address=vault_address, is_deposit=False, usd=usd_int,
            )
        )
        log.info("hl_withdraw", vault=vault_address, usdt=withdraw_usdt, result=result)
        return result

    def my_vault_equities(self) -> list[dict[str, Any]]:
        """Return the user's current vault holdings.

        Each entry has at minimum: vaultAddress, equity (USDC value), lockUntil.
        """
        addr = self.s.hyperliquid_wallet_address
        if not addr:
            return []
        return self.info.user_vault_equities(addr) or []

    def account_balance(self) -> float:
        """USDC available in the funding wallet (not yet deployed to vaults)."""
        addr = self.s.hyperliquid_wallet_address
        if not addr:
            return 0.0
        try:
            state = self.info.user_state(addr) or {}
            margin = state.get("marginSummary") or {}
            return float(margin.get("accountValue", 0))
        except Exception as e:  # noqa: BLE001
            log.warning("hl_balance_failed", err=str(e))
            return 0.0

    def subscription_pnl(self, vault_address: str) -> dict[str, Any]:
        """Current value + unrealised PnL for one vault holding.

        Hyperliquid returns the equity (current USDC value of share). PnL is
        derived from equity - deposited; we track deposited in our DB so the
        worker can compute realised PnL itself.
        """
        for entry in self.my_vault_equities():
            if str(entry.get("vaultAddress", "")).lower() == vault_address.lower():
                return entry
        return {}

    # ---- internals -----------------------------------------------------------
    def _post_info(self, payload: dict[str, Any]) -> Any:
        """Wrapper with retry/backoff around Info.post."""
        return self._with_retry(lambda: self.info.post("/info", payload))

    def _with_retry(self, fn, *, max_retries: int = 4) -> Any:
        attempt = 0
        while True:
            attempt += 1
            try:
                return fn()
            except Exception as e:  # noqa: BLE001
                if attempt > max_retries:
                    raise
                delay = min(16.0, 2 ** (attempt - 1) + random.random() * 0.5)
                log.warning("hl_retry", attempt=attempt, delay=delay, err=str(e))
                time.sleep(delay)


# ---- parsing -----------------------------------------------------------------
def _float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> int | None:
    try:
        return int(float(str(v)))
    except (TypeError, ValueError):
        return None


def _parse_vault_summary(v: dict[str, Any]) -> MasterStats:
    """Map a vaultSummaries entry → MasterStats.

    Per Hyperliquid docs, vaultSummaries returns only:
      name, vaultAddress, leader, tvl, isClosed, relationship, createTimeMillis
    APR, MDD, follower count, history must come from vaultDetails.
    """
    return MasterStats(
        uid=str(v.get("vaultAddress") or ""),
        platform="hyperliquid",
        nickname=str(v.get("name") or "")[:40],
        roi_7d=None, roi_30d=None, roi_all=None,
        mdd=None, win_rate=None,
        total_trades=None, followers=None,
        aum_usdt=_float(v.get("tvl")),
        avg_holding_h=None, sharpe=None,
        raw=v,
    )


def _compute_mdd_from_history(account_value_history: list) -> float | None:
    """Max drawdown from an equity curve, returned as negative fraction.

    Hyperliquid `accountValueHistory` is a list of `[timestamp_ms, equity_str]`.
    """
    if not account_value_history or len(account_value_history) < 2:
        return None
    try:
        values = [float(point[1]) for point in account_value_history]
    except (TypeError, ValueError, IndexError):
        return None
    if not values:
        return None
    peak = values[0]
    worst_dd = 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            dd = (v - peak) / peak
            worst_dd = min(worst_dd, dd)
    return worst_dd  # negative fraction


def _merge_details(m: MasterStats, d: dict[str, Any]) -> MasterStats:
    """Overlay vaultDetails onto a summary-level master.

    vaultDetails returns: name, vaultAddress, leader, description, apr (annualised),
    leaderFraction, leaderCommission, followers[], portfolio (period→{accountValueHistory,
    pnlHistory, vlm}). MDD computed client-side from accountValueHistory.
    """
    raw = {**(m.raw or {}), **(d or {})}
    apr = _float(d.get("apr"))
    followers_arr = d.get("followers") or []
    follower_count = len(followers_arr) if isinstance(followers_arr, list) else m.followers

    # Try monthly window first, fall back to all-time, for MDD calc.
    portfolio = d.get("portfolio") or []
    history_by_period = {}
    if isinstance(portfolio, list):
        for entry in portfolio:
            if isinstance(entry, list) and len(entry) == 2:
                history_by_period[entry[0]] = entry[1] or {}

    monthly = history_by_period.get("month") or {}
    all_time = history_by_period.get("allTime") or {}

    mdd = (
        _compute_mdd_from_history(all_time.get("accountValueHistory") or [])
        or _compute_mdd_from_history(monthly.get("accountValueHistory") or [])
        or m.mdd
    )

    # Compute 7d / 30d ROI from accountValueHistory if available.
    roi_30d = _roi_over_history(monthly.get("accountValueHistory") or []) or m.roi_30d
    week = history_by_period.get("week") or {}
    roi_7d = _roi_over_history(week.get("accountValueHistory") or []) or m.roi_7d

    return MasterStats(
        uid=m.uid,
        platform=m.platform,
        nickname=m.nickname or str(d.get("name", ""))[:40],
        roi_7d=roi_7d,
        roi_30d=roi_30d,
        roi_all=apr,
        mdd=mdd,
        win_rate=m.win_rate,
        total_trades=m.total_trades,
        followers=follower_count,
        aum_usdt=_float(d.get("tvl")) or m.aum_usdt,
        avg_holding_h=m.avg_holding_h,
        sharpe=m.sharpe,
        raw=raw,
    )


def _roi_over_history(history: list) -> float | None:
    """Total return over a history series: (last - first) / first."""
    if not history or len(history) < 2:
        return None
    try:
        first = float(history[0][1])
        last = float(history[-1][1])
        if first <= 0:
            return None
        return (last - first) / first
    except (TypeError, ValueError, IndexError):
        return None
