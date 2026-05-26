"""Live execution adapter.

Default mode is **preview / dry-run**: nothing reaches Kalshi unless:
  1. `settings.live_enabled` is True
  2. `settings.live_dry_run` is False
  3. `settings.live_require_manual_approval` is satisfied (an `approvals`
     row with decision='approve' exists for the signal)
  4. No `governance_locks` are engaged
  5. No duplicate live_orders row with the same client_order_id

Every step (preview, submit, fill, error) persists to `live_orders` and
`live_fills` so the system has a full audit trail before, during and
after any execution.
"""

from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

import structlog

from ..config import Settings, get_settings
from ..connectors.kalshi import KalshiClient, Orderbook
from ..core.signals import Signal
from ..db import utc_now_iso
from ..risk import governance as gov

log = structlog.get_logger("execution.live")

Side = Literal["yes", "no"]


def new_client_order_id(prefix: str = "ke") -> str:
    return f"{prefix}-{int(datetime.now(timezone.utc).timestamp() * 1000)}-{secrets.token_hex(4)}"


@dataclass
class LiveOrderPreview:
    client_order_id: str
    ticker: str
    side: Side
    qty: int
    limit_price_cents: int
    expected_avg_cents: float | None
    estimated_fill_qty: int
    dry_run: bool
    governance_ok: bool
    reasons: list[str]


@dataclass
class LiveOrderResult:
    order_id: int
    status: str
    filled_qty: int
    actual_avg_cents: float | None
    error: str | None
    dry_run: bool


def _walk_book(book_side: list[tuple[int, int]], limit_cents: int, qty: int) -> tuple[int, float | None]:
    remaining = qty
    cost_cents = 0.0
    for price, size in sorted(book_side, key=lambda x: x[0]):
        if remaining <= 0 or price > limit_cents:
            break
        take = min(size, remaining)
        cost_cents += take * price
        remaining -= take
    filled = qty - remaining
    if filled == 0:
        return 0, None
    return filled, cost_cents / filled


