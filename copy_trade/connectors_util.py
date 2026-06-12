"""Shared connector utilities: data quality gates + circuit breakers."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field

from .db import utc_now_iso


@dataclass
class CircuitBreaker:
    name: str
    failure_threshold: int = 5
    cool_down_s: float = 300.0
    failures: int = 0
    open_until: float = 0.0
    events: list[str] = field(default_factory=list)

    def record_success(self) -> None:
        self.failures = 0

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self.open_until = time.time() + self.cool_down_s
            self.events.append(f"open_at_{int(time.time())}")

    def is_open(self) -> bool:
        if time.time() < self.open_until:
            return True
        if self.open_until and time.time() >= self.open_until:
            self.open_until = 0
            self.failures = 0
        return False


def log_data_quality_failure(conn: sqlite3.Connection, source: str,
                              reason: str, raw: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO data_quality_failures "
        "(captured_at, source, reason, raw_json) VALUES (?,?,?,?)",
        (utc_now_iso(), source, reason,
         json.dumps(raw or {}, default=str)[:8000]),
    )


def validate_kalshi_market(raw: dict) -> str | None:
    """Return reason string if market should be rejected, else None."""
    if not isinstance(raw, dict):
        return "not_dict"
    if "ticker" not in raw:
        return "missing_ticker"
    vol = raw.get("volume")
    if vol is not None and (isinstance(vol, (int, float)) and vol < 0):
        return "negative_volume"
    return None


def validate_hl_vault(raw: dict) -> str | None:
    if not isinstance(raw, dict):
        return "not_dict"
    if "vaultAddress" not in raw:
        return "missing_vaultAddress"
    tvl = raw.get("tvl")
    try:
        if tvl is not None and float(tvl) < 0:
            return "negative_tvl"
    except (TypeError, ValueError):
        return "tvl_unparseable"
    create_ms = raw.get("createTimeMillis")
    if create_ms is not None:
        try:
            if int(create_ms) > int(time.time() * 1000) + 60_000:
                return "future_create_time"
        except (TypeError, ValueError):
            pass
    return None
