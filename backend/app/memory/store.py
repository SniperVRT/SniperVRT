"""Structured long-term memory: failures, successes, anomalies, warnings.

Memory is intentionally simple — it's just rows. Queries support recency, subject,
and weight aggregation so downstream agents can ask "what do we know about X?".
"""
from __future__ import annotations

import math
import time
from typing import Iterable, Optional

from backend.app.core.db import session_scope
from backend.app.models import MemoryEntry


VALID_KINDS = {"failure", "success", "anomaly", "warning", "insight"}


def remember(kind: str, subject: str, summary: str, *,
             weight: float = 1.0, evidence: Optional[dict] = None) -> int:
    if kind not in VALID_KINDS:
        raise ValueError(f"unknown memory kind: {kind}")
    with session_scope() as s:
        m = MemoryEntry(
            ts=int(time.time()),
            kind=kind, subject=subject[:96], summary=summary,
            weight=float(weight), evidence=evidence or {},
        )
        s.add(m)
        s.flush()
        return m.id


def recall(*, subject: Optional[str] = None, kinds: Optional[Iterable[str]] = None,
           since_ts: Optional[int] = None, limit: int = 50) -> list[dict]:
    with session_scope() as s:
        q = s.query(MemoryEntry)
        if subject:
            q = q.filter(MemoryEntry.subject == subject)
        if kinds:
            q = q.filter(MemoryEntry.kind.in_(list(kinds)))
        if since_ts is not None:
            q = q.filter(MemoryEntry.ts >= since_ts)
        rows = q.order_by(MemoryEntry.ts.desc()).limit(limit).all()
        return [{
            "id": m.id, "ts": m.ts, "kind": m.kind, "subject": m.subject,
            "summary": m.summary, "weight": m.weight, "evidence": m.evidence,
        } for m in rows]


def recent(limit: int = 25) -> list[dict]:
    return recall(limit=limit)


def score_for_subject(subject: str, *, half_life_days: float = 14.0) -> dict:
    """Weighted score for a subject: positive for successes, negative for failures.

    Older memories decay exponentially. Returns counts and a single composite score
    in [-1, +1]-ish range.
    """
    decay_lambda = math.log(2) / (half_life_days * 86400.0)
    now = int(time.time())
    with session_scope() as s:
        rows = s.query(MemoryEntry).filter(MemoryEntry.subject == subject).all()
        score = 0.0
        counts = {"failure": 0, "success": 0, "anomaly": 0, "warning": 0, "insight": 0}
        for m in rows:
            age = max(0, now - m.ts)
            decay = math.exp(-decay_lambda * age)
            sign = {"success": 1.0, "insight": 0.5, "failure": -1.0,
                    "warning": -0.5, "anomaly": -0.3}.get(m.kind, 0.0)
            score += sign * m.weight * decay
            counts[m.kind] = counts.get(m.kind, 0) + 1
    return {
        "subject": subject,
        "score": round(score, 4),
        "counts": counts,
        "total": sum(counts.values()),
    }


def forget_older_than(days: float) -> int:
    cutoff = int(time.time()) - int(days * 86400)
    with session_scope() as s:
        n = s.query(MemoryEntry).filter(MemoryEntry.ts < cutoff,
                                         MemoryEntry.weight < 0.5).delete()
        return int(n)


def top_warnings(limit: int = 10) -> list[dict]:
    return recall(kinds={"failure", "warning", "anomaly"}, limit=limit)


def top_successes(limit: int = 10) -> list[dict]:
    return recall(kinds={"success", "insight"}, limit=limit)
