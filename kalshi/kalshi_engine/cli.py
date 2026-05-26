"""CLI entry point: `python -m kalshi_engine.cli ...` or `kalshi-engine ...`."""

from __future__ import annotations

import logging

import structlog
import typer
from rich.console import Console
from rich.table import Table

from .config import get_settings
from .db import connect, init_db
from .scanner import scan
from .validation.calibration import calibration_report

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
    )
    _print_signals(res.signals)
    console.print(
        f"\n[dim]fetched={res.fetched} "
        f"filtered_out={res.filtered_out} "
        f"signals={len(res.signals)} "
        f"rejections={len(res.rejections)}[/dim]"
    )
    if dry_run:
        console.print("[yellow]dry-run: signals were still written to SQLite.[/yellow]")


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
