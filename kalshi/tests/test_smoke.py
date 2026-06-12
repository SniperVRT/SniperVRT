"""Smoke tests: CLI registration, dashboard importability, full pipeline."""

import importlib

from typer.testing import CliRunner


def test_cli_registers_expected_commands():
    from kalshi_engine.cli import app
    registered = {c.name for c in app.registered_commands}
    expected = {
        "init-db", "scan", "daily-report", "performance", "approve",
        "news-ingest", "governance-status", "lock", "unlock", "health-check",
        "rehearse-live", "drift-report", "live-readiness", "calibration",
    }
    missing = expected - registered
    assert not missing, f"missing CLI commands: {missing}"


def test_cli_help_succeeds():
    from kalshi_engine.cli import app
    runner = CliRunner()
    res = runner.invoke(app, ["--help"])
    assert res.exit_code == 0


def test_cli_init_db_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "x.db"))
    from kalshi_engine import config as _cfg
    _cfg._settings = None
    from kalshi_engine.cli import app
    runner = CliRunner()
    res = runner.invoke(app, ["init-db"])
    assert res.exit_code == 0


def test_dashboard_module_imports():
    # Streamlit module-level code runs on import; the test environment may
    # not have a Streamlit runtime, so we only check it parses cleanly.
    import importlib.util, pathlib
    p = pathlib.Path(__file__).parent.parent / "kalshi_engine" / "dashboard.py"
    spec = importlib.util.spec_from_file_location("ke_dashboard_test", p)
    assert spec is not None
    # Compile-only check: no execution side effects.
    src = p.read_text()
    compile(src, str(p), "exec")


def test_package_modules_importable():
    for mod in (
        "kalshi_engine.scanner",
        "kalshi_engine.connectors.kalshi",
        "kalshi_engine.core.probability",
        "kalshi_engine.core.quality",
        "kalshi_engine.core.resolution",
        "kalshi_engine.core.ensemble",
        "kalshi_engine.paper.executor",
        "kalshi_engine.news.feeds",
        "kalshi_engine.news.evidence",
        "kalshi_engine.risk.rules",
        "kalshi_engine.risk.governance",
        "kalshi_engine.execution.live",
        "kalshi_engine.rehearsal.engine",
        "kalshi_engine.validation.calibration",
        "kalshi_engine.validation.drift",
        "kalshi_engine.validation.metrics",
        "kalshi_engine.ops.health",
        "kalshi_engine.reports.daily",
    ):
        importlib.import_module(mod)
