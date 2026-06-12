"""FIFO tax-lot tracker for US Form 8949 reporting."""

from __future__ import annotations

import csv
import io
import sqlite3
from datetime import datetime, timezone

from ..db import utc_now_iso


def record_deposit(conn: sqlite3.Connection, vault: str, units_usdt: float,
                    cost_basis_usdt: float | None = None) -> int:
    cb = cost_basis_usdt if cost_basis_usdt is not None else units_usdt
    cur = conn.execute(
        "INSERT INTO tax_lots (vault_address, opened_at, units_usdt, cost_basis_usdt) "
        "VALUES (?, ?, ?, ?)",
        (vault, utc_now_iso(), units_usdt, cb),
    )
    return int(cur.lastrowid)


def record_withdrawal(conn: sqlite3.Connection, vault: str,
                       units_usdt: float, proceeds_usdt: float) -> list[dict]:
    """Close lots FIFO until units_usdt covered. Returns closed-lot summaries."""
    rows = conn.execute(
        "SELECT * FROM tax_lots WHERE vault_address=? AND closed_at IS NULL "
        "ORDER BY opened_at ASC, id ASC",
        (vault,),
    ).fetchall()
    remaining = units_usdt
    avg_proceeds_per_unit = proceeds_usdt / units_usdt if units_usdt > 0 else 0.0
    closed: list[dict] = []
    now = utc_now_iso()
    for r in rows:
        if remaining <= 0:
            break
        lot_units = float(r["units_usdt"])
        take = min(lot_units, remaining)
        lot_proceeds = take * avg_proceeds_per_unit
        lot_basis = float(r["cost_basis_usdt"]) * (take / lot_units) if lot_units > 0 else 0
        opened = datetime.fromisoformat(r["opened_at"])
        closed_dt = datetime.fromisoformat(now)
        days = (closed_dt - opened).days
        gain = lot_proceeds - lot_basis
        is_long = 1 if days > 365 else 0
        if take >= lot_units - 1e-9:
            conn.execute(
                "UPDATE tax_lots SET closed_at=?, proceeds_usdt=?, "
                "realised_gain=?, holding_days=?, is_long_term=? WHERE id=?",
                (now, lot_proceeds, gain, days, is_long, r["id"]),
            )
        else:
            # Partial close: close a portion as new closed lot, reduce original.
            conn.execute(
                "UPDATE tax_lots SET units_usdt = units_usdt - ?, "
                "cost_basis_usdt = cost_basis_usdt - ? WHERE id=?",
                (take, lot_basis, r["id"]),
            )
            conn.execute(
                "INSERT INTO tax_lots (vault_address, opened_at, units_usdt, "
                "cost_basis_usdt, closed_at, proceeds_usdt, realised_gain, "
                "holding_days, is_long_term) VALUES (?,?,?,?,?,?,?,?,?)",
                (vault, r["opened_at"], take, lot_basis, now, lot_proceeds,
                 gain, days, is_long),
            )
        closed.append({"vault": vault, "units": take, "basis": lot_basis,
                       "proceeds": lot_proceeds, "gain": gain, "days": days,
                       "long_term": bool(is_long)})
        remaining -= take
    return closed


def export_8949_csv(conn: sqlite3.Connection, year: int) -> str:
    rows = conn.execute(
        "SELECT * FROM tax_lots WHERE closed_at IS NOT NULL "
        "AND substr(closed_at, 1, 4) = ?",
        (str(year),),
    ).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["description", "acquired_date", "sold_date",
                "proceeds", "basis", "gain_loss", "term"])
    for r in rows:
        w.writerow([
            f"Hyperliquid vault {r['vault_address']}",
            r["opened_at"][:10],
            r["closed_at"][:10],
            f"{float(r['proceeds_usdt'] or 0):.2f}",
            f"{float(r['cost_basis_usdt']):.2f}",
            f"{float(r['realised_gain'] or 0):.2f}",
            "long" if r["is_long_term"] else "short",
        ])
    return buf.getvalue()
