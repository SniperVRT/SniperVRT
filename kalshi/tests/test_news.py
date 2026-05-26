from datetime import datetime, timezone

from kalshi_engine.news.evidence import (
    attach_to_market, extract_keywords, relevance_score,
)
from kalshi_engine.news.feeds import (
    DEFAULT_FEEDS, FeedItem, parse_feed, register_default_feeds, _persist_item,
)


RSS_OK = """<?xml version='1.0'?>
<rss version='2.0'><channel>
  <title>x</title>
  <item>
    <title>BLS CPI rose 3.1% YoY</title>
    <link>https://example.com/a</link>
    <description>Consumer Price Index official BLS release</description>
    <pubDate>Mon, 26 May 2026 12:00:00 GMT</pubDate>
  </item>
  <item>
    <title>BLS CPI rose 3.1% YoY</title>
    <link>https://example.com/a</link>
    <description>Same item to test dedup</description>
    <pubDate>Mon, 26 May 2026 12:00:00 GMT</pubDate>
  </item>
</channel></rss>"""

ATOM_OK = """<?xml version='1.0'?>
<feed xmlns='http://www.w3.org/2005/Atom'>
  <title>x</title>
  <entry>
    <title>Federal Reserve raises rates</title>
    <link href='https://example.com/fed'/>
    <summary>Fed announces rate change</summary>
    <updated>2026-05-26T12:00:00Z</updated>
  </entry>
</feed>"""

RSS_BAD = "<rss><channel><item><title>no link</title></item></channel></rss>"
RSS_MALFORMED = "<<<not xml"


def test_parse_feed_rss():
    items = parse_feed(source_name="x", source_url="u", reliability=0.9, body=RSS_OK)
    assert len(items) == 2  # parser yields both; dedup happens at persist
    assert "BLS" in items[0].title


def test_parse_feed_atom():
    items = parse_feed(source_name="x", source_url="u", reliability=0.9, body=ATOM_OK)
    assert len(items) == 1
    assert "Federal Reserve" in items[0].title


def test_parse_feed_malformed_returns_empty():
    assert parse_feed(source_name="x", source_url="u", reliability=0.9, body=RSS_MALFORMED) == []


def test_dedup_on_persist(conn):
    items = parse_feed(source_name="x", source_url="u", reliability=0.9, body=RSS_OK)
    first = _persist_item(conn, items[0], ["bls", "cpi"])
    second = _persist_item(conn, items[1], ["bls", "cpi"])
    assert first is not None
    assert second is None  # duplicate hash


def test_extract_keywords_drops_stopwords():
    kws = extract_keywords("The Federal Reserve will not change rates", "")
    assert "federal" in kws and "reserve" in kws and "rates" in kws
    assert "the" not in kws and "will" not in kws and "not" not in kws


def test_relevance_score_zero_when_no_overlap():
    s, m = relevance_score(
        market_tokens={"weather", "rain"},
        evidence_keywords=["federal", "reserve"],
        reliability=0.9, freshness=0.9,
    )
    assert s == 0 and m == []


def test_relevance_score_rewards_overlap_freshness_reliability():
    s_hi, _ = relevance_score(
        market_tokens={"cpi", "bls"}, evidence_keywords=["bls", "cpi", "inflation"],
        reliability=0.95, freshness=1.0,
    )
    s_lo, _ = relevance_score(
        market_tokens={"cpi", "bls"}, evidence_keywords=["bls", "cpi", "inflation"],
        reliability=0.1, freshness=0.0,
    )
    assert s_hi > s_lo


def test_attach_to_market(conn):
    item = FeedItem(
        source_name="bls_news", source_url="u", title="CPI YoY rose",
        link="https://example.com/x", summary="BLS official release",
        published_at=datetime.now(timezone.utc), reliability=0.95,
    )
    _persist_item(conn, item, ["cpi", "bls", "inflation"])
    conn.execute(
        "INSERT INTO markets (ticker, title, subtitle, category, status, "
        "first_seen_at, last_seen_at) VALUES "
        "('CPI-Y','Will BLS CPI YoY be >= 3%?','', 'Economics','active','t','t')"
    )
    n = attach_to_market(conn, ticker="CPI-Y",
                        title="Will BLS CPI YoY be >= 3%?",
                        subtitle="", category="Economics",
                        min_score=0.0)
    assert n >= 1


def test_register_default_feeds_idempotent(conn):
    register_default_feeds(conn)
    register_default_feeds(conn)
    n = conn.execute("SELECT COUNT(*) FROM feed_sources").fetchone()[0]
    assert n == len(DEFAULT_FEEDS)
