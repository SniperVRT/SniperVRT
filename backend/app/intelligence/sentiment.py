"""Sentiment intelligence.

- Pulls Alternative.me's free Fear & Greed Index when reachable (no API key needed).
- Aggregates News sentiment from recent NewsItem rows as a deterministic fallback.
- Persists snapshots so we can later correlate sentiment vs forward returns.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import httpx

from backend.app.core.db import session_scope
from backend.app.models import NewsItem, SentimentSnapshot

log = logging.getLogger(__name__)


def _fear_greed() -> Optional[dict]:
    try:
        resp = httpx.get("https://api.alternative.me/fng/?limit=1",
                         timeout=4.0)
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            if data:
                d = data[0]
                value = float(d.get("value", 50))
                cls = d.get("value_classification", "neutral")
                # Normalize 0..100 -> -1..+1
                norm = (value - 50.0) / 50.0
                return {
                    "score": round(norm, 4),
                    "components": {"fear_greed_raw": value, "classification": cls},
                    "notes": f"Fear&Greed {value} ({cls})",
                }
    except Exception as e:
        log.debug("fear_greed fetch failed: %s", e)
    return None


def _news_sentiment_aggregate(window_hours: int = 24) -> dict:
    """Average news sentiment, weighted by importance, over the last `window_hours`."""
    since = int(time.time()) - window_hours * 3600
    with session_scope() as s:
        rows = s.query(NewsItem.sentiment, NewsItem.importance).filter(
            NewsItem.ts >= since,
        ).all()
    if not rows:
        return {"score": 0.0, "components": {"n": 0}, "notes": "no recent news"}
    wsum = sum(max(0.05, float(imp)) for _, imp in rows)
    score = sum(float(sent) * max(0.05, float(imp)) for sent, imp in rows) / wsum
    return {
        "score": round(score, 4),
        "components": {"n": len(rows), "window_hours": window_hours},
        "notes": f"weighted news sentiment over {window_hours}h",
    }


def snapshot_sentiment() -> dict:
    """Take all available sentiment snapshots in one call. Persists each source."""
    out = []
    now = int(time.time())
    fg = _fear_greed()
    sources = []
    if fg is not None:
        sources.append(("fear_greed", fg))
    sources.append(("news_agg", _news_sentiment_aggregate(24)))
    sources.append(("news_agg_1h", _news_sentiment_aggregate(1)))

    with session_scope() as s:
        for name, payload in sources:
            snap = SentimentSnapshot(
                ts=now, source=name, score=float(payload["score"]),
                components=payload.get("components", {}),
                notes=payload.get("notes", ""),
            )
            s.add(snap)
            s.flush()
            out.append({"source": name, "score": snap.score,
                        "components": snap.components, "notes": snap.notes,
                        "id": snap.id})
    return {"ok": True, "snapshots": out}


def recent_sentiment(*, limit: int = 100, source: Optional[str] = None) -> list[dict]:
    with session_scope() as s:
        q = s.query(SentimentSnapshot)
        if source:
            q = q.filter(SentimentSnapshot.source == source)
        rows = q.order_by(SentimentSnapshot.ts.desc()).limit(limit).all()
        return [{
            "id": r.id, "ts": r.ts, "source": r.source, "score": r.score,
            "components": r.components, "notes": r.notes,
        } for r in rows]
