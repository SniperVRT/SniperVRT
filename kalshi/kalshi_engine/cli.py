"""CLI entry point: `python -m kalshi_engine.cli ...` or `kalshi-engine ...`."""

from __future__ import annotations

import logging

import structlog
import typer
from rich.console import Console
from rich.table import Table

from .config import get_settings
from .db import connect, init_db
from .news.evidence import attach_recent_evidence
from .news.feeds import fetch_and_persist, register_default_feeds
from .ops.health import run_health_checks
from .rehearsal.engine import run_rehearsal
from .reports.daily import (
    build_daily_report,
    persist_daily_report,
    render_daily_report_text,
)
from .risk import governance as gov
from .scanner import scan
from .validation.calibration import calibration_report
from .validation.drift import summary as drift_summary
from .validation.metrics import performance_report

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()


def _configure_logging() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper(), format="%(message)s")
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )


@app.command("init-db")
def cmd_init_db() -> None:
    """Create the SQLite schema."""
    _configure_logging()
    init_db()
    settings = get_settings()
    console.print(f"[green]initialised[/green] {settings.db_path}")


@app.command("scan")
def cmd_scan(
    limit: int = typer.Option(200, help="Markets per page."),
    pages: int = typer.Option(1, help="Max pages to pull (set 0 for unlimited)."),
    orderbook: bool = typer.Option(False, "--orderbook/--no-orderbook"),
    paper: bool = typer.Option(False, "--paper", help="Open paper orders on accepted signals."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Skip persisting signals."),
) -> None:
    """Run one scan pass and print the ranked signals."""
    _configure_logging()
    settings = get_settings()
    init_db()  # idempotent
    res = scan(
        settings=settings,
        limit=limit,
        max_pages=None if pages == 0 else pages,
        fetch_orderbook=orderbook,
        paper_execute=paper,
    )
    _print_signals(res.signals)
    console.print(
        f"\n[dim]fetched={res.fetched} "
        f"filtered_out={res.filtered_out} "
        f"resolution_rejects={res.classified_rejects} "
        f"signals={len(res.signals)} "
        f"rejections={len(res.rejections)} "
        f"paper_orders={res.paper_orders_placed}[/dim]"
    )
    if dry_run:
        console.print("[yellow]dry-run: signals were still written to SQLite.[/yellow]")


@app.command("daily-report")
def cmd_daily_report(write: bool = typer.Option(True, "--write/--no-write")) -> None:
    """Build the rolling 24h operational report and print it."""
    _configure_logging()
    with connect() as conn:
        rep = build_daily_report(conn)
        if write:
            persist_daily_report(conn, rep)
    console.print(render_daily_report_text(rep))


@app.command("performance")
def cmd_performance(strategy: str = typer.Option(None)) -> None:
    """Print profit factor, drawdown, Brier, log loss, etc."""
    _configure_logging()
    with connect() as conn:
        rep = performance_report(conn, strategy=strategy)
    t = Table("metric", "value")
    t.add_row("n", str(rep.n))
    t.add_row("brier", f"{rep.brier:.4f}")
    t.add_row("log_loss", f"{rep.log_loss:.4f}")
    t.add_row("avg_edge", f"{rep.avg_edge:+.4f}")
    t.add_row("avg_realized", f"{rep.avg_realized:+.4f}")
    t.add_row("profit_factor", f"{rep.profit_factor:.2f}")
    t.add_row("max_drawdown", f"${rep.max_drawdown:.2f}")
    t.add_row("win_rate", f"{rep.win_rate:.0%}")
    t.add_row("expectancy", f"${rep.expectancy:+.4f}")
    console.print(t)


@app.command("approve")
def cmd_approve(
    signal_id: int = typer.Argument(...),
    decision: str = typer.Argument(..., help="approve | reject | watchlist"),
    notes: str = typer.Option(""),
) -> None:
    """Log a manual approval decision for a signal."""
    _configure_logging()
    if decision not in ("approve", "reject", "watchlist"):
        console.print("[red]decision must be approve | reject | watchlist[/red]")
        raise typer.Exit(code=2)
    from .db import utc_now_iso
    with connect() as conn:
        conn.execute(
            "INSERT INTO approvals (signal_id, decision, notes, created_at) VALUES (?,?,?,?)",
            (signal_id, decision, notes, utc_now_iso()),
        )
        if decision == "approve":
            conn.execute(
                "UPDATE signals SET status='approved', decided_at=? WHERE id=?",
                (utc_now_iso(), signal_id),
            )
        elif decision == "reject":
            conn.execute(
                "UPDATE signals SET status='rejected', decided_at=? WHERE id=?",
                (utc_now_iso(), signal_id),
            )
    console.print(f"[green]{decision}[/green] signal_id={signal_id}")


@app.command("news-ingest")
def cmd_news_ingest(
    register: bool = typer.Option(True, "--register/--no-register",
                                  help="Seed default feed list first."),
    only: str = typer.Option(None, help="Comma-separated feed names to restrict to."),
) -> None:
    """Fetch all enabled RSS/Atom feeds and persist new evidence items."""
    _configure_logging()
    init_db()
    with connect() as conn:
        if register:
            register_default_feeds(conn)
        only_list = [s.strip() for s in only.split(",")] if only else None
        summary = fetch_and_persist(conn, only=only_list)
        attach = attach_recent_evidence(conn)
    console.print(summary)
    console.print(attach)


@app.command("governance-status")
def cmd_governance_status() -> None:
    _configure_logging()
    settings = get_settings()
    with connect() as conn:
        state = gov.compute_state(conn, settings)
        locks = gov.active_locks(conn)
    t = Table("metric", "value")
    t.add_row("bankroll", f"${state.bankroll_usd:.2f}")
    t.add_row("realized_pnl", f"${state.realized_pnl_usd:+.2f}")
    t.add_row("unrealized_pnl", f"${state.unrealized_pnl_usd:+.2f}")
    t.add_row("open_exposure", f"${state.open_exposure_usd:.2f}")
    t.add_row("open_positions", str(state.open_positions))
    t.add_row("peak_equity", f"${state.peak_equity_usd:.2f}")
    t.add_row("drawdown", f"${state.drawdown_usd:.2f}")
    t.add_row("consecutive_losses", str(state.consecutive_losses))
    t.add_row("category_exposure", str(state.category_exposure))
    t.add_row("strategy_exposure", str(state.strategy_exposure))
    t.add_row("locks", ", ".join(n for n, _ in locks) or "[]")
    console.print(t)


@app.command("lock")
def cmd_lock(name: str = typer.Argument(...), reason: str = typer.Option("manual")) -> None:
    _configure_logging()
    with connect() as conn:
        gov.engage_lock(conn, name, reason)
    console.print(f"[yellow]engaged[/yellow] lock={name} reason={reason}")


@app.command("unlock")
def cmd_unlock(name: str = typer.Argument(...)) -> None:
    _configure_logging()
    with connect() as conn:
        gov.release_lock(conn, name)
    console.print(f"[green]released[/green] lock={name}")


@app.command("health-check")
def cmd_health_check() -> None:
    _configure_logging()
    with connect() as conn:
        rep = run_health_checks(conn)
    t = Table("component", "status", "detail", title=f"Health — overall: {rep.overall}")
    for c in rep.checks:
        colour = {"ok": "green", "warn": "yellow", "fail": "red"}[c.status]
        t.add_row(c.component, f"[{colour}]{c.status}[/{colour}]", c.detail)
    console.print(t)


@app.command("rehearse-live")
def cmd_rehearse_live() -> None:
    """Run the full live-workflow rehearsal (never touches real money)."""
    _configure_logging()
    init_db()
    with connect() as conn:
        rep = run_rehearsal(conn)
    t = Table("scenario", "passed", "detail")
    for s in rep.scenarios:
        colour = "green" if s.passed else "red"
        t.add_row(s.name, f"[{colour}]{s.passed}[/{colour}]",
                  ", ".join(f"{k}={v!r}" for k, v in list(s.detail.items())[:3]))
    console.print(t)
    console.print(f"[bold]{rep.passed}/{rep.passed + rep.failed} passed[/bold]")


@app.command("drift-report")
def cmd_drift_report() -> None:
    _configure_logging()
    with connect() as conn:
        s = drift_summary(conn)
    t = Table("metric", "value")
    for k, v in s.items():
        t.add_row(k, f"{v}")
    console.print(t)


@app.command("live-readiness")
def cmd_live_readiness() -> None:
    """Aggregate readiness: tests aren't checked here (use pytest separately)."""
    _configure_logging()
    settings = get_settings()
    with connect() as conn:
        health = run_health_checks(conn)
        locks = gov.active_locks(conn)
        drift = drift_summary(conn)
        rep = build_daily_report(conn)
    ready = (
        health.overall == "ok"
        and not locks
        and settings.live_enabled
        and not settings.live_dry_run
        and settings.live_require_manual_approval
    )
    console.print(f"[bold]status:[/bold] {'LIVE_READY' if ready else 'LIVE_LOCKED'}")
    console.print(f"  health: {health.overall}")
    console.print(f"  locks: {[n for n,_ in locks] or 'none'}")
    console.print(f"  drift n={drift['n']} avg_slip={drift['avg_slippage_cents']:+.2f}c")
    console.print(f"  signals today: {rep['signals_total']}  paper open: {rep['open_paper_positions']}")
    console.print(f"  exec: live_enabled={settings.live_enabled} dry_run={settings.live_dry_run}")


@app.command("calibration")
def cmd_calibration(strategy: str = typer.Option(None)) -> None:
    """Print calibration buckets + Brier score."""
    _configure_logging()
    with connect() as conn:
        rep = calibration_report(conn, strategy=strategy)
    console.print(f"[bold]Calibration[/bold] strategy={rep.strategy} n={rep.n} brier={rep.brier:.4f}")
    t = Table("bucket", "n", "avg_predicted", "realized_rate")
    for b in rep.buckets:
        t.add_row(f"{b.bucket_lo:.0%}–{b.bucket_hi:.0%}", str(b.n),
                  f"{b.avg_predicted:.0%}", f"{b.realized_rate:.0%}")
    console.print(t)


def _print_signals(signals) -> None:
    t = Table(
        "ticker", "side", "strategy", "fair", "implied", "edge",
        "EV/$", "conf", "size $", "max ¢", title="Ranked signals",
    )
    for s in signals:
        t.add_row(
            s.ticker, s.side, s.strategy,
            f"{s.fair_prob:.0%}", f"{s.implied_prob:.0%}",
            f"{s.edge:+.1%}", f"{s.expected_value:+.2f}",
            f"{s.confidence:.2f}", f"{s.suggested_size_usd:.2f}",
            str(s.max_price_cents),
        )
    console.print(t)


if __name__ == "__main__":
    app()
