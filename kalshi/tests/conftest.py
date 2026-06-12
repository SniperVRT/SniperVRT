"""Pytest fixtures: isolated SQLite DB + settings overrides per test."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

# Force settings to point at a temp DB before any module imports them.
@pytest.fixture(autouse=True)
def _isolate_env(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "kalshi.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("BANKROLL_USD", "100")
    monkeypatch.setenv("MAX_POSITION_USD", "5")
    monkeypatch.setenv("MAX_OPEN_POSITIONS", "3")
    monkeypatch.setenv("DAILY_LOSS_STOP_USD", "10")
    monkeypatch.setenv("WEEKLY_LOSS_STOP_USD", "30")
    monkeypatch.setenv("MIN_EDGE", "0.05")
    monkeypatch.setenv("MIN_CONFIDENCE", "0.6")
    monkeypatch.setenv("MAX_SPREAD_CENTS", "4")
    monkeypatch.setenv("MIN_VOLUME_24H", "500")
    monkeypatch.setenv("MIN_MINUTES_TO_CLOSE", "15")
    monkeypatch.setenv("MAX_MINUTES_TO_CLOSE", "43200")
    monkeypatch.setenv("COOLDOWN_MINUTES_AFTER_LOSS", "30")
    # Reset cached settings module-level singleton.
    from kalshi_engine import config as _cfg
    _cfg._settings = None
    yield


@pytest.fixture
def settings():
    from kalshi_engine.config import get_settings
    return get_settings()


@pytest.fixture
def conn(settings) -> sqlite3.Connection:
    from kalshi_engine.db import connect, init_db
    init_db(settings.db_path)
    c = connect(settings.db_path)
    yield c
    c.close()
