from datetime import datetime, timedelta, timezone

from kalshi_engine.risk.rules import (
    can_take_trade,
    record_risk_event,
)


def test_blocks_oversized(conn, settings):
    d = can_take_trade(conn=conn, ticker="A", proposed_size_usd=999, settings=settings)
    assert not d.allowed and d.reason == "exceeds_max_position"


def test_blocks_after_daily_stop(conn, settings):
    conn.execute(
        "INSERT INTO pnl_history (day, realized_usd) VALUES (?, ?)",
        (datetime.now(timezone.utc).date().isoformat(), -10.0),
    )
    d = can_take_trade(conn=conn, ticker="A", proposed_size_usd=1.0, settings=settings)
    assert not d.allowed and d.reason == "daily_loss_stop_hit"


def test_blocks_when_max_open_positions(conn, settings):
    now = datetime.now(timezone.utc).isoformat()
    for t in ("A", "B", "C"):
        conn.execute(
            "INSERT INTO positions (ticker, side, qty, avg_cost_cents, opened_at) "
            "VALUES (?, 'yes', 1, 40, ?)", (t, now),
        )
    d = can_take_trade(conn=conn, ticker="D", proposed_size_usd=1.0, settings=settings)
    assert not d.allowed and d.reason == "max_open_positions"


def test_blocks_position_already_open(conn, settings):
    conn.execute(
        "INSERT INTO positions (ticker, side, qty, avg_cost_cents, opened_at) "
        "VALUES ('A', 'yes', 1, 40, ?)", (datetime.now(timezone.utc).isoformat(),),
    )
    d = can_take_trade(conn=conn, ticker="A", proposed_size_usd=1.0, settings=settings)
    assert not d.allowed and d.reason == "position_already_open"


def test_blocks_during_cooldown(conn, settings):
    record_risk_event(conn, "loss", "test")
    d = can_take_trade(conn=conn, ticker="A", proposed_size_usd=1.0, settings=settings)
    assert not d.allowed and d.reason == "cooldown_after_loss"


def test_allows_clean_path(conn, settings):
    d = can_take_trade(conn=conn, ticker="A", proposed_size_usd=1.0, settings=settings)
    assert d.allowed
