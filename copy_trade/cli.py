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


@app.command("testnet-roundtrip")
def cmd_testnet_roundtrip(
    amount: float = typer.Option(5.0, "--amount"),
    dry_run: bool = typer.Option(True, "--dry-run/--real"),
) -> None:
    """Hyperliquid testnet round-trip — required gate before --live."""
    from .config import get_settings
    from .db import connect
    from .validation.testnet_roundtrip import run_roundtrip
    settings = get_settings()
    with connect(settings.db_path) as conn:
        out = run_roundtrip(conn, settings, amount_usdt=amount, dry_run=dry_run)
    if out["passed"]:
        console.print(f"[green]roundtrip passed[/green] run_id={out['run_id']}")
    else:
        console.print(f"[red]roundtrip failed[/red]: {out['failure']}")
        raise typer.Exit(code=1)


@app.command("promotion-check")
def cmd_promotion_check() -> None:
    """Check whether the system is ready for --live."""
    from .config import get_settings
    from .db import connect
    from .validation.promotion import check_ready_for_live
    settings = get_settings()
    with connect(settings.db_path) as conn:
        rep = check_ready_for_live(conn, settings)
    console.print(f"[bold]{'LIVE_READY' if rep.ready else 'LIVE_LOCKED'}[/bold]")
    for b in rep.blockers:
        console.print(f"  [red]blocker:[/red] {b}")
    for k, v in rep.details.items():
        console.print(f"  {k}: {v}")
    if not rep.ready:
        raise typer.Exit(code=1)


@app.command("keystore-init")
def cmd_keystore_init() -> None:
    """Encrypt and store the private key."""
    import getpass
    from .security.keystore import init_keystore, keystore_exists
    if keystore_exists():
        if not typer.confirm("keystore exists — overwrite?"):
            raise typer.Exit()
    key = getpass.getpass("Private key (0x...): ")
    pw1 = getpass.getpass("Passphrase: ")
    pw2 = getpass.getpass("Confirm passphrase: ")
    if pw1 != pw2:
        console.print("[red]passphrases do not match[/red]"); raise typer.Exit(2)
    init_keystore(key, pw1)
    console.print("[green]keystore created[/green]")


@app.command("watchdog")
def cmd_watchdog(once: bool = typer.Option(True, "--once")) -> None:
    """Check active vaults for drawdown blowup; emit unwind decisions."""
    from .config import get_settings
    from .db import connect
    from .risk.watchdog import check_vault_drawdowns
    settings = get_settings()
    with connect(settings.db_path) as conn:
        ds = check_vault_drawdowns(conn, settings)
    if not ds:
        console.print("[green]all vaults healthy[/green]")
    for d in ds:
        console.print(f"[red]blowup[/red] uid={d.master_uid} reason={d.reason}")


@app.command("risk-snapshot")
def cmd_risk_snapshot() -> None:
    """Compute portfolio correlation, leader concentration, liquidity."""
    from .config import get_settings
    from .db import connect, transaction
    from .risk import portfolio_risk
    settings = get_settings()
    with connect(settings.db_path) as conn:
        with transaction(conn):
            out = portfolio_risk.snapshot(conn, settings)
    t = Table("metric", "value")
    t.add_row("avg_pairwise_corr", f"{out['corr']['avg']:.3f}")
    t.add_row("max_pairwise_corr", f"{out['corr']['max']:.3f}")
    t.add_row("n_pairs", str(out["corr"]["n_pairs"]))
    t.add_row("locked_usdt", f"${out['liquidity']['locked_usdt']:.0f}")
    t.add_row("unlocked_usdt", f"${out['liquidity']['unlocked_usdt']:.0f}")
    t.add_row("locked_pct", f"{out['liquidity']['locked_pct']:.1%}")
    t.add_row("engaged_lock", str(out["engage_lock"]))
    console.print(t)


@app.command("stress-test")
def cmd_stress_test() -> None:
    from .config import get_settings
    from .db import connect
    from .risk.stress import stress_test
    settings = get_settings()
    with connect(settings.db_path) as conn:
        rows = stress_test(conn, settings)
    t = Table("scenario", "loss_usdt", "drawdown_pct", "breach?")
    for r in rows:
        t.add_row(r["scenario"], f"${r['predicted_loss_usdt']:.0f}",
                  f"{r['predicted_drawdown_pct']:.1%}",
                  "[red]YES[/red]" if r["would_breach"] else "no")
    console.print(t)


