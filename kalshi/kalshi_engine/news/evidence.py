"""Evidence keyword matching + attachment to markets.

Deterministic only — no LLM. Builds a tokenised set from each market's
title/subtitle/category and scores each news_evidence item by overlap
+ recency + source reliability.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone

from ..db import utc_now_iso

# Small inline English stop list — enough to keep relevance noise down
# without pulling NLTK or sklearn.
_STOP = frozenset("""
the be to of and a in that have i it for not on with he as you do at this but
his by from they we say her she or an will my one all would there their what so
up out if about who get which go me when make can like time no just him know
take person into year your good some could them see other than then now look
only come its over think also back after use two how our work first well way
even new want because any these give day most us is are was were has had does
also amid says said reports report week today yesterday tomorrow am pm et utc
will would should could may might
""".split())

_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")


def extract_keywords(title: str | None, summary: str | None) -> list[str]:
    """Lowercase tokens with stopwords/short tokens removed; dedup, preserves order."""
    text = " ".join(s for s in (title, summary) if s)
    seen: set[str] = set()
    out: list[str] = []
    for tok in _TOKEN_RE.findall(text.lower()):
        if len(tok) < 3 or tok in _STOP or tok.isdigit():
            continue
        if tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out


def _market_tokens(title: str | None, subtitle: str | None, category: str | None) -> set[str]:
    text = " ".join(s for s in (title, subtitle, category) if s)
    return {
        tok for tok in _TOKEN_RE.findall(text.lower())
        if len(tok) >= 3 and tok not in _STOP and not tok.isdigit()
    }


def relevance_score(
    *,
    market_tokens: set[str],
    evidence_keywords: list[str],
    reliability: float,
    freshness: float,
) -> tuple[float, list[str]]:
    """Returns (score 0..1, matched_keywords)."""
    if not market_tokens or not evidence_keywords:
        return 0.0, []
    ev_set = set(evidence_keywords)
    matched = list(market_tokens & ev_set)
    if not matched:
        return 0.0, []
    # Overlap fraction relative to evidence (small markets shouldn't get penalised).
    overlap = len(matched) / max(3, min(len(evidence_keywords), 30))
    score = 0.5 * overlap + 0.3 * freshness + 0.2 * reliability
    return min(1.0, score), sorted(matched)


def attach_to_market(
    conn: sqlite3.Connection,
    *,
    ticker: str,
    title: str | None,
    subtitle: str | None,
    category: str | None,
    since_hours: int = 48,
    min_score: float = 0.25,
    limit: int = 20,
) -> int:
    """Score recent news_evidence items against a market; persist attachments
    above `min_score`. Returns count of new attachments.
    """
    mtoks = _market_tokens(title, subtitle, category)
    if not mtoks:
        return 0
    since = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).isoformat()
    rows = conn.execute(
        """
        SELECT id, keywords_json, reliability_score, freshness_score
          FROM news_evidence
         WHERE fetched_at >= ?
         ORDER BY id DESC
         LIMIT ?
        """,
        (since, limit * 5),
    ).fetchall()

    count = 0
    for r in rows:
        try:
            kws = json.loads(r["keywords_json"] or "[]")
        except (TypeError, ValueError):
            kws = []
        score, matched = relevance_score(
            market_tokens=mtoks, evidence_keywords=kws,
            reliability=float(r["reliability_score"]),
            freshness=float(r["freshness_score"]),
        )
        if score < min_score:
            continue
        try:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO evidence_attachments
                  (ticker, evidence_id, relevance_score, matched_keywords, attached_at)
                VALUES (?,?,?,?,?)
                """,
                (ticker, int(r["id"]), score, ",".join(matched), utc_now_iso()),
            )
            if cur.rowcount > 0:
                count += 1
        except sqlite3.IntegrityError:
            pass
        if count >= limit:
            break
    return count


def attach_recent_evidence(
    conn: sqlite3.Connection,
    *,
    tickers: list[str] | None = None,
    since_hours: int = 24,
    min_score: float = 0.25,
) -> dict:
    """Attach recent evidence to one or more markets. Returns a summary."""
    if tickers is None:
        rows = conn.execute(
            "SELECT ticker, title, subtitle, category FROM markets "
            "WHERE status IN ('active','open') ORDER BY last_seen_at DESC LIMIT 500",
        ).fetchall()
    else:
        placeholders = ",".join("?" * len(tickers))
        rows = conn.execute(
            f"SELECT ticker, title, subtitle, category FROM markets WHERE ticker IN ({placeholders})",
            tuple(tickers),
        ).fetchall()
    total = 0
    for r in rows:
        total += attach_to_market(
            conn, ticker=r["ticker"], title=r["title"],
            subtitle=r["subtitle"], category=r["category"],
            since_hours=since_hours, min_score=min_score,
        )
    return {"markets": len(rows), "new_attachments": total}


def evidence_summary_for(conn: sqlite3.Connection, ticker: str, limit: int = 5) -> list[dict]:
    rows = conn.execute(
        """
        SELECT ne.title, ne.source_name, ne.item_url, ne.published_at,
               ea.relevance_score, ea.matched_keywords
          FROM evidence_attachments ea
          JOIN news_evidence ne ON ne.id = ea.evidence_id
         WHERE ea.ticker = ?
         ORDER BY ea.relevance_score DESC, ne.published_at DESC
         LIMIT ?
        """,
        (ticker, limit),
    ).fetchall()
    return [dict(r) for r in rows]
