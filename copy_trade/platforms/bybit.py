"""Bybit Copy Trading v5 connector.

API docs: https://bybit-exchange.github.io/docs/v5/copytrading/overview
Authentication: HMAC-SHA256 signed requests (api_key + api_secret).

Public endpoints (no auth):
  GET /v5/copytrading/pub/leader-board   — paginated leaderboard

Authenticated endpoints:
  POST /v5/copytrading/order/create      — start copying a master
  POST /v5/copytrading/order/cancel      — stop copying
  GET  /v5/copytrading/order/list        — list active copy orders
  GET  /v5/copytrading/position/list     — open positions from copies
  GET  /v5/account/wallet-balance        — follower account balance

NOTE: Verify all paths against latest docs before going live. Bybit
occasionally renames endpoints between minor API versions.
"""

from __future__ import annotations

import hashlib
import hmac
import random
import time
from typing import Any

import httpx
import structlog

from ..config import CopyTradeSettings, get_settings
from .base import MasterStats

log = structlog.get_logger("copy_trade.bybit")

_MAINNET = "https://api.bybit.com"
_TESTNET = "https://api-testnet.bybit.com"

# Bybit's period parameter for leaderboard
_PERIOD_MAP = {"7D": "7D", "30D": "30D", "ALL": "ALL"}


