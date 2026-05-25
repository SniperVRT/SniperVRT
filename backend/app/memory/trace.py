"""Decision traces — why a specific decision happened. The frontend uses these
to explain trades, blocks, governance verdicts, edge acceptances, etc.
"""
from __future__ import annotations

import time
from typing import Optional

from backend.app.core.db import session_scope
from backend.app.models import DecisionTrace


VALID_KINDS = {"open", "close", "block", "governance", "edge_accept",
               "edge_reject", "strategy_evolve", "regime_change", "tick"}


def trace(kind: str, subject: str, reason_chain: list[str], *,
          inputs: Optional[dict] = None, outputs: Optional[dict] = None) -> int:
    if kind not in VALID_KINDS:
        raise ValueError(f"unknown trace kind: {kind}")
    with session_scope() as s:
        t = DecisionTrace(
            ts=int(time.time()),
            kind=kind, subject=subject[:96], reason_chain=list(reason_chain),
            inputs=inputs or {}, outputs=outputs or {},
        )
        s.add(t)
        s.flush()
        return t.id


def recent_traces(*, kind: Optional[str] = None, subject: Optional[str] = None,
                  limit: int = 50) -> list[dict]:
    with session_scope() as s:
        q = s.query(DecisionTrace)
        if kind:
            q = q.filter(DecisionTrace.kind == kind)
        if subject:
            q = q.filter(DecisionTrace.subject == subject)
        rows = q.order_by(DecisionTrace.ts.desc()).limit(limit).all()
        return [{
            "id": t.id, "ts": t.ts, "kind": t.kind, "subject": t.subject,
            "reason_chain": t.reason_chain, "inputs": t.inputs, "outputs": t.outputs,
        } for t in rows]
