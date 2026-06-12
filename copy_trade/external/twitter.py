"""Twitter/X sentiment scraper.

Uses twscrape when available (no API key, but requires authenticated cookie
pool — set up via `copy-trade twitter-setup`). Falls back to a stub that
records "unavailable" if cookies aren't configured.

Sentiment: rule-based bull/bear scoring on a keyword lexicon. No LLM.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from dataclasses import dataclass

import structlog

from ..config import CopyTradeSettings, get_settings
from ..db import utc_now_iso

log = structlog.get_logger("copy_trade.twitter")

BULLISH = frozenset({
    "long", "buy", "bull", "bullish", "moon", "pump", "breakout", "rally",
    "uptrend", "bid", "accumulating", "gem", "calls", "send",
})
BEARISH = frozenset({
    "short", "sell", "bear", "bearish", "dump", "crash", "breakdown",
    "downtrend", "puts", "rekt", "rugged", "exit", "topped",
})


@dataclass
class TweetRecord:
    tweet_id: str
    author: str
    text: str
    created_at: str
    likes: int = 0
    retweets: int = 0


def score_text(text: str) -> float:
    """Return sentiment in [-1, +1]. Pure function; testable."""
    if not text:
        return 0.0
    tokens = {t.strip(".,!?#@:;\"'()[]").lower() for t in text.split()}
    n_pos = len(tokens & BULLISH)
    n_neg = len(tokens & BEARISH)
    total = n_pos + n_neg
    if total == 0:
        return 0.0
    return (n_pos - n_neg) / total


async def _fetch_tweets_async(handle: str, limit: int = 50) -> list[TweetRecord]:
    """Use twscrape if cookies configured; else return empty list."""
    try:
        from twscrape import API  # type: ignore
    except ImportError:
        log.warning("twscrape_not_installed")
        return []
    try:
        api = API()
        out: list[TweetRecord] = []
        # twscrape.user_tweets returns async iterator
        async for tweet in api.user_tweets(handle, limit=limit):
            out.append(TweetRecord(
                tweet_id=str(getattr(tweet, "id", "")),
                author=handle,
                text=str(getattr(tweet, "rawContent", "")
                          or getattr(tweet, "content", "") or ""),
                created_at=str(getattr(tweet, "date", "")),
                likes=int(getattr(tweet, "likeCount", 0) or 0),
                retweets=int(getattr(tweet, "retweetCount", 0) or 0),
            ))
        return out
    except Exception as e:  # noqa: BLE001
        log.warning("twscrape_fetch_failed", handle=handle, err=str(e))
        return []


def fetch_tweets(handle: str, limit: int = 50) -> list[TweetRecord]:
    """Sync wrapper around the async fetch."""
    try:
        return asyncio.run(_fetch_tweets_async(handle, limit))
    except RuntimeError:
        # already in an event loop; fall back to creating a new one
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_fetch_tweets_async(handle, limit))
        finally:
            loop.close()


def aggregate_sentiment(tweets: list[TweetRecord]) -> dict:
    if not tweets:
        return {"n": 0, "score": 0.0, "weighted": 0.0}
    scores = [score_text(t.text) for t in tweets]
    plain = sum(scores) / len(scores)
    # Weight by engagement (likes + 2 * retweets, +1 to floor)
    weights = [t.likes + 2 * t.retweets + 1 for t in tweets]
    weighted = sum(s * w for s, w in zip(scores, weights)) / max(1, sum(weights))
    return {"n": len(tweets), "score": plain, "weighted": weighted}


def poll_handles(conn: sqlite3.Connection,
                  handles: list[str],
                  *,
                  key_prefix: str = "twitter:",
                  limit: int = 50) -> dict[str, dict]:
    """Fetch tweets for each handle, score, persist to external_signals."""
    out: dict[str, dict] = {}
    for h in handles:
        tweets = fetch_tweets(h, limit=limit)
        agg = aggregate_sentiment(tweets)
        out[h] = agg
        conn.execute(
            "INSERT INTO external_signals "
            "(captured_at, source, key, score, payload_json) VALUES (?,?,?,?,?)",
            (utc_now_iso(), "twitter", f"{key_prefix}{h}",
             agg["weighted"], json.dumps(agg)),
        )
    return out
