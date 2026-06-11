"""CLI for the copy-trade meta-allocator.

Usage:
  copy-trade init-db
  copy-trade worker [--live]
  copy-trade poll-leaderboard [--live]
  copy-trade rebalance [--live]
  copy-trade status
  copy-trade scores [--top N]
  copy-trade subscriptions
  copy-trade pnl
  copy-trade validation
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()


@app.command("init-db")
def cmd_init_db() -> None:
    """Create or migrate the copy-trade SQLite schema."""
    from .config import get_settings
    from .db import init_db
    init_db()
    console.print(f"[green]initialised[/green] {get_settings().db_path}")


@app.command("worker")
def cmd_worker(
    live: bool = typer.Option(False, "--live", help="Execute real API calls (default: dry-run)."),
) -> None:
    """Start the continuous polling + rebalance worker."""
    from .worker import start_worker
    dry_run = not live
    if live:
        console.print("[bold red]LIVE MODE: real subscriptions will be placed.[/bold red]")
        if not typer.confirm("Continue?"):
            raise typer.Exit()
    start_worker(dry_run=dry_run)


@app.command("poll-leaderboard")
def cmd_poll_leaderboard(
    live: bool = typer.Option(False, "--live"),
) -> None:
    """Run one leaderboard poll and scoring pass."""
    from .worker import run_leaderboard_poll
    run_leaderboard_poll(dry_run=not live)
    console.print("[green]done[/green]")


@app.command("rebalance")
def cmd_rebalance(
    live: bool = typer.Option(False, "--live"),
) -> None:
    """Compute allocation decisions and execute subscribe/unsubscribe."""
    from .worker import run_rebalance
    dry_run = not live
    if live:
        console.print("[bold red]LIVE: real subscriptions will change.[/bold red]")
        if not typer.confirm("Continue?"):
            raise typer.Exit()
    run_rebalance(dry_run=dry_run)
    console.print("[green]done[/green]")


@app.command("status")
def cmd_status() -> None:
    """Portfolio-level summary: equity, drawdown, active masters."""
    from .config import get_settings
    from .db import connect
    settings = get_settings()
    with connect(settings.db_path) as conn:
        snap = conn.execute(
            "SELECT * FROM portfolio_snapshots ORDER BY captured_at DESC LIMIT 1"
        ).fetchone()
        n_active = conn.execute(
            "SELECT COUNT(*) FROM subscriptions WHERE status='active'"
        ).fetchone()[0]
        safety = conn.execute(
            "SELECT * FROM safety_events WHERE resolved_at IS NULL"
        ).fetchall()

    t = Table("metric", "value")
    if snap:
        t.add_row("equity", f"${float(snap['total_capital']) + float(snap['realized_pnl']) + float(snap['unrealized_pnl']):.2f}")
        t.add_row("realized_pnl", f"${float(snap['realized_pnl']):+.2f}")
        t.add_row("unrealized_pnl", f"${float(snap['unrealized_pnl']):+.2f}")
        t.add_row("deployed", f"${float(snap['deployed_usdt']):.2f}")
        t.add_row("drawdown", f"{float(snap['drawdown_pct']):.1%}")
    t.add_row("active_masters", str(n_active))
    t.add_row("safety_stops", str(len(safety)))
    console.print(t)


@app.command("scores")
def cmd_scores(top: int = typer.Option(20, "--top")) -> None:
    """Print latest trader scores."""
    from .config import get_settings
    from .db import connect
    with connect(get_settings().db_path) as conn:
        rows = conn.execute(
            """
            SELECT ts.master_uid, m.nickname, ts.composite_score,
                   ts.sharpe_est, ts.calmar_est, ts.eligible, ts.filter_reason,
                   ts.rank_at_time
            FROM trader_scores ts JOIN masters m ON m.uid = ts.master_uid
            WHERE ts.scored_at = (SELECT MAX(scored_at) FROM trader_scores
                                  WHERE master_uid = ts.master_uid)
            ORDER BY ts.rank_at_time
            LIMIT ?
            """,
            (top,),
        ).fetchall()

    t = Table("rank", "uid", "name", "score", "sharpe", "calmar", "eligible", "reason",
              title=f"Top {top} traders (latest scores)")
    for r in rows:
        col = "green" if r["eligible"] else "red"
        t.add_row(
            str(r["rank_at_time"]),
            r["master_uid"][:12],
            (r["nickname"] or "")[:20],
            f"{r['composite_score']:.4f}",
            f"{r['sharpe_est']:.2f}" if r["sharpe_est"] else "—",
            f"{r['calmar_est']:.2f}" if r["calmar_est"] else "—",
            f"[{col}]{bool(r['eligible'])}[/{col}]",
            r["filter_reason"] or "",
        )
    console.print(t)


@app.command("subscriptions")
def cmd_subscriptions() -> None:
    """List active copy-trade subscriptions."""
    from .config import get_settings
    from .db import connect
    with connect(get_settings().db_path) as conn:
        rows = conn.execute(
            "SELECT s.*, m.nickname FROM subscriptions s "
            "JOIN masters m ON m.uid = s.master_uid WHERE s.status='active'"
        ).fetchall()

    t = Table("uid", "nickname", "platform", "allocated", "subscribed_at")
    for r in rows:
        t.add_row(
            r["master_uid"][:12],
            (r["nickname"] or "")[:20],
            r["platform"],
            f"${float(r['allocated_usdt']):.0f}",
            r["subscribed_at"][:19],
        )
    console.print(t)


@app.command("pnl")
def cmd_pnl() -> None:
    """Show PnL per subscription."""
    from .config import get_settings
    from .db import connect
    with connect(get_settings().db_path) as conn:
        rows = conn.execute(
            """
            SELECT s.master_uid, m.nickname,
                   sp.realized_pnl, sp.unrealized_pnl, sp.total_pnl,
                   sp.captured_at
            FROM subscriptions s
            JOIN masters m ON m.uid = s.master_uid
            LEFT JOIN subscription_pnl sp ON sp.subscription_id = s.id
            WHERE sp.id = (SELECT MAX(id) FROM subscription_pnl
                           WHERE subscription_id = s.id)
            ORDER BY sp.total_pnl DESC
            """
        ).fetchall()

    t = Table("uid", "name", "realized", "unrealized", "total", "as_of")
    for r in rows:
        col = "green" if (r["total_pnl"] or 0) >= 0 else "red"
        t.add_row(
            r["master_uid"][:12], (r["nickname"] or "")[:20],
            f"[{col}]${float(r['realized_pnl'] or 0):+.2f}[/{col}]",
            f"${float(r['unrealized_pnl'] or 0):+.2f}",
            f"[{col}]${float(r['total_pnl'] or 0):+.2f}[/{col}]",
            (r["captured_at"] or "")[:19],
        )
    console.print(t)


@app.command("validation")
def cmd_validation() -> None:
    """Show score prediction accuracy: did high-scored traders outperform?"""
    from .config import get_settings
    from .db import connect
    from .validation.tracker import score_prediction_accuracy
    with connect(get_settings().db_path) as conn:
        result = score_prediction_accuracy(conn)
    t = Table("metric", "value")
    for k, v in result.items():
        t.add_row(k, str(v))
    console.print(t)


if __name__ == "__main__":
    app()
