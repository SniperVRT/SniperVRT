from kalshi_engine.connectors.kalshi import Orderbook
from kalshi_engine.core.signals import Signal
from kalshi_engine.paper.executor import PaperExecutor


def _signal(**o) -> Signal:
    base = dict(
        ticker="X", side="yes", strategy="news_lag",
        fair_prob=0.6, implied_prob=0.42, edge=0.18, expected_value=0.4,
        confidence=0.75, max_price_cents=44, suggested_size_usd=4.0, thesis="t",
    )
    base.update(o)
    return Signal(**base)


def test_fills_at_or_below_limit(conn, settings):
    book = Orderbook(ticker="X", yes=[(42, 50), (43, 50), (45, 50)], no=[(58, 50)])
    px = PaperExecutor(conn=conn, settings=settings)
    order, fill = px.submit_buy(signal=_signal(suggested_size_usd=2.0), book=book)
    assert fill is not None
    assert order.status in ("filled", "partial")
    assert fill.qty > 0
    # All fills must be <= 44¢
    fills = conn.execute(
        "SELECT price_cents FROM paper_fills WHERE order_id=?", (order.id,)
    ).fetchall()
    assert all(int(r[0]) <= 44 for r in fills)


def test_rejected_when_book_empty(conn, settings):
    book = Orderbook(ticker="X", yes=[], no=[])
    px = PaperExecutor(conn=conn, settings=settings)
    order, fill = px.submit_buy(signal=_signal(), book=book)
    assert fill is None
    assert order.status == "rejected"


def test_close_position_records_pnl(conn, settings):
    book = Orderbook(ticker="X", yes=[(42, 50)], no=[])
    px = PaperExecutor(conn=conn, settings=settings)
    px.submit_buy(signal=_signal(suggested_size_usd=1.0), book=book)
    pnl = px.close_position("X", exit_price_cents=55, reason="manual")
    assert pnl is not None and pnl > 0
    closed = conn.execute(
        "SELECT closed_at, realized_pnl_usd FROM paper_positions WHERE ticker='X'"
    ).fetchone()
    assert closed["closed_at"] is not None
