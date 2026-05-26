"""Kalshi REST connector.

Public market data works without auth. Order placement and portfolio reads
require RSA-signed requests using an API key id + private key — see
https://trading-api.readme.io/reference/authentication.

This module exposes a thin client plus a `Market` / `Orderbook` dataclass
the rest of the engine consumes.
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from ..config import Settings, get_settings


# --------------------------------------------------------------------------- #
# Data models
# --------------------------------------------------------------------------- #
@dataclass
class Market:
    ticker: str
    event_ticker: str | None
    series_ticker: str | None
    title: str
    subtitle: str
    category: str | None
    status: str
    yes_bid: int | None
    yes_ask: int | None
    no_bid: int | None
    no_ask: int | None
    last_price: int | None
    volume: int | None
    volume_24h: int | None
    open_interest: int | None
    liquidity: int | None
    close_time: datetime | None
    open_time: datetime | None
    expected_expiration_time: datetime | None
    rules_primary: str | None
    rules_secondary: str | None
    settlement_source: str | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @property
    def midpoint_cents(self) -> float | None:
        if self.yes_bid is None or self.yes_ask is None:
            return None
        return (self.yes_bid + self.yes_ask) / 2.0

    @property
    def spread_cents(self) -> int | None:
        if self.yes_bid is None or self.yes_ask is None:
            return None
        return max(0, self.yes_ask - self.yes_bid)

    def minutes_to_close(self, now: datetime | None = None) -> float | None:
        if self.close_time is None:
            return None
        now = now or datetime.now(timezone.utc)
        return (self.close_time - now).total_seconds() / 60.0


@dataclass
class Orderbook:
    ticker: str
    yes: list[tuple[int, int]]  # [(price_cents, size)]
    no: list[tuple[int, int]]


# --------------------------------------------------------------------------- #
# Auth signer
# --------------------------------------------------------------------------- #
class _Signer:
    """Loads the RSA private key once and signs the per-request preimage."""

    def __init__(self, key_path: Path):
        self._key = None
        self._key_path = key_path

    def _load(self):
        if self._key is None:
            data = self._key_path.read_bytes()
            self._key = serialization.load_pem_private_key(data, password=None)
        return self._key

    def sign(self, timestamp_ms: str, method: str, path: str) -> str:
        # Kalshi v2 preimage: timestamp + METHOD + path (path includes /trade-api/v2/...)
        msg = f"{timestamp_ms}{method.upper()}{path}".encode()
        sig = self._load().sign(
            msg,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return base64.b64encode(sig).decode()


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
class KalshiClient:
    """Minimal Kalshi REST client.

    Public endpoints (markets, orderbook, events) work with no auth.
    Private endpoints attach the timestamp + signature headers if creds exist.
    """

    def __init__(self, settings: Settings | None = None, http: httpx.Client | None = None):
        self.settings = settings or get_settings()
        self.http = http or httpx.Client(
            base_url=self.settings.kalshi_api_base,
            timeout=httpx.Timeout(15.0, connect=5.0),
            headers={"User-Agent": "kalshi-engine/0.1"},
        )
        self._signer: _Signer | None = None
        if (
            self.settings.kalshi_api_key_id
            and self.settings.kalshi_private_key_path.exists()
        ):
            self._signer = _Signer(self.settings.kalshi_private_key_path)

    # ----- internal -----------------------------------------------------------
    def _auth_headers(self, method: str, path: str) -> dict[str, str]:
        if not self._signer or not self.settings.kalshi_api_key_id:
            return {}
        ts = str(int(time.time() * 1000))
        sig = self._signer.sign(ts, method, path)
        return {
            "KALSHI-ACCESS-KEY": self.settings.kalshi_api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": sig,
        }

    def _get(self, path: str, *, params: dict[str, Any] | None = None, auth: bool = False) -> dict:
        full_path = self.http.base_url.path.rstrip("/") + path
        headers = self._auth_headers("GET", full_path) if auth else {}
        r = self.http.get(path, params=params, headers=headers)
        r.raise_for_status()
        return r.json()

    # ----- public market data -------------------------------------------------
    def iter_markets(
        self,
        *,
        status: str = "open",
        limit: int = 200,
        max_pages: int | None = None,
    ) -> Iterator[Market]:
        cursor: str | None = None
        pages = 0
        while True:
            params: dict[str, Any] = {"status": status, "limit": limit}
            if cursor:
                params["cursor"] = cursor
            data = self._get("/markets", params=params)
            for m in data.get("markets", []):
                yield _parse_market(m)
            cursor = data.get("cursor") or None
            pages += 1
            if not cursor or (max_pages is not None and pages >= max_pages):
                break

    def get_orderbook(self, ticker: str, depth: int = 10) -> Orderbook:
        data = self._get(f"/markets/{ticker}/orderbook", params={"depth": depth})
        ob = data.get("orderbook", {}) or {}
        return Orderbook(
            ticker=ticker,
            yes=[(int(p), int(s)) for p, s in (ob.get("yes") or [])],
            no=[(int(p), int(s)) for p, s in (ob.get("no") or [])],
        )

    # ----- private (placeholder; gated by EXECUTION_MODE) ---------------------
    def get_balance(self) -> dict:
        return self._get("/portfolio/balance", auth=True)


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_market(m: dict[str, Any]) -> Market:
    return Market(
        ticker=m["ticker"],
        event_ticker=m.get("event_ticker"),
        series_ticker=m.get("series_ticker"),
        title=m.get("title", ""),
        subtitle=m.get("subtitle", "") or "",
        category=m.get("category"),
        status=m.get("status", "unknown"),
        yes_bid=m.get("yes_bid"),
        yes_ask=m.get("yes_ask"),
        no_bid=m.get("no_bid"),
        no_ask=m.get("no_ask"),
        last_price=m.get("last_price"),
        volume=m.get("volume"),
        volume_24h=m.get("volume_24h"),
        open_interest=m.get("open_interest"),
        liquidity=m.get("liquidity"),
        close_time=_parse_dt(m.get("close_time")),
        open_time=_parse_dt(m.get("open_time")),
        expected_expiration_time=_parse_dt(m.get("expected_expiration_time")),
        rules_primary=m.get("rules_primary"),
        rules_secondary=m.get("rules_secondary"),
        settlement_source=m.get("settlement_source"),
        raw=m,
    )
