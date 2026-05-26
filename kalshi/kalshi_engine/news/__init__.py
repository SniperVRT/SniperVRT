"""News evidence ingestion (RSS + Atom) and per-market attachment."""
from .feeds import (
    DEFAULT_FEEDS,
    FeedItem,
    fetch_and_persist,
    parse_feed,
    register_default_feeds,
)
from .evidence import (
    attach_to_market,
    attach_recent_evidence,
    extract_keywords,
    relevance_score,
)

__all__ = [
    "DEFAULT_FEEDS", "FeedItem", "fetch_and_persist", "parse_feed",
    "register_default_feeds",
    "attach_to_market", "attach_recent_evidence",
    "extract_keywords", "relevance_score",
]
