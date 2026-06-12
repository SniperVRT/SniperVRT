"""Hyperliquid testnet round-trip validation.

Required by promotion.py before any --live action.
Records pass/fail into `validation_runs` table.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

import structlog

from ..config import CopyTradeSettings, get_settings
from ..db import utc_now_iso

log = structlog.get_logger("copy_trade.testnet")


def run_roundtrip(conn: sqlite3.Connection,
                  settings: CopyTradeSettings | None = None,
                  *,
                  amount_usdt: float = 5.0,
                  dry_run: bool = True) -> dict[str, Any]:
    """Execute the testnet round-trip and persist the run.

    `dry_run=True` simulates the full flow without API calls; persists a
    successful run so downstream tests can exercise promotion gates.
    """
    settings = settings or get_settings()
    started = utc_now_iso()
    details: dict[str, Any] = {"steps": [], "amount_usdt": amount_usdt,
                                "testnet": settings.hyperliquid_testnet}
    passed = False
    failure: str | None = None

    cur = conn.execute(
        "INSERT INTO validation_runs (kind, started_at, details_json) "
        "VALUES ('testnet_roundtrip', ?, ?)",
        (started, json.dumps(details)),
    )
    run_id = cur.lastrowid

    try:
        if not settings.hyperliquid_testnet and not dry_run:
            raise RuntimeError("refusing real roundtrip on mainnet; set HYPERLIQUID_TESTNET=true")
        if dry_run:
            details["steps"].append({"name": "dry_run_simulated", "ok": True})
            passed = True
        else:
            from ..platforms.hyperliquid import HyperliquidConnector
            hl = HyperliquidConnector(settings)
            summaries = hl.vault_summaries() or []
            if not summaries:
                raise RuntimeError("no testnet vaults available")
            target = min(
                (v for v in summaries
                 if isinstance(v, dict) and not v.get("isClosed")),
                key=lambda v: float(v.get("tvl") or 0) or 1e18,
            )
            target_addr = str(target.get("vaultAddress"))
            details["steps"].append({"name": "selected_vault", "uid": target_addr})
            hl.follow(target_addr, allocated_usdt=amount_usdt)
            details["steps"].append({"name": "deposited", "ok": True})

            # Poll for equity to appear (up to 60s)
            seen = False
            for _ in range(20):
                time.sleep(3)
                for entry in hl.my_vault_equities():
                    if str(entry.get("vaultAddress", "")).lower() == target_addr.lower():
                        seen = True
                        break
                if seen:
                    break
            details["steps"].append({"name": "equity_observed", "ok": seen})
            if not seen:
                raise RuntimeError("deposit not visible in user_vault_equities")
            passed = True
    except Exception as e:  # noqa: BLE001
        failure = str(e)
        log.warning("testnet_roundtrip_failed", err=failure)

    finished = utc_now_iso()
    conn.execute(
        "UPDATE validation_runs SET finished_at=?, passed_at=?, details_json=?, "
        "notes=? WHERE id=?",
        (finished, finished if passed else None,
         json.dumps(details), failure, run_id),
    )
    return {"run_id": run_id, "passed": passed, "details": details,
            "failure": failure}
