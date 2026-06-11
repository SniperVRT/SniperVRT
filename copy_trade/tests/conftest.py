"""Shared test fixtures for copy-trade tests."""

from __future__ import annotations

import sqlite3
import pytest

from copy_trade.db import init_db, connect
from copy_trade.config import CopyTradeSettings


@pytest.fixture
def settings():
    return CopyTradeSettings(
        db_path=":memory:",
        bitget_api_key="test_key",
        bitget_api_secret="test_secret",
        bitget_passphrase="test_pass",
        total_capital_usdt=1000.0,
        min_track_record_days=1,   # relax for tests
        min_followers=0,
        min_sharpe=0.0,
        bybit_testnet=True,
    )


@pytest.fixture
def conn(settings, tmp_path):
    db_path = tmp_path / "test.db"
    settings.db_path = db_path
    init_db(db_path)
    c = connect(db_path)
    yield c
    c.close()
