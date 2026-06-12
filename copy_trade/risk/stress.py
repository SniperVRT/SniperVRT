"""Three deterministic stress scenarios on the active portfolio."""

from __future__ import annotations

import sqlite3

from ..config import CopyTradeSettings, get_settings
from ..execution.subscriptions import active_subscriptions


def stress_test(conn: sqlite3.Connection,
                settings: CopyTradeSettings | None = None) -> list[dict]:
    settings = settings or get_settings()
    subs = active_subscriptions(conn)
    total = sum(float(s["allocated_usdt"]) for s in subs) or 1.0
    if not subs:
        return []

    # 1. crypto_crash: -30% across the board
    loss_a = 0.30 * total

    # 2. single_blowup: largest holding -50%
    largest = max(float(s["allocated_usdt"]) for s in subs)
    loss_b = 0.50 * largest

    # 3. correlation_unification: every vault realises worst single drop (-50%)
    loss_c = 0.50 * total

    return [
        {"scenario": "crypto_crash", "predicted_loss_usdt": loss_a,
         "predicted_drawdown_pct": loss_a / settings.total_capital_usdt,
         "would_breach": loss_a / settings.total_capital_usdt >= settings.max_total_drawdown_pct},
        {"scenario": "single_blowup", "predicted_loss_usdt": loss_b,
         "predicted_drawdown_pct": loss_b / settings.total_capital_usdt,
         "would_breach": loss_b / settings.total_capital_usdt >= settings.max_total_drawdown_pct},
        {"scenario": "correlation_unification", "predicted_loss_usdt": loss_c,
         "predicted_drawdown_pct": loss_c / settings.total_capital_usdt,
         "would_breach": loss_c / settings.total_capital_usdt >= settings.max_total_drawdown_pct},
    ]
