"""End-to-end scanner with a stub Kalshi client.

Exercises filter → quality → resolution → strategy → ensemble → signal →
risk → optional paper-execute flow.
"""

from datetime import datetime, timedelta, timezone

from kalshi_engine.connectors.kalshi import Market, Orderbook
from kalshi_engine.scanner import scan


class FakeClient:
    def __init__(self, markets, book=None):
        self.markets = markets
        self.book = book

    def iter_markets(self, *, limit=200, max_pages=None):
        for m in self.markets:
            yield m

    def get_orderbook(self, ticker, depth=10):
        if self.book is None:
            raise RuntimeError("no book")
        return self.book


def _market(ticker, **o):
    now = datetime.now(timezone.utc)
    base = dict(
        ticker=ticker, event_ticker=None, series_ticker=None,
        title=f"Will {ticker} pass?", subtitle="", category="Economics", status="active",
        yes_bid=40, yes_ask=42, no_bid=58, no_ask=60,
        last_price=41, volume=1000, volume_24h=2000, open_interest=500, liquidity=20000,
        close_time=now + timedelta(hours=4),
        open_time=None, expected_expiration_time=None,
        rules_primary="Resolves YES if BLS CPI YoY >= 3% by 09:00 ET on 2026-06-12.",
        rules_secondary=None, settlement_source="BLS", raw={},
    )
    base.update(o)
    return Market(**base)


def _provider(strong_yes_for):
    def fn(m):
        if m.ticker == strong_yes_for:
            return {"news_signal": {"direction": "yes", "strength": 0.85, "implied_shift": 0.18}}
        return {}
    return fn


def test_scanner_emits_signal_with_quality_and_resolution(conn, settings):
    book = Orderbook(ticker="ECON-OK", yes=[(42, 200), (43, 50)], no=[(58, 200)])
    client = FakeClient([_market("ECON-OK")], book=book)
    res = scan(
        client=client, conn=conn, settings=settings,
        fetch_orderbook=True,
        evidence_provider=_provider("ECON-OK"),
        paper_execute=True,
    )
    assert res.fetched == 1
    assert len(res.signals) == 1
    # quality, resolution, ensemble all persisted
    q = conn.execute("SELECT COUNT(*) FROM market_quality").fetchone()[0]
    r = conn.execute("SELECT COUNT(*) FROM resolution_classifications").fetchone()[0]
    e = conn.execute("SELECT COUNT(*) FROM ensemble_votes").fetchone()[0]
    assert q >= 1 and r >= 1 and e >= 1
    # paper execution attempted
    orders = conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0]
    assert orders == 1


def test_scanner_rejects_subjective_market(conn, settings):
    # "approximately" is vague but not a hard subjective phrase, so the
    # basic filter passes; the resolution classifier should then drop it.
    m = _market(
        "SUBJ",
        rules_primary=(
            "Resolves YES if the perceived growth is significant in the "
            "opinion of the issuer per major news reports."
        ),
        settlement_source=None,
    )
    res = scan(client=FakeClient([m]), conn=conn, settings=settings, fetch_orderbook=False)
    # Either path is acceptable — both indicate the market was correctly rejected.
    rejected_for_subjectivity = (
        res.classified_rejects >= 1
        or any(r.reason in ("rules_vague", "rules_missing") for r in res.rejections)
        or any("REJECT_SUBJECTIVE" in r.reason for r in res.rejections)
    )
    assert rejected_for_subjectivity, res


def test_scanner_records_ingestion_run(conn, settings):
    res = scan(client=FakeClient([_market("X")]), conn=conn, settings=settings)
    row = conn.execute(
        "SELECT pages_fetched, markets_fetched, signals_emitted FROM ingestion_runs"
    ).fetchone()
    assert row is not None
    assert int(row["markets_fetched"]) >= 1
