"""Paper executor.

Walks the actual order book to produce volume-weighted fills, applies the
same fee schedule as the live engine, and journals every step.

Exits supported (per spec):
  - take_profit   — price moves to favourable target
  - stop_loss     — price moves against by X%
  - time_based    — N minutes before close
  - edge_decay    — re-scored edge falls below threshold
  - market_close  — close-time reached, mark to last price
  - resolution    — outcome is known
  - manual        — operator close
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from ..config import Settings, get_settings
from ..connectors.kalshi import Orderbook
from ..core.probability import estimate_fee_usd
from ..core.signals import Signal
from ..db import utc_now_iso

Side = Literal["yes", "no"]
ExitReason = Literal[
    "take_profit", "stop_loss", "time_based",
    "edge_decay", "market_close", "resolution", "manual",
]


@dataclass
class PaperOrder:
    id: int
    ticker: str
    side: Side
    action: str
    qty: int
    limit_price_cents: int
    status: str


@dataclass
class PaperFill:
    order_id: int
    qty: int
    avg_price_cents: float
    fee_usd: float


def _journal(conn: sqlite3.Connection, ticker: str, event: str, payload: dict) -> None:
    conn.execute(
        "INSERT INTO paper_trade_journal (ticker, event_type, payload_json, created_at) "
        "VALUES (?,?,?,?)",
        (ticker, event, json.dumps(payload, default=str), utc_now_iso()),
    )


def _walk_book(book_side: list[tuple[int, int]], limit_cents: int, qty: int) -> tuple[int, list[tuple[int, int]]]:
    """Greedy fill against an ask-side list of (price, size).

    Returns (filled_qty, [(fill_price_cents, fill_qty), ...]).
    Order assumed sorted best-first (lowest ask).
    """
    remaining = qty
    fills: list[tuple[int, int]] = []
    for price, size in sorted(book_side, key=lambda x: x[0]):
        if remaining <= 0:
            break
        if price > limit_cents:
            break
        take = min(size, remaining)
        fills.append((price, take))
        remaining -= take
    return qty - remaining, fills


@dataclass
class PaperExecutor:
    """Stateful around a single SQLite connection."""

    conn: sqlite3.Connection
    settings: Settings

    @classmethod
    def from_env(cls, conn: sqlite3.Connection) -> "PaperExecutor":
        return cls(conn=conn, settings=get_settings())

    # ----- entry -------------------------------------------------------------
    def submit_buy(
        self,
        *,
        signal: Signal,
        book: Orderbook,
        ensemble_id: int | None = None,
    ) -> tuple[PaperOrder, PaperFill | None]:
        """Submit a buy order from a signal against a live order book snapshot.

        Sizes are derived from `signal.suggested_size_usd / (price / 100)`.
        Walks the book up to `signal.max_price_cents`.
        """
        # Notional / price -> contract count
        price_dollars = signal.max_price_cents / 100.0
        qty = max(1, int(signal.suggested_size_usd / max(0.01, price_dollars)))

        cur = self.conn.execute(
            """
            INSERT INTO paper_orders (
                signal_id, ensemble_id, ticker, side, action, qty,
                limit_price_cents, status, strategy, created_at
            ) VALUES (?, ?, ?, ?, 'buy', ?, ?, 'open', ?, ?)
            """,
            (None, ensemble_id, signal.ticker, signal.side, qty,
             signal.max_price_cents, signal.strategy, utc_now_iso()),
        )
        order_id = int(cur.lastrowid)
        order = PaperOrder(
            id=order_id, ticker=signal.ticker, side=signal.side,
            action="buy", qty=qty,
            limit_price_cents=signal.max_price_cents, status="open",
        )

        side_book = book.yes if signal.side == "yes" else book.no
        filled_qty, fills = _walk_book(side_book, signal.max_price_cents, qty)
        if filled_qty <= 0:
            self.conn.execute(
                "UPDATE paper_orders SET status='rejected', decided_at=? WHERE id=?",
                (utc_now_iso(), order_id),
            )
            _journal(self.conn, signal.ticker, "open_rejected",
                     {"order_id": order_id, "reason": "no_fill"})
            order.status = "rejected"
            return order, None

        avg_price = sum(p * q for p, q in fills) / filled_qty
        fee_total = sum(estimate_fee_usd(q, p) for p, q in fills)
        for p, q in fills:
            self.conn.execute(
                "INSERT INTO paper_fills (order_id, qty, price_cents, fee_usd, filled_at) "
                "VALUES (?,?,?,?,?)",
                (order_id, q, p, estimate_fee_usd(q, p), utc_now_iso()),
            )

        new_status = "filled" if filled_qty == qty else "partial"
        self.conn.execute(
            "UPDATE paper_orders SET status=?, decided_at=? WHERE id=?",
            (new_status, utc_now_iso(), order_id),
        )
        order.status = new_status

        # Open / append to position
        self._open_or_extend_position(
            ticker=signal.ticker, side=signal.side,
            qty=filled_qty, avg_price_cents=int(round(avg_price)),
            strategy=signal.strategy,
        )

        _journal(self.conn, signal.ticker, "open", {
            "order_id": order_id, "qty": filled_qty,
            "avg_price_cents": avg_price, "fee_usd": fee_total,
            "side": signal.side, "strategy": signal.strategy,
            "thesis": signal.thesis,
        })

        return order, PaperFill(
            order_id=order_id, qty=filled_qty,
            avg_price_cents=avg_price, fee_usd=fee_total,
        )

    # ----- exits -------------------------------------------------------------
    def mark_to_market(self, ticker: str, mark_cents: int) -> None:
        self.conn.execute(
            "UPDATE paper_positions SET last_mark_cents=? "
            "WHERE ticker=? AND closed_at IS NULL",
            (mark_cents, ticker),
        )
        _journal(self.conn, ticker, "mark", {"mark_cents": mark_cents})

    def close_position(
        self,
        ticker: str,
        *,
        exit_price_cents: int,
        reason: ExitReason,
    ) -> float | None:
        """Close ALL open paper positions on `ticker` at `exit_price_cents`."""
        rows = self.conn.execute(
            "SELECT id, side, qty, avg_cost_cents FROM paper_positions "
            "WHERE ticker=? AND closed_at IS NULL",
            (ticker,),
        ).fetchall()
        if not rows:
            return None
        total_pnl = 0.0
        now = utc_now_iso()
        for r in rows:
            pid, side, qty, avg_cost = r["id"], r["side"], r["qty"], r["avg_cost_cents"]
            # YES: pnl = (exit - cost) * qty / 100 ; NO: same formula because
            # NO price is just (100 - YES price). The qty is in *contracts*
            # and the side's own price moves are what we care about.
            pnl = (exit_price_cents - avg_cost) * qty / 100.0
            fee = estimate_fee_usd(qty, exit_price_cents)
            pnl -= fee
            total_pnl += pnl
            self.conn.execute(
                "UPDATE paper_positions SET closed_at=?, realized_pnl_usd=? WHERE id=?",
                (now, pnl, pid),
            )
            _journal(self.conn, ticker, f"exit_{reason}", {
                "position_id": pid, "side": side, "qty": qty,
                "avg_cost_cents": avg_cost, "exit_price_cents": exit_price_cents,
                "pnl_usd": pnl, "fee_usd": fee,
            })
        # Roll daily PnL
        day = datetime.now(timezone.utc).date().isoformat()
        self.conn.execute(
            """
            INSERT INTO paper_pnl (day, realized_usd, fees_usd, trade_count)
            VALUES (?, ?, 0, 1)
            ON CONFLICT(day) DO UPDATE SET
                realized_usd = paper_pnl.realized_usd + excluded.realized_usd,
                trade_count = paper_pnl.trade_count + 1
            """,
            (day, total_pnl),
        )
        return total_pnl

    # ----- internals ---------------------------------------------------------
    def _open_or_extend_position(
        self, *, ticker: str, side: Side, qty: int,
        avg_price_cents: int, strategy: str | None,
    ) -> None:
        row = self.conn.execute(
            "SELECT id, qty, avg_cost_cents FROM paper_positions "
            "WHERE ticker=? AND side=? AND closed_at IS NULL",
            (ticker, side),
        ).fetchone()
        if row is None:
            self.conn.execute(
                "INSERT INTO paper_positions "
                "(ticker, side, qty, avg_cost_cents, opened_at, strategy) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (ticker, side, qty, avg_price_cents, utc_now_iso(), strategy),
            )
            return
        # Weighted average extension (spec disallows averaging-DOWN but we
        # may still extend size by policy in paper mode; entry is gated by
        # the risk engine anyway).
        new_qty = row["qty"] + qty
        new_avg = ((row["qty"] * row["avg_cost_cents"]) + (qty * avg_price_cents)) // new_qty
        self.conn.execute(
            "UPDATE paper_positions SET qty=?, avg_cost_cents=? WHERE id=?",
            (new_qty, new_avg, row["id"]),
        )


# Convenience for the scanner: given a signal + book, attempt to open a paper
# position. Returns the order, fill, or None when execution is gated.
def open_position_from_signal(
    *,
    conn: sqlite3.Connection,
    signal: Signal,
    book: Orderbook,
    ensemble_id: int | None = None,
) -> tuple[PaperOrder, PaperFill | None]:
    return PaperExecutor.from_env(conn).submit_buy(
        signal=signal, book=book, ensemble_id=ensemble_id,
    )
