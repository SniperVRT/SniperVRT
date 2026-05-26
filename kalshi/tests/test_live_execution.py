from kalshi_engine.connectors.kalshi import Orderbook
from kalshi_engine.core.signals import Signal
from kalshi_engine.execution.live import LiveExecutionAdapter, new_client_order_id
from kalshi_engine.risk import governance as gov


def _signal(**o) -> Signal:
    base = dict(
        ticker="X", side="yes", strategy="ensemble",
        fair_prob=0.6, implied_prob=0.42, edge=0.18, expected_value=0.4,
        confidence=0.8, max_price_cents=44, suggested_size_usd=2.0, thesis="t",
    )
    base.update(o)
    return Signal(**base)


def _book() -> Orderbook:
    return Orderbook(ticker="X", yes=[(42, 100)], no=[(58, 100)])


def test_client_order_id_unique():
    a, b = new_client_order_id(), new_client_order_id()
    assert a != b
    assert a.startswith("ke-")


def test_preview_persists_dry_run(conn, settings):
    a = LiveExecutionAdapter(conn=conn, settings=settings)
    p = a.preview(signal=_signal(), book=_book())
    assert p.dry_run is True
    row = conn.execute(
        "SELECT status, dry_run FROM live_orders WHERE client_order_id=?",
        (p.client_order_id,),
    ).fetchone()
    assert row["status"] == "preview" and int(row["dry_run"]) == 1


def test_submit_blocked_without_approval(conn, settings):
    a = LiveExecutionAdapter(conn=conn, settings=settings)
    sig = _signal()
    p = a.preview(signal=sig, book=_book())
    res = a.submit(signal=sig, preview=p)
    assert res.dry_run and res.status == "cancelled"


def test_duplicate_order_engages_lock(conn, settings):
    a = LiveExecutionAdapter(conn=conn, settings=settings)
    sig = _signal()
    p1 = a.preview(signal=sig, book=_book())
    conn.execute(
        "UPDATE live_orders SET status='submitted' WHERE client_order_id=?",
        (p1.client_order_id,),
    )
    p2 = a.preview(signal=sig, book=_book())
    res = a.submit(signal=sig, preview=p2)
    assert res.status == "rejected" and res.error == "duplicate_order"
    assert gov.is_locked(conn)
    gov.release_lock(conn, gov.LOCK_DUPLICATE_ORDER)


def test_cancel(conn, settings):
    a = LiveExecutionAdapter(conn=conn, settings=settings)
    p = a.preview(signal=_signal(), book=_book())
    assert a.cancel(p.client_order_id) is True
    row = conn.execute(
        "SELECT status FROM live_orders WHERE client_order_id=?",
        (p.client_order_id,),
    ).fetchone()
    assert row["status"] == "cancelled"


def test_reconcile_records_fills(conn, settings):
    a = LiveExecutionAdapter(conn=conn, settings=settings)
    sig = _signal()
    p = a.preview(signal=sig, book=_book())
    conn.execute(
        "UPDATE live_orders SET status='submitted' WHERE client_order_id=?",
        (p.client_order_id,),
    )
    a.reconcile_order(p.client_order_id, external_fills=[
        {"qty": p.qty, "price_cents": 42, "fee_usd": 0.01},
    ])
    row = conn.execute(
        "SELECT status, filled_qty, actual_avg_cents FROM live_orders WHERE client_order_id=?",
        (p.client_order_id,),
    ).fetchone()
    assert row["status"] == "filled"
    assert int(row["filled_qty"]) == p.qty
    assert abs(float(row["actual_avg_cents"]) - 42) < 1e-9


def test_reconcile_unknown_id_engages_lock(conn, settings):
    a = LiveExecutionAdapter(conn=conn, settings=settings)
    a.reconcile_order("bogus-id", external_fills=[{"qty": 1, "price_cents": 42}])
    assert gov.is_locked(conn)
    gov.release_lock(conn, gov.LOCK_RECONCILE_FAIL)


def test_unapproved_signal_can_never_progress_to_live(conn, settings, monkeypatch):
    # Even if dry_run is OFF and live is ON, missing approval must block.
    monkeypatch.setattr(settings, "live_dry_run", False)
    monkeypatch.setattr(settings, "live_enabled", True)
    a = LiveExecutionAdapter(conn=conn, settings=settings)
    sig = _signal()
    p = a.preview(signal=sig, book=_book())
    res = a.submit(signal=sig, preview=p)
    assert res.dry_run and res.status == "cancelled"
    assert "manual_approval_required" in (res.error or "")
