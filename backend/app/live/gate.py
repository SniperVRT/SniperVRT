"""LIVE TRADING GATE — locked by default.

This module purposefully does NOT execute any orders. It models the readiness, approval, and
unlock workflow only. Activating live execution requires:

  1. configs/live.yaml: locked: false
  2. environment variables SNIPER_EXCHANGE_API_KEY and SNIPER_EXCHANGE_API_SECRET set
  3. governance approval (live_readiness_score >= 80)
  4. explicit POST /api/live/unlock with reason

Even when unlocked, this codebase ships without any code that actually places live orders.
A future LiveExecutor module would gate every send_order call behind LiveGate.is_unlocked().
"""
from __future__ import annotations

import time
from threading import Lock
from typing import Optional

from backend.app.core.config import get_config, get_settings
from backend.app.core.db import session_scope
from backend.app.governance.validator import live_readiness_score
from backend.app.models import EventLog, GovernanceDecision


class LiveGate:
    def __init__(self) -> None:
        self._unlocked = False
        self._unlock_ts: Optional[int] = None
        self._unlock_reason: str = ""
        self._lock = Lock()

    def status(self) -> dict:
        cfg = get_config().live
        s = get_settings()
        readiness = live_readiness_score()
        has_keys = bool(s.exchange_api_key and s.exchange_api_secret)
        gov_ok = readiness["score"] >= 80
        config_unlocked = not cfg.locked
        # Composite: ALL gates must be true for the system to ever consider live orders.
        composite = (
            config_unlocked
            and not s.kill_switch
            and has_keys
            and gov_ok
            and self._unlocked
        )
        return {
            "locked_config": cfg.locked,
            "kill_switch": s.kill_switch,
            "has_api_keys": has_keys,
            "governance_ok": gov_ok,
            "live_readiness_score": readiness["score"],
            "human_unlocked": self._unlocked,
            "unlock_ts": self._unlock_ts,
            "unlock_reason": self._unlock_reason,
            "ready_for_live": composite,
            "exchange": cfg.exchange,
            "sandbox": cfg.sandbox,
            "require_human_unlock": cfg.require_human_unlock,
            "require_governance_approval": cfg.require_governance_approval,
        }

    def unlock(self, reason: str) -> dict:
        """Human unlock. Does NOT execute orders. Caller must still set locked=false in configs/live.yaml."""
        with self._lock:
            self._unlocked = True
            self._unlock_ts = int(time.time())
            self._unlock_reason = reason or "(no reason)"
        with session_scope() as s:
            s.add(EventLog(ts=int(time.time()), level="warn", category="live",
                           message="live trading human-unlock requested",
                           payload={"reason": reason}))
            s.add(GovernanceDecision(
                ts=int(time.time()), category="live_unlock",
                decision="HUMAN_UNLOCK", rationale=reason,
                live_readiness_score=live_readiness_score()["score"],
                payload={"reason": reason},
            ))
        return self.status()

    def relock(self, reason: str = "manual") -> dict:
        with self._lock:
            self._unlocked = False
            self._unlock_ts = None
            self._unlock_reason = ""
        with session_scope() as s:
            s.add(EventLog(ts=int(time.time()), level="warn", category="live",
                           message="live trading re-locked", payload={"reason": reason}))
        return self.status()

    def emergency_shutdown(self) -> dict:
        with self._lock:
            self._unlocked = False
            self._unlock_ts = None
            self._unlock_reason = ""
        from backend.app.paper.runtime import get_runtime
        get_runtime().stop()
        with session_scope() as s:
            s.add(EventLog(ts=int(time.time()), level="error", category="emergency",
                           message="EMERGENCY SHUTDOWN", payload={}))
        return {"emergency": True, **self.status()}

    def is_unlocked(self) -> bool:
        return self.status()["ready_for_live"]


_gate: Optional[LiveGate] = None


def get_live_gate() -> LiveGate:
    global _gate
    if _gate is None:
        _gate = LiveGate()
    return _gate
