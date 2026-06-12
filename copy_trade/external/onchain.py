"""On-chain flow data from Hyperliquid public endpoints.

Funding rates + open interest per coin feed into regime classification.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import structlog

from ..config import get_settings
from ..db import utc_now_iso

log = structlog.get_logger("copy_trade.onchain")


def fetch_meta_and_contexts() -> dict[str, Any] | None:
    """Pull `metaAndAssetCtxs` from Hyperliquid: per-coin funding + OI."""
    try:
        from ..platforms.hyperliquid import HyperliquidConnector
        hl = HyperliquidConnector(get_settings())
        return hl._post_info({"type": "metaAndAssetCtxs"})
    except Exception as e:  # noqa: BLE001
        log.warning("onchain_fetch_failed", err=str(e))
        return None


def aggregate_signal(meta_ctxs: Any) -> dict[str, float]:
    """Compute a risk-on / risk-off score from funding rates + OI deltas.

    Positive funding + rising OI → risk-on (+1). Negative funding + flat OI → risk-off (-1).
    """
    if not meta_ctxs or not isinstance(meta_ctxs, list) or len(meta_ctxs) < 2:
        return {"risk_on_score": 0.0, "n_coins": 0,
                "avg_funding": 0.0, "total_oi": 0.0}
    # Hyperliquid metaAndAssetCtxs returns [meta, [ctx_per_asset]]
    ctxs = meta_ctxs[1] if isinstance(meta_ctxs[1], list) else []
    fundings: list[float] = []
    ois: list[float] = []
    for c in ctxs:
        if not isinstance(c, dict):
            continue
        try:
            fundings.append(float(c.get("funding", 0)))
        except (TypeError, ValueError):
            pass
        try:
            ois.append(float(c.get("openInterest", 0)))
        except (TypeError, ValueError):
            pass
    if not fundings:
        return {"risk_on_score": 0.0, "n_coins": 0, "avg_funding": 0.0,
                "total_oi": 0.0}
    avg_f = sum(fundings) / len(fundings)
    total_oi = sum(ois)
    # Funding range is small; scale roughly so risk_on falls in [-1, +1].
    score = max(-1.0, min(1.0, avg_f * 1000))
    return {"risk_on_score": score, "n_coins": len(fundings),
            "avg_funding": avg_f, "total_oi": total_oi}


def persist_snapshot(conn: sqlite3.Connection,
                      signal: dict[str, float]) -> int:
    cur = conn.execute(
        "INSERT INTO external_signals "
        "(captured_at, source, key, score, payload_json) VALUES (?,?,?,?,?)",
        (utc_now_iso(), "onchain", "hyperliquid_funding_oi",
         signal.get("risk_on_score", 0.0), json.dumps(signal)),
    )
    return int(cur.lastrowid)
