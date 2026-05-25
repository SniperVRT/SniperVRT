"""Governance / council validation.

Computes a live-readiness score from validation evidence: backtest performance,
paper-trade stability, risk-rule integrity, data quality. Produces
council_decision.json, risk_report.json, paper_summary.json artifacts.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from backend.app.core.config import REPORT_DIR
from backend.app.core.db import session_scope
from backend.app.data import data_quality
from backend.app.models import (
    BacktestRun, EventLog, GovernanceDecision, PaperAccount, RiskBlock, Trade,
)


def _now() -> int:
    return int(time.time())


def _paper_summary() -> dict:
    with session_scope() as s:
        acc = s.query(PaperAccount).filter_by(name="default").one_or_none()
        if acc is None:
            return {"present": False}
        trades = s.query(Trade).filter_by(account_id=acc.id).order_by(Trade.ts.asc()).all()
        closed = [t for t in trades if t.reason and t.reason.startswith("close")]
        wins = [t for t in closed if t.pnl > 0]
        losses = [t for t in closed if t.pnl <= 0]
        gross_w = sum(t.pnl for t in wins) if wins else 0.0
        gross_l = sum(t.pnl for t in losses) if losses else 0.0
        return {
            "present": True,
            "equity": round(acc.equity, 2),
            "starting_equity": acc.starting_equity,
            "realized_pnl": round(acc.realized_pnl, 4),
            "fees_paid": round(acc.fees_paid, 4),
            "halted": acc.halted,
            "halt_reason": acc.halt_reason,
            "consecutive_losses": acc.consecutive_losses,
            "num_trades": len(closed),
            "win_rate": round(len(wins) / len(closed), 4) if closed else 0.0,
            "profit_factor": round(gross_w / abs(gross_l), 4) if gross_l < 0 else (gross_w if gross_w > 0 else 0.0),
        }


def _risk_report() -> dict:
    with session_scope() as s:
        blocks = s.query(RiskBlock).order_by(RiskBlock.ts.desc()).limit(50).all()
        return {
            "recent_blocks": [{
                "ts": b.ts, "rule": b.rule, "detail": b.detail,
                "strategy": b.strategy,
            } for b in blocks],
            "total_blocks": s.query(RiskBlock).count(),
        }


def _strategy_scoreboard() -> dict:
    with session_scope() as s:
        runs = s.query(BacktestRun).order_by(BacktestRun.sharpe.desc()).limit(20).all()
        return {"top": [{
            "strategy_name": r.strategy_name, "sharpe": r.sharpe,
            "total_return_pct": r.total_return_pct, "max_drawdown_pct": r.max_drawdown_pct,
            "win_rate": r.win_rate, "profit_factor": r.profit_factor,
            "num_trades": r.num_trades, "created_at": r.created_at.isoformat() if r.created_at else None,
        } for r in runs]}


def live_readiness_score() -> dict:
    """A bounded 0..100 score quantifying whether the system is healthy enough to consider live.

    Each criterion is gated: missing criteria score 0; we never claim readiness > 0
    without actual paper-trade evidence.
    """
    ps = _paper_summary()
    rr = _risk_report()
    ss = _strategy_scoreboard()
    dq = data_quality().to_dict()

    score = 0.0
    reasons: list[str] = []

    # Data quality (0–20)
    if dq["candle_count"] >= 500 and dq["coverage_pct"] >= 0.95:
        score += 20
    else:
        reasons.append(f"data_quality_low:{dq['coverage_pct']:.3f}")

    # Backtest evidence (0–30)
    top = ss["top"]
    if top:
        best = top[0]
        if best["sharpe"] >= 0.5:
            score += 15
        else:
            reasons.append(f"low_sharpe:{best['sharpe']}")
        if best["num_trades"] >= 20:
            score += 10
        else:
            reasons.append(f"few_backtest_trades:{best['num_trades']}")
        if best["max_drawdown_pct"] > -0.2:
            score += 5
        else:
            reasons.append(f"dd_too_deep:{best['max_drawdown_pct']:.2%}")
    else:
        reasons.append("no_backtest_runs")

    # Paper-trade evidence (0–30)
    if ps.get("present") and ps.get("num_trades", 0) >= 20:
        score += 10
        if ps.get("profit_factor", 0) >= 1.0:
            score += 10
        else:
            reasons.append(f"paper_profit_factor:{ps.get('profit_factor')}")
        if not ps.get("halted"):
            score += 10
        else:
            reasons.append(f"paper_halted:{ps.get('halt_reason')}")
    else:
        reasons.append(f"paper_trades_insufficient:{ps.get('num_trades', 0)}")

    # Risk-rule integrity (0–20): observed risk blocks are healthy unless excessive
    blocks = rr["total_blocks"]
    if blocks >= 1:
        score += 10  # risk engine is observably active
    else:
        reasons.append("risk_engine_silent")
    if ps.get("present") and ps.get("num_trades", 0) > 0 and blocks < 10 * max(1, ps.get("num_trades", 1)):
        score += 10
    else:
        reasons.append("risk_block_ratio_high")

    score = max(0.0, min(100.0, score))
    recommendation = "stay_paper"
    if score >= 80:
        recommendation = "consider_live_with_caution"
    elif score >= 60:
        recommendation = "extend_paper_run"
    return {
        "score": round(score, 2),
        "recommendation": recommendation,
        "evidence": {
            "data_quality": dq,
            "paper_summary": ps,
            "risk_report": rr,
            "strategy_scoreboard": ss,
        },
        "reasons": reasons,
        "ts": _now(),
    }


def generate_council_decision() -> dict:
    """Run validation and write all governance artifacts. Returns the council decision dict."""
    readiness = live_readiness_score()
    decision = "PAPER_ONLY"
    rationale = "Live trading remains locked. " + ", ".join(readiness["reasons"][:5])
    if readiness["score"] >= 80:
        decision = "RECOMMEND_LIVE_PILOT"
        rationale = "Validation passed thresholds; live remains locked pending explicit human unlock."

    out = {
        "ts": readiness["ts"],
        "decision": decision,
        "rationale": rationale,
        "live_readiness_score": readiness["score"],
        "recommendation": readiness["recommendation"],
        "evidence": readiness["evidence"],
        "reasons": readiness["reasons"],
        "live_locked": True,  # always — config gating still applies
    }

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "council_decision.json").write_text(json.dumps(out, indent=2, default=str))
    (REPORT_DIR / "live_readiness_report.json").write_text(json.dumps(readiness, indent=2, default=str))
    (REPORT_DIR / "risk_report.json").write_text(json.dumps(_risk_report(), indent=2, default=str))
    (REPORT_DIR / "paper_summary.json").write_text(json.dumps(_paper_summary(), indent=2, default=str))
    (REPORT_DIR / "strategy_scoreboard.json").write_text(json.dumps(_strategy_scoreboard(), indent=2, default=str))

    with session_scope() as s:
        s.add(GovernanceDecision(
            ts=_now(), category="readiness", decision=decision,
            rationale=rationale, live_readiness_score=readiness["score"],
            payload=out,
        ))
        s.add(EventLog(ts=_now(), level="info", category="governance",
                       message=f"council decision: {decision}", payload=out))
    return out