class BybitConnector:
    def __init__(self, settings: CopyTradeSettings | None = None,
                 http: httpx.Client | None = None):
        self.s = settings or get_settings()
        base = _TESTNET if self.s.bybit_testnet else _MAINNET
        self.http = http or httpx.Client(
            base_url=base,
            timeout=httpx.Timeout(15.0, connect=5.0),
            headers={"User-Agent": "copy-trade-engine/0.1"},
        )

    # ----- auth ---------------------------------------------------------------
    def _sign(self, params: dict) -> dict:
        ts = str(int(time.time() * 1000))
        recv_window = "5000"
        param_str = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        preimage = f"{ts}{self.s.bybit_api_key}{recv_window}{param_str}"
        sig = hmac.new(
            self.s.bybit_api_secret.encode(), preimage.encode(), hashlib.sha256
        ).hexdigest()
        return {
            **params,
            "api_key": self.s.bybit_api_key,
            "timestamp": ts,
            "recv_window": recv_window,
            "sign": sig,
        }

    def _get(self, path: str, *, params: dict | None = None, auth: bool = False,
             max_retries: int = 4) -> dict:
        attempt = 0
        p = params or {}
        if auth:
            p = self._sign(p)
        while True:
            attempt += 1
            try:
                r = self.http.get(path, params=p)
                if r.status_code == 429 or 500 <= r.status_code < 600:
                    if attempt > max_retries:
                        r.raise_for_status()
                    delay = min(16.0, 2 ** (attempt - 1) + random.random() * 0.5)
                    log.warning("bybit_retry", path=path, status=r.status_code, delay=delay)
                    time.sleep(delay)
                    continue
                r.raise_for_status()
                data = r.json()
                if data.get("retCode", 0) != 0:
                    log.warning("bybit_api_error", path=path, code=data.get("retCode"),
                                msg=data.get("retMsg"))
                return data
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                if attempt > max_retries:
                    raise
                delay = min(16.0, 2 ** (attempt - 1) + random.random() * 0.5)
                log.warning("bybit_network_retry", path=path, attempt=attempt, err=str(e))
                time.sleep(delay)

    def _post(self, path: str, *, body: dict, max_retries: int = 4) -> dict:
        attempt = 0
        signed = self._sign(body)
        while True:
            attempt += 1
            try:
                r = self.http.post(path, json=signed)
                if r.status_code == 429 or 500 <= r.status_code < 600:
                    if attempt > max_retries:
                        r.raise_for_status()
                    delay = min(16.0, 2 ** (attempt - 1) + random.random() * 0.5)
                    time.sleep(delay)
                    continue
                r.raise_for_status()
                data = r.json()
                if data.get("retCode", 0) != 0:
                    log.warning("bybit_post_error", path=path, code=data.get("retCode"),
                                msg=data.get("retMsg"))
                return data
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                if attempt > max_retries:
                    raise
                delay = min(16.0, 2 ** (attempt - 1) + random.random() * 0.5)
                time.sleep(delay)

    # ----- public -------------------------------------------------------------
    def leaderboard(self, *, page: int = 1, page_size: int = 50,
                    period: str = "ALL") -> list[MasterStats]:
        """Fetch copy-trade master leaderboard (no auth required)."""
        data = self._get(
            "/v5/copytrading/pub/leader-board",
            params={"page": page, "pageSize": page_size,
                    "periodType": _PERIOD_MAP.get(period, "ALL")},
        )
        result: list[MasterStats] = []
        for item in (data.get("result", {}).get("list") or []):
            try:
                result.append(_parse_master(item))
            except Exception as e:  # noqa: BLE001
                log.warning("bybit_parse_master_failed", uid=item.get("leaderId"), err=str(e))
        return result

    def all_leaderboard(self, *, period: str = "ALL",
                        max_pages: int = 5) -> list[MasterStats]:
        """Iterate all leaderboard pages, deduplicated by uid."""
        seen: set[str] = set()
        masters: list[MasterStats] = []
        for page in range(1, max_pages + 1):
            batch = self.leaderboard(page=page, page_size=100, period=period)
            if not batch:
                break
            for m in batch:
                if m.uid not in seen:
                    seen.add(m.uid)
                    masters.append(m)
        return masters

    def master_stats(self, master_uid: str) -> MasterStats | None:
        """Get detailed stats for a single master."""
        data = self._get(
            "/v5/copytrading/pub/leader-stat",
            params={"leaderId": master_uid},
        )
        items = data.get("result", {}).get("list") or []
        if not items:
            return None
        try:
            return _parse_master(items[0])
        except Exception as e:  # noqa: BLE001
            log.warning("bybit_master_stat_failed", uid=master_uid, err=str(e))
            return None

    # ----- authenticated ------------------------------------------------------
    def subscribe(self, master_uid: str, *, allocated_usdt: float) -> str:
        """Start copying a master trader. Returns Bybit's order id."""
        data = self._post(
            "/v5/copytrading/order/create",
            body={
                "leaderId": master_uid,
                "investAmt": str(allocated_usdt),
                "investCoin": "USDT",
            },
        )
        order_id = (data.get("result") or {}).get("orderId", "")
        log.info("bybit_subscribed", master=master_uid, usdt=allocated_usdt, order_id=order_id)
        return order_id

    def unsubscribe(self, master_uid: str, subscription_id: str) -> bool:
        data = self._post(
            "/v5/copytrading/order/cancel",
            body={"orderId": subscription_id},
        )
        ok = data.get("retCode", -1) == 0
        log.info("bybit_unsubscribed", master=master_uid, sub=subscription_id, ok=ok)
        return ok

    def list_subscriptions(self) -> list[dict[str, Any]]:
        data = self._get("/v5/copytrading/order/list", auth=True)
        return (data.get("result", {}).get("list") or [])

    def subscription_pnl(self, subscription_id: str) -> dict[str, Any]:
        data = self._get(
            "/v5/copytrading/order/pnl",
            params={"orderId": subscription_id},
            auth=True,
        )
        return data.get("result") or {}

    def account_balance(self) -> float:
        data = self._get(
            "/v5/account/wallet-balance",
            params={"accountType": "UNIFIED"},
            auth=True,
        )
        wallets = (data.get("result", {}).get("list") or [])
        for w in wallets:
            if w.get("accountType") == "UNIFIED":
                return float(w.get("totalAvailableBalance", 0))
        return 0.0


# ---- parsing -----------------------------------------------------------------
def _pct(v: Any) -> float | None:
    try:
        return float(v) / 100.0
    except (TypeError, ValueError):
        return None


def _float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _parse_master(item: dict[str, Any]) -> MasterStats:
    # Bybit returns ROI as percentage strings (e.g. "12.34")
    return MasterStats(
        uid=str(item["leaderId"]),
        platform="bybit",
        nickname=item.get("nickName", "") or "",
        roi_7d=_pct(item.get("roi7d")),
        roi_30d=_pct(item.get("roi30d")),
        roi_all=_pct(item.get("roi")),
        mdd=_pct(item.get("maxDrawdown")),       # stored as positive %, we negate
        win_rate=_pct(item.get("winRate")),
        total_trades=_int(item.get("totalOrder")),
        followers=_int(item.get("followerNum")),
        aum_usdt=_float(item.get("aum")),
        avg_holding_h=_float(item.get("avgHoldingTime")),
        sharpe=_float(item.get("sharpeRatio")),
        raw=item,
    )
