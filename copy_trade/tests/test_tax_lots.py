"""Tests for FIFO tax-lot ledger."""

from __future__ import annotations

import pytest
from copy_trade.compliance.tax_lots import (
    record_deposit, record_withdrawal, export_8949_csv,
)


def test_deposit_creates_lot(conn):
    lot_id = record_deposit(conn, "vault1", 100.0)
    row = conn.execute("SELECT * FROM tax_lots WHERE id=?", (lot_id,)).fetchone()
    assert row["closed_at"] is None
    assert float(row["units_usdt"]) == 100.0


def test_fifo_close_partial(conn):
    record_deposit(conn, "v1", 100.0, cost_basis_usdt=100.0)
    record_deposit(conn, "v1", 200.0, cost_basis_usdt=200.0)
    closed = record_withdrawal(conn, "v1", 150.0, 180.0)
    # First lot fully closed, second partially closed
    assert len(closed) == 2
    total_basis = sum(c["basis"] for c in closed)
    assert abs(total_basis - 150.0) < 0.01


def test_8949_export_format(conn):
    record_deposit(conn, "v1", 100.0, cost_basis_usdt=100.0)
    record_withdrawal(conn, "v1", 100.0, 110.0)
    csv = export_8949_csv(conn, 2026)
    assert "description" in csv
    assert "v1" in csv or "Hyperliquid" in csv
