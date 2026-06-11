"""Bitget Copy Trading v2 connector.

THE ONLY platform with a full programmatic follower subscribe/unsubscribe API.
(Bybit and Binance require UI-based subscription; see base.py notes.)

API docs: https://www.bitget.com/api-doc/copytrading/intro

Authentication: HMAC-SHA256.
  Headers: ACCESS-KEY, ACCESS-SIGN, ACCESS-TIMESTAMP, ACCESS-PASSPHRASE
  Preimage: {timestamp}{METHOD}{path}[?query]{body_json_if_post}

Public endpoints (no auth):
  GET /api/mix/v1/trace/traderList      — leaderboard (ROI, win rate, followers)

Authenticated follower endpoints:
  POST /api/v2/copy/mix-follower/settings         — follow a master + configure
  POST /api/v2/copy/mix-follower/cancel-trader    — unfollow
  GET  /api/v2/copy/mix-follower/query-traders    — list currently followed
  GET  /api/v2/copy/mix-follower/query-current-orders  — open copy positions
  GET  /api/v2/copy/mix-follower/query-history-orders  — history
  POST /api/v2/copy/mix-follower/close-positions  — close a copy position

Rate limits: 10 req/sec per UID standard; 250 req/sec on UTA.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json as _json
import random
import time
from typing import Any
from urllib.parse import urlencode

import httpx
import structlog

from ..config import CopyTradeSettings, get_settings
from .base import MasterStats

log = structlog.get_logger("copy_trade.bitget")

_MAINNET = "https://api.bitget.com"

# productType used in most copy trading calls
_PRODUCT_TYPE = "USDT-FUTURES"


class BitgetConnector:
    def __init__(self, settings: CopyTradeSettings | None = None,
                 http: httpx.Client | None = None):
        self.s = settings or get_settings()
        self.http = http or httpx.Client(
            base_url=_MAINNET,
            timeout=httpx.Timeout(15.0, connect=5.0),
            headers={"User-Agent": "copy-trade-engine/0.1", "Content-Type": "application/json"},
        )

    # ---- auth ----------------------------------------------------------------
    def _sign(self, timestamp: str, method: str, path: str,
              query: str = "", body: str = "") -> str:
        preimage = f"{timestamp}{method.upper()}{path}"
        if query:
            preimage += f"?{query}"
        preimage += body
        return base64.b64encode(
            hmac.new(
                self.s.bitget_api_secret.encode(),
                preimage.encode(),
                hashlib.sha256,
            ).digest()
        ).decode()

    def _auth_headers(self, method: str, path: str,
                      query: str = "", body: str = "") -> dict[str, str]:
        ts = str(int(time.time() * 1000))
        return {
            "ACCESS-KEY": self.s.bitget_api_key,
            "ACCESS-SIGN": self._sign(ts, method, path, query, body),
            "ACCESS-TIMESTAMP": ts,
            "ACCESS-PASSPHRASE": self.s.bitget_passphrase,
        }

    def _get(self, path: str, *, params: dict | None = None, auth: bool = False,
             max_retries: int = 4) -> dict:
        attempt = 0
        query = urlencode(params) if params else ""
        while True:
            attempt += 1
            try:
                headers = self._auth_headers("GET", path, query) if auth else {}
                r = self.http.get(path, params=params, headers=headers)
                if r.status_code == 429 or 500 <= r.status_code < 600:
                    if attempt > max_retries:
                        r.raise_for_status()
                    delay = min(16.0, 2 ** (attempt - 1) + random.random() * 0.5)
                    log.warning("bitget_retry", status=r.status_code, delay=delay)
                    time.sleep(delay)
                    continue
                r.raise_for_status()
                data = r.json()
                if str(data.get("code", "00000")) != "00000":
                    log.warning("bitget_api_error", path=path,
                                code=data.get("code"), msg=data.get("msg"))
                return data
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                if attempt > max_retries:
                    raise
                delay = min(16.0, 2 ** (attempt - 1) + random.random() * 0.5)
                log.warning("bitget_network_retry", attempt=attempt, err=str(e))
                time.sleep(delay)

    def _post(self, path: str, *, body: dict, max_retries: int = 4) -> dict:
        attempt = 0
        body_str = _json.dumps(body, separators=(",", ":"))
        while True:
            attempt += 1
            try:
                headers = self._auth_headers("POST", path, "", body_str)
                r = self.http.post(path, content=body_str, headers=headers)
                if r.status_code == 429 or 500 <= r.status_code < 600:
                    if attempt > max_retries:
                        r.raise_for_status()
                    delay = min(16.0, 2 ** (attempt - 1) + random.random() * 0.5)
                    time.sleep(delay)
                    continue
                r.raise_for_status()
                data = r.json()
                if str(data.get("code", "00000")) != "00000":
                    log.warning("bitget_post_error", path=path,
                                code=data.get("code"), msg=data.get("msg"))
                return data
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                if attempt > max_retries:
                    raise
                delay = min(16.0, 2 ** (attempt - 1) + random.random() * 0.5)
                time.sleep(delay)

    # ---- public leaderboard --------------------------------------------------
    def leaderboard(self, *, page_no: int = 1, page_size: int = 50) -> list[MasterStats]:
        """Fetch master trader list (public, no auth)."""
        data = self._get(
            "/api/mix/v1/trace/traderList",
            params={"pageNo": page_no, "pageSize": page_size,
                    "productType": _PRODUCT_TYPE},
        )
        result: list[MasterStats] = []
        for item in (data.get("data", {}).get("records") or []):
            try:
                result.append(_parse_master(item))
            except Exception as e:  # noqa: BLE001
                log.warning("bitget_parse_failed", uid=item.get("traderId"), err=str(e))
        return result

    def all_leaderboard(self, *, max_pages: int = 10) -> list[MasterStats]:
        seen: set[str] = set()
        masters: list[MasterStats] = []
        for page in range(1, max_pages + 1):
            batch = self.leaderboard(page_no=page, page_size=100)
            if not batch:
                break
            for m in batch:
                if m.uid not in seen:
                    seen.add(m.uid)
                    masters.append(m)
        return masters

    def master_detail(self, trader_id: str) -> MasterStats | None:
        """Get detailed stats for one master."""
        data = self._get(
            "/api/mix/v1/trace/traderDetail",
            params={"traderId": trader_id, "productType": _PRODUCT_TYPE},
        )
        item = data.get("data")
        if not item:
            return None
        try:
            return _parse_master(item)
        except Exception as e:  # noqa: BLE001
            log.warning("bitget_detail_failed", uid=trader_id, err=str(e))
            return None

    # ---- authenticated follower API ------------------------------------------
    def follow(self, trader_id: str, *,
               allocated_usdt: float, product_type: str = _PRODUCT_TYPE,
               trace_type: str = "percent", trace_value: float = 10.0,
               max_hold_size: int = 10) -> dict:
        """Subscribe to a master trader.

        trace_type='percent': copy X% of master's position size
        trace_type='amount':  copy a fixed USDT amount per trade

        Returns the full API response dict.
        """
        return self._post(
            "/api/v2/copy/mix-follower/settings",
            body={
                "traderId": trader_id,
                "productType": product_type,
                "traceType": trace_type,
                "traceValue": str(trace_value),
                "maxHoldSize": str(max_hold_size),
            },
        )

    def unfollow(self, trader_id: str, product_type: str = _PRODUCT_TYPE) -> bool:
        data = self._post(
            "/api/v2/copy/mix-follower/cancel-trader",
            body={"traderId": trader_id, "productType": product_type},
        )
        ok = str(data.get("code", "")) == "00000"
        log.info("bitget_unfollow", trader=trader_id, ok=ok)
        return ok

    def followed_traders(self, product_type: str = _PRODUCT_TYPE) -> list[dict[str, Any]]:
        data = self._get(
            "/api/v2/copy/mix-follower/query-traders",
            params={"productType": product_type},
            auth=True,
        )
        return data.get("data") or []

    def current_orders(self, product_type: str = _PRODUCT_TYPE) -> list[dict[str, Any]]:
        data = self._get(
            "/api/v2/copy/mix-follower/query-current-orders",
            params={"productType": product_type, "pageSize": "100"},
            auth=True,
        )
        return (data.get("data") or {}).get("orderList") or []

    def history_orders(self, product_type: str = _PRODUCT_TYPE,
                       limit: int = 100) -> list[dict[str, Any]]:
        data = self._get(
            "/api/v2/copy/mix-follower/query-history-orders",
            params={"productType": product_type, "pageSize": str(limit)},
            auth=True,
        )
        return (data.get("data") or {}).get("orderList") or []

    def account_balance(self, product_type: str = _PRODUCT_TYPE) -> float:
        """Available USDT in the copy trade account."""
        data = self._get(
            "/api/v2/account/accounts",
            params={"productType": product_type},
            auth=True,
        )
        for item in (data.get("data") or []):
            if item.get("marginCoin", "").upper() == "USDT":
                return float(item.get("available", 0) or 0)
        return 0.0

    def subscription_pnl(self, trader_id: str) -> dict[str, Any]:
        """Get PnL summary for a followed trader."""
        data = self._get(
            "/api/v2/copy/mix-follower/query-traders",
            params={"productType": _PRODUCT_TYPE},
            auth=True,
        )
        for item in (data.get("data") or []):
            if str(item.get("traderId", "")) == str(trader_id):
                return item
        return {}


# ---- parsing -----------------------------------------------------------------
def _pct(v: Any) -> float | None:
    try:
        f = float(v)
        return f / 100.0 if abs(f) > 1.0 else f
    except (TypeError, ValueError):
        return None


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


def _parse_master(item: dict[str, Any]) -> MasterStats:
    return MasterStats(
        uid=str(item.get("traderId", item.get("traderUid", ""))),
        platform="bitget",
        nickname=item.get("nickName", "") or "",
        roi_7d=_pct(item.get("7dYield") or item.get("recentMonthYield")),
        roi_30d=_pct(item.get("monthlyYield") or item.get("recentMonthYield")),
        roi_all=_pct(item.get("totalYield") or item.get("totalProfitLossRatio")),
        mdd=_pct(item.get("maxDrawdown") or item.get("maxRetracement")),
        win_rate=_pct(item.get("winRate")),
        total_trades=_int(item.get("totalOrder") or item.get("orderCount")),
        followers=_int(item.get("followerNum") or item.get("followerCount")),
        aum_usdt=_float(item.get("followerTotalInvest") or item.get("aum")),
        avg_holding_h=_float(item.get("avgHoldingTime")),
        sharpe=None,  # not exposed in Bitget public API
        raw=item,
    )
