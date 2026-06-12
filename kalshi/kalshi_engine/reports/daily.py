"""Daily report generator.

Pure-SQL aggregation over the last 24 hours: scanner health, signals
emitted, rejection breakdown, strategy attribution, paper PnL,
calibration delta. Output is a JSON-able dict + a plain-text renderer
for CLI / Slack / email distribution later.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from ..db import utc_now_iso
from ..validation.metrics import performance_report


def _last_24h_iso() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()


def build_daily_report(conn: sqlite3.Connection, *, day: str | None = None) -> dict[str, Any]:
    since = _last_24h_iso()
    day = day or datetime.now(timezone.utc).date().isoformat()

    scanned = conn.execute(
        "SELECT COUNT(*) FROM market_snapshots WHERE captured_at >= ?", (since,)
    ).fetchone()[0]

    signals_total = conn.execute(
        "SELECT COUNT(*) FROM signals WHERE created_at >= ?", (since,)
    ).fetchone()[0]

    by_strategy = conn.execute(
        "SELECT strategy, COUNT(*), AVG(edge), AVG(confidence) "
        "FROM signals WHERE created_at >= ? GROUP BY strategy",
        (since,),
    ).fetchall()

    rejection_reasons = conn.execute(
        "SELECT reason, COUNT(*) FROM rejected_signals "
        "WHERE created_at >= ? GROUP BY reason ORDER BY 2 DESC LIMIT 20",
        (since,),
    ).fetchall()

    quality_summary = conn.execute(
        "SELECT AVG(total_score), MIN(total_score), MAX(total_score), COUNT(*) "
        "FROM market_quality WHERE captured_at >= ?",
        (since,),
    ).fetchone()

    resolution_breakdown = conn.execute(
        "SELECT risk_class, COUNT(*) FROM resolution_classifications "
        "WHERE classified_at >= ? GROUP BY risk_class",
        (since,),
    ).fetchall()

    paper_pnl = conn.execute(
        "SELECT realized_usd, fees_usd, trade_count FROM paper_pnl WHERE day=?", (day,),
    ).fetchone()

    open_positions = conn.execute(
        "SELECT COUNT(*) FROM paper_positions WHERE closed_at IS NULL"
    ).fetchone()[0]

    perf = performance_report(conn)

    return {
        "day": day,
        "window_start": since,
        "scanned_snapshots": int(scanned or 0),
        "signals_total": int(signals_total or 0),
        "by_strategy": [
            {"strategy": s, "count": int(c),
             "avg_edge": float(e or 0), "avg_confidence": float(cf or 0)}
            for s, c, e, cf in by_strategy
        ],
        "rejection_reasons": [{"reason": r, "count": int(c)} for r, c in rejection_reasons],
        "quality": {
            "avg": float(quality_summary[0] or 0),
            "min": float(quality_summary[1] or 0),
            "max": float(quality_summary[2] or 0),
            "n": int(quality_summary[3] or 0),
        },
        "resolution_classes": [{"class": rc, "count": int(c)} for rc, c in resolution_breakdown],
        "paper_pnl": {
            "realized_usd": float(paper_pnl[0]) if paper_pnl else 0.0,
            "fees_usd": float(paper_pnl[1]) if paper_pnl else 0.0,
            "trade_count": int(paper_pnl[2]) if paper_pnl else 0,
        },
        "open_paper_positions": int(open_positions or 0),
        "calibration": {
            "n": perf.n, "brier": perf.brier, "log_loss": perf.log_loss,
            "profit_factor": perf.profit_factor, "max_drawdown": perf.max_drawdown,
            "win_rate": perf.win_rate, "expectancy": perf.expectancy,
        },
    }


def render_daily_report_text(report: dict[str, Any]) -> str:
    lines = [
        f"Daily Report — {report['day']}",
        f"  window:    last 24h since {report['window_start']}",
        f"  snapshots: {report['scanned_snapshots']}",
        f"  signals:   {report['signals_total']}",
    ]
    if report["by_strategy"]:
        lines.append("  per strategy:")
        for r in report["by_strategy"]:
            lines.append(
                f"    - {r['strategy']:<22} n={r['count']:<4} "
                f"avg_edge={r['avg_edge']:+.2%}  avg_conf={r['avg_confidence']:.2f}"
            )
    if report["rejection_reasons"]:
        lines.append("  top rejections:")
        for r in report["rejection_reasons"][:10]:
            lines.append(f"    - {r['reason']:<32} {r['count']}")
    q = report["quality"]
    lines.append(f"  quality:   avg={q['avg']:.2f} min={q['min']:.2f} max={q['max']:.2f} n={q['n']}")
    if report["resolution_classes"]:
        rcs = ", ".join(f"{r['class']}={r['count']}" for r in report["resolution_classes"])
        lines.append(f"  resolution: {rcs}")
    p = report["paper_pnl"]
    lines.append(
        f"  paper pnl: ${p['realized_usd']:+.2f}  trades={p['trade_count']}  "
        f"open={report['open_paper_positions']}"
    )
    c = report["calibration"]
    lines.append(
        f"  metrics:   n={c['n']} brier={c['brier']:.4f} log_loss={c['log_loss']:.4f} "
        f"pf={c['profit_factor']:.2f} mdd=${c['max_drawdown']:.2f} "
        f"win={c['win_rate']:.0%} expectancy=${c['expectancy']:+.2f}"
    )
    return "\n".join(lines)


def persist_daily_report(conn: sqlite3.Connection, report: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO daily_reports (day, payload_json, generated_at) "
        "VALUES (?, ?, ?) "
        "ON CONFLICT(day) DO UPDATE SET "
        "payload_json=excluded.payload_json, generated_at=excluded.generated_at",
        (report["day"], json.dumps(report), utc_now_iso()),
    )
