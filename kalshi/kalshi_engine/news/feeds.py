"""RSS / Atom feed ingestion.

Pure-stdlib parser — handles both formats without adding feedparser.
Stores each item once via SHA-1 hash dedup on (source + url + title).

A small default seed of reliable mainstream / official sources is shipped;
operators add more via `register_default_feeds(conn)` or by inserting
into `feed_sources` directly.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx
import structlog

from ..config import Settings, get_settings
from ..db import utc_now_iso

log = structlog.get_logger("news.feeds")


# (name, url, reliability 0..1)
DEFAULT_FEEDS: list[tuple[str, str, float]] = [
    ("reuters_top", "https://feeds.reuters.com/reuters/topNews", 0.85),
    ("ap_top", "https://feeds.apnews.com/rss/apf-topnews", 0.85),
    ("bls_news", "https://www.bls.gov/feed/news_release.rss", 0.95),
    ("federalreserve_news", "https://www.federalreserve.gov/feeds/press_all.xml", 0.95),
    ("eia_news", "https://www.eia.gov/rss/press_rss.xml", 0.95),
    ("nhc_atlantic", "https://www.nhc.noaa.gov/index-at.xml", 0.95),
    ("cnbc_top", "https://www.cnbc.com/id/100003114/device/rss/rss.html", 0.65),
]


@dataclass
class FeedItem:
    source_name: str
    source_url: str
    title: str
    link: str
    summary: str
    published_at: datetime | None
    reliability: float

    def item_hash(self) -> str:
        h = hashlib.sha1()
        h.update(self.source_name.encode())
        h.update(self.link.encode())
        h.update(self.title.encode())
        return h.hexdigest()


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
_NS = {"atom": "http://www.w3.org/2005/Atom"}


def _strip_html(s: str | None) -> str:
    if not s:
        return ""
    return re.sub(r"<[^>]+>", "", s).strip()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_feed(*, source_name: str, source_url: str, reliability: float, body: str) -> list[FeedItem]:
    """Parse an RSS 2.0 or Atom feed body. Returns [] on malformed input."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        log.warning("feed_parse_error", source=source_name, err=str(e))
        return []

    items: list[FeedItem] = []

    # RSS 2.0
    for item in root.iter("item"):
        title = _strip_html((item.findtext("title") or "").strip())
        link = (item.findtext("link") or "").strip()
        desc = _strip_html(item.findtext("description") or "")
        pub = _parse_dt(item.findtext("pubDate"))
        if not title and not link:
            continue
        items.append(FeedItem(
            source_name=source_name, source_url=source_url,
            title=title, link=link or source_url, summary=desc,
            published_at=pub, reliability=reliability,
        ))

    # Atom
    for entry in root.findall("atom:entry", _NS):
        title = _strip_html((entry.findtext("atom:title", default="", namespaces=_NS) or "").strip())
        link_el = entry.find("atom:link", _NS)
        link = link_el.get("href") if link_el is not None else ""
        summary = _strip_html(entry.findtext("atom:summary", default="", namespaces=_NS))
        pub = _parse_dt(entry.findtext("atom:updated", namespaces=_NS)) or \
              _parse_dt(entry.findtext("atom:published", namespaces=_NS))
        if not title and not link:
            continue
        items.append(FeedItem(
            source_name=source_name, source_url=source_url,
            title=title, link=link or source_url, summary=summary,
            published_at=pub, reliability=reliability,
        ))

    return items


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def _freshness_score(published_at: datetime | None, now: datetime | None = None) -> float:
    if published_at is None:
        return 0.3
    now = now or datetime.now(timezone.utc)
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    age_hours = (now - published_at).total_seconds() / 3600.0
    if age_hours <= 1:
        return 1.0
    if age_hours >= 48:
        return 0.0
    return max(0.0, 1.0 - age_hours / 48.0)


def _persist_item(conn: sqlite3.Connection, item: FeedItem, keywords: list[str]) -> int | None:
    import json
    try:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO news_evidence (
                source_name, source_url, item_url, item_hash, title, summary,
                published_at, fetched_at, keywords_json, reliability_score, freshness_score
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                item.source_name, item.source_url, item.link, item.item_hash(),
                item.title, item.summary,
                item.published_at.isoformat() if item.published_at else None,
                utc_now_iso(),
                json.dumps(keywords),
                item.reliability,
                _freshness_score(item.published_at),
            ),
        )
    except sqlite3.IntegrityError as e:
        log.warning("persist_news_failed", err=str(e))
        return None
    if cur.rowcount == 0:
        return None
    return int(cur.lastrowid)


def register_default_feeds(conn: sqlite3.Connection) -> None:
    for name, url, rel in DEFAULT_FEEDS:
        conn.execute(
            "INSERT OR IGNORE INTO feed_sources (name, url, reliability, enabled) "
            "VALUES (?,?,?,1)",
            (name, url, rel),
        )


def fetch_and_persist(
    conn: sqlite3.Connection,
    *,
    http: httpx.Client | None = None,
    settings: Settings | None = None,
    only: list[str] | None = None,
    extractor=None,
) -> dict:
    """Fetch every enabled feed, parse, dedup, persist.

    `extractor`: optional callable(title, summary) -> list[str] for
    keyword extraction. Defaults to `extract_keywords` from `evidence`.
    """
    from .evidence import extract_keywords as _default_extract

    settings = settings or get_settings()
    extractor = extractor or _default_extract
    http = http or httpx.Client(timeout=settings.news_fetch_timeout_s,
                                headers={"User-Agent": "kalshi-engine-news/0.1"})

    rows = conn.execute(
        "SELECT id, name, url, reliability FROM feed_sources "
        "WHERE enabled = 1" + (" AND name IN (" + ",".join("?" * len(only)) + ")" if only else ""),
        tuple(only) if only else (),
    ).fetchall()

    summary = {"feeds": 0, "items_seen": 0, "items_new": 0, "errors": 0}
    for row in rows:
        fid, name, url, rel = row["id"], row["name"], row["url"], float(row["reliability"])
        summary["feeds"] += 1
        try:
            r = http.get(url)
            r.raise_for_status()
            items = parse_feed(source_name=name, source_url=url,
                               reliability=rel, body=r.text)
            limited = items[: settings.news_max_items_per_feed]
            for it in limited:
                summary["items_seen"] += 1
                kws = extractor(it.title, it.summary)
                new_id = _persist_item(conn, it, kws)
                if new_id is not None:
                    summary["items_new"] += 1
            conn.execute(
                "UPDATE feed_sources SET last_fetched_at=?, last_status='ok', last_error=NULL "
                "WHERE id=?",
                (utc_now_iso(), fid),
            )
        except Exception as e:  # noqa: BLE001
            summary["errors"] += 1
            log.warning("feed_fetch_failed", name=name, err=str(e))
            conn.execute(
                "UPDATE feed_sources SET last_fetched_at=?, last_status='error', last_error=? "
                "WHERE id=?",
                (utc_now_iso(), str(e)[:500], fid),
            )
    return summary