@app.command("paper-rebalance")
def cmd_paper_rebalance() -> None:
    """Run rebalance in paper mode — same logic as live but no real deposits."""
    from .allocation.portfolio import compute_allocations
    from .config import get_settings
    from .db import connect, transaction
    from .paper.executor import (
        active_paper_subscriptions, execute_paper_decisions,
    )
    from .ranking.score import TraderScore
    settings = get_settings()
    with connect(settings.db_path) as conn:
        rows = conn.execute(
            """
            SELECT ts.*, ts.composite_score, ts.eligible FROM trader_scores ts
            WHERE ts.scored_at = (SELECT MAX(scored_at) FROM trader_scores
                                  WHERE master_uid = ts.master_uid)
              AND ts.eligible = 1
            ORDER BY ts.composite_score DESC
            """
        ).fetchall()
        scores = [
            TraderScore(
                master_uid=r["master_uid"], snapshot_id=r["snapshot_id"],
                sharpe_est=r["sharpe_est"], calmar_est=r["calmar_est"],
                composite=r["composite_score"], rank=i + 1,
                eligible=bool(r["eligible"]), filter_reason=r["filter_reason"],
            ) for i, r in enumerate(rows)
        ]
        current = active_paper_subscriptions(conn)
        decisions = compute_allocations(scores, current, settings)
        import json as _json
        from .db import utc_now_iso as _now
        with transaction(conn):
            counts = execute_paper_decisions(conn, decisions, settings)
            conn.execute(
                "INSERT INTO paper_rebalance_runs (ran_at, counts_json, success) "
                "VALUES (?, ?, 1)",
                (_now(), _json.dumps(counts)),
            )
    console.print(counts)


@app.command("paper-pnl")
def cmd_paper_pnl() -> None:
    """Poll real vault equities and apply to paper subscriptions."""
    from .config import get_settings
    from .db import connect
    from .paper.executor import snapshot_paper_pnl
    with connect(get_settings().db_path) as conn:
        snapshot_paper_pnl(conn)
    console.print("[green]paper PnL updated[/green]")


@app.command("backtest")
def cmd_backtest(
    capital: float = typer.Option(1000.0, "--capital"),
    rebalance_hours: int = typer.Option(24, "--rebalance-hours"),
    notes: str = typer.Option("", "--notes"),
) -> None:
    """Replay collected snapshots through the scoring + allocation logic."""
    from .backtest.engine import run_backtest
    from .config import get_settings
    from .db import connect, transaction
    settings = get_settings()
    with connect(settings.db_path) as conn:
        with transaction(conn):
            res = run_backtest(
                conn, initial_capital=capital,
                rebalance_hours=rebalance_hours, settings=settings, notes=notes,
            )
    t = Table("metric", "value")
    t.add_row("run_id", str(res.run_id))
    t.add_row("ticks", str(res.n_ticks))
    t.add_row("initial", f"${res.initial_capital:.2f}")
    t.add_row("final", f"${res.final_equity:.2f}")
    t.add_row("return", f"{res.total_return:+.1%}")
    t.add_row("sharpe", f"{res.sharpe:.2f}" if res.sharpe else "—")
    t.add_row("max_dd", f"{res.max_drawdown:.1%}")
    t.add_row("avg_alloc_count", f"{res.avg_alloc_count:.1f}")
    console.print(t)


@app.command("backtest-list")
def cmd_backtest_list() -> None:
    """List completed backtest runs."""
    from .config import get_settings
    from .db import connect
    with connect(get_settings().db_path) as conn:
        rows = conn.execute(
            "SELECT id, started_at, tick_count, total_return, sharpe, "
            "max_drawdown, notes FROM backtest_runs ORDER BY id DESC LIMIT 20"
        ).fetchall()
    t = Table("id", "started", "ticks", "return", "sharpe", "mdd", "notes")
    for r in rows:
        t.add_row(
            str(r["id"]), (r["started_at"] or "")[:19],
            str(r["tick_count"]),
            f"{r['total_return']:+.1%}" if r["total_return"] is not None else "—",
            f"{r['sharpe']:.2f}" if r["sharpe"] is not None else "—",
            f"{r['max_drawdown']:.1%}" if r["max_drawdown"] is not None else "—",
            (r["notes"] or "")[:30],
        )
    console.print(t)


@app.command("tax-report")
def cmd_tax_report(
    year: int = typer.Argument(...),
    out: str = typer.Option("tax_report.csv", "--out"),
) -> None:
    """Export realised gains as Form 8949 CSV."""
    from .config import get_settings
    from .db import connect
    from .compliance.tax_lots import export_8949_csv
    with connect(get_settings().db_path) as conn:
        csv = export_8949_csv(conn, year)
    Path = __import__("pathlib").Path
    Path(out).write_text(csv)
    console.print(f"[green]wrote[/green] {out}")


@app.command("unified-snapshot")
def cmd_unified_snapshot() -> None:
    """Aggregate Kalshi + copy-trade into unified.db snapshot."""
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
    from unified.aggregator import aggregate
    from unified.db import connect as uc, init_db as ui
    ui()
    with uc() as conn:
        out = aggregate(conn)
    t = Table("metric", "value")
    for k, v in out.items():
        t.add_row(k, str(v))
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