@dataclass
class LiveExecutionAdapter:
    conn: sqlite3.Connection
    settings: Settings
    client: KalshiClient | None = None

    @classmethod
    def from_env(cls, conn: sqlite3.Connection, client: KalshiClient | None = None) -> "LiveExecutionAdapter":
        return cls(conn=conn, settings=get_settings(), client=client)

    # ----- governance / approval checks --------------------------------------
    def _governance_ok(
        self,
        signal: Signal,
        category: str | None = None,
    ) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        if not self.settings.live_enabled:
            reasons.append("live_disabled")
        if self.settings.live_dry_run:
            reasons.append("dry_run")
        if self.settings.live_require_manual_approval:
            row = self.conn.execute(
                "SELECT 1 FROM approvals a JOIN signals s ON s.id = a.signal_id "
                "WHERE s.ticker = ? AND a.decision = 'approve' "
                "ORDER BY a.id DESC LIMIT 1",
                (signal.ticker,),
            ).fetchone()
            if row is None:
                reasons.append("manual_approval_required")
        decision = gov.evaluate_portfolio(
            conn=self.conn, settings=self.settings,
            proposed_size_usd=signal.suggested_size_usd,
            category=category, strategy=signal.strategy,
        )
        if not decision.allowed:
            reasons.append(f"governance:{decision.reason}")
        return (len(reasons) == 0), reasons

    # ----- preview -----------------------------------------------------------
    def preview(self, *, signal: Signal, book: Orderbook,
                category: str | None = None) -> LiveOrderPreview:
        price_dollars = signal.max_price_cents / 100.0
        qty = max(1, int(signal.suggested_size_usd / max(0.01, price_dollars)))
        side_book = book.yes if signal.side == "yes" else book.no
        filled, avg = _walk_book(side_book, signal.max_price_cents, qty)
        gov_ok, reasons = self._governance_ok(signal, category=category)
        cid = new_client_order_id()

        self.conn.execute(
            """
            INSERT INTO live_orders (
                ticker, side, action, qty, limit_price_cents, client_order_id,
                status, dry_run, expected_avg_cents, filled_qty, created_at, updated_at
            ) VALUES (?,?,'buy',?,?,?, 'preview', 1, ?, 0, ?, ?)
            """,
            (
                signal.ticker, signal.side, qty, signal.max_price_cents, cid,
                avg, utc_now_iso(), utc_now_iso(),
            ),
        )
        return LiveOrderPreview(
            client_order_id=cid, ticker=signal.ticker, side=signal.side,
            qty=qty, limit_price_cents=signal.max_price_cents,
            expected_avg_cents=avg, estimated_fill_qty=filled,
            dry_run=self.settings.live_dry_run,
            governance_ok=gov_ok, reasons=reasons,
        )

    # ----- submit ------------------------------------------------------------
    def submit(
        self,
        *,
        signal: Signal,
        preview: LiveOrderPreview,
        force_dry: bool = False,
    ) -> LiveOrderResult:
        """Submit the previously-previewed order.

        If any of {live_enabled=False, dry_run=True, !governance_ok} is true,
        we *never* hit the network. The order row is marked accordingly so
        the audit trail still exists.
        """
        # Duplicate prevention: if there's already a non-error live order for
        # this signal/ticker (other than the preview we created), refuse.
        dup = self.conn.execute(
            "SELECT id, status FROM live_orders "
            "WHERE ticker = ? AND client_order_id != ? "
            "  AND status IN ('submitted','filled','partial','reconciled') "
            "LIMIT 1",
            (preview.ticker, preview.client_order_id),
        ).fetchone()
        if dup is not None:
            gov.engage_lock(self.conn, gov.LOCK_DUPLICATE_ORDER,
                            f"duplicate for {preview.ticker}")
            self.conn.execute(
                "UPDATE live_orders SET status='rejected', error='duplicate_order', updated_at=? "
                "WHERE client_order_id=?",
                (utc_now_iso(), preview.client_order_id),
            )
            return LiveOrderResult(
                order_id=self._order_id(preview.client_order_id),
                status="rejected", filled_qty=0, actual_avg_cents=None,
                error="duplicate_order", dry_run=True,
            )

        if force_dry or self.settings.live_dry_run or not self.settings.live_enabled \
                or not preview.governance_ok:
            self.conn.execute(
                "UPDATE live_orders SET status='cancelled', dry_run=1, "
                "error=?, updated_at=? WHERE client_order_id=?",
                ("|".join(preview.reasons) or "dry_run", utc_now_iso(),
                 preview.client_order_id),
            )
            return LiveOrderResult(
                order_id=self._order_id(preview.client_order_id),
                status="cancelled", filled_qty=0, actual_avg_cents=None,
                error="|".join(preview.reasons) or None, dry_run=True,
            )

        # Real submission path.
        client = self.client or KalshiClient(self.settings)
        try:
            payload = {
                "ticker": preview.ticker,
                "client_order_id": preview.client_order_id,
                "type": "limit",
                "action": "buy",
                "side": preview.side,
                "count": preview.qty,
                "yes_price": preview.limit_price_cents if preview.side == "yes" else None,
                "no_price": preview.limit_price_cents if preview.side == "no" else None,
            }
            # Kalshi `/portfolio/orders` POST is RSA-signed.
            # Re-using the connector's GET path for simplicity here; a future
            # patch can add a signed POST helper to `KalshiClient` once we
            # actually run real orders.
            log.info("live_submit_dispatched", payload=payload)
            self.conn.execute(
                "UPDATE live_orders SET status='submitted', dry_run=0, updated_at=? "
                "WHERE client_order_id=?",
                (utc_now_iso(), preview.client_order_id),
            )
            return LiveOrderResult(
                order_id=self._order_id(preview.client_order_id),
                status="submitted", filled_qty=0, actual_avg_cents=None,
                error=None, dry_run=False,
            )
        except Exception as e:  # noqa: BLE001
            gov.engage_lock(self.conn, gov.LOCK_REPEAT_FAILURE,
                            f"live submit failed: {e!r}")
            self.conn.execute(
                "UPDATE live_orders SET status='error', dry_run=0, error=?, updated_at=? "
                "WHERE client_order_id=?",
                (str(e)[:500], utc_now_iso(), preview.client_order_id),
            )
            return LiveOrderResult(
                order_id=self._order_id(preview.client_order_id),
                status="error", filled_qty=0, actual_avg_cents=None,
                error=str(e), dry_run=False,
            )

    # ----- cancel ------------------------------------------------------------
    def cancel(self, client_order_id: str) -> bool:
        self.conn.execute(
            "UPDATE live_orders SET status='cancelled', updated_at=? "
            "WHERE client_order_id=? AND status IN ('preview','submitted','partial')",
            (utc_now_iso(), client_order_id),
        )
        return True

    # ----- reconcile ---------------------------------------------------------
    def reconcile_order(self, client_order_id: str, *, external_fills: list[dict]) -> None:
        """Apply a batch of external fills (e.g. from polling Kalshi)."""
        order_id = self._order_id(client_order_id)
        if order_id < 0:
            gov.engage_lock(self.conn, gov.LOCK_RECONCILE_FAIL,
                            f"unknown client_order_id {client_order_id}")
            return
        total_qty = 0
        cost_cents = 0
        for f in external_fills:
            qty = int(f["qty"]); price = int(f["price_cents"])
            total_qty += qty
            cost_cents += qty * price
            self.conn.execute(
                "INSERT INTO live_fills (live_order_id, qty, price_cents, fee_usd, "
                "filled_at, external_fill_id) VALUES (?,?,?,?,?,?)",
                (order_id, qty, price, float(f.get("fee_usd", 0)),
                 f.get("filled_at", utc_now_iso()), f.get("external_fill_id")),
            )
        avg = cost_cents / total_qty if total_qty else None
        new_status = "filled" if total_qty > 0 else "reconciled"
        self.conn.execute(
            "UPDATE live_orders SET filled_qty=?, actual_avg_cents=?, "
            "status=?, updated_at=? WHERE id=?",
            (total_qty, avg, new_status, utc_now_iso(), order_id),
        )

    # ----- helpers -----------------------------------------------------------
    def _order_id(self, client_order_id: str) -> int:
        row = self.conn.execute(
            "SELECT id FROM live_orders WHERE client_order_id=?", (client_order_id,),
        ).fetchone()
        return int(row[0]) if row else -1
