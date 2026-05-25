"""News ingestion. Tries public RSS feeds; falls back to deterministic synthetic
events so the system can be exercised offline.

We classify each item with a tiny lexicon (no LLM calls), score sentiment in
[-1, +1], and assign a topic and importance. After enough price history exists,
`attribute_market_reactions` measures the realized 1h/24h log-return after each
event so downstream edge-discovery can correlate news classes with price moves.
"""
from __future__ import annotations

import hashlib
import logging
import math
import re
import time
from datetime import datetime
from typing import Optional
from xml.etree import ElementTree as ET

import httpx

from backend.app.core.db import session_scope
from backend.app.data import load_candles, TIMEFRAME_SECONDS
from backend.app.models import NewsItem

log = logging.getLogger(__name__)


RSS_FEEDS: list[tuple[str, str]] = [
    # (source, url)
    ("coindesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("cointelegraph", "https://cointelegraph.com/rss"),
    ("bitcoinmagazine", "https://bitcoinmagazine.com/.rss/full/"),
    ("decrypt", "https://decrypt.co/feed"),
]

# Topic keyword map (deterministic, auditable).
TOPIC_KEYWORDS = {
    "etf":         ["etf", "spot etf", "ishares", "blackrock", "fidelity"],
    "regulation":  ["sec ", "cftc", "regulator", "lawsuit", "settlement", "ban", "compliance"],
    "macro":       ["cpi", "fomc", "fed ", "rate hike", "rate cut", "inflation", "treasury", "jobs report"],
    "hack":        ["hack", "exploit", "breach", "stolen", "drained", "rug"],
    "exchange":    ["binance", "coinbase", "okx", "kraken", "bybit", "huobi", "ftx"],
    "miner":       ["miner", "mining", "hashrate", "difficulty adjustment"],
    "liquidation": ["liquidation", "liquidated", "long squeeze", "short squeeze"],
    "institution": ["microstrategy", "saylor", "treasury reserve", "corporate", "fund", "etf flow"],
    "onchain":     ["wallet", "whale", "transferred", "on-chain", "miner outflow"],
}

POSITIVE_LEXICON = {
    "surge", "soar", "rally", "rise", "gain", "high", "approval", "bullish", "adoption",
    "record", "inflow", "buy", "accumulate", "breakout",
}
NEGATIVE_LEXICON = {
    "crash", "plunge", "drop", "fall", "low", "rejection", "bearish", "ban", "hack",
    "exploit", "liquidation", "outflow", "sell", "dump", "fraud", "fear", "panic",
}
HIGH_IMPACT_WORDS = {"sec ", "fomc", "fed ", "etf approval", "hack", "halving", "default"}


def _classify(text: str) -> tuple[str, float, float, list[str]]:
    t = text.lower()
    topic = "btc"
    matched: list[str] = []
    for tname, kws in TOPIC_KEYWORDS.items():
        for kw in kws:
            if kw in t:
                topic = tname
                matched.append(kw)
                break
    pos = sum(1 for w in POSITIVE_LEXICON if w in t)
    neg = sum(1 for w in NEGATIVE_LEXICON if w in t)
    total = pos + neg
    sentiment = 0.0 if total == 0 else (pos - neg) / total
    importance = min(1.0, 0.2 + 0.15 * total)
    for hw in HIGH_IMPACT_WORDS:
        if hw in t:
            importance = max(importance, 0.85)
    return topic, sentiment, importance, matched


def _parse_rss(xml: str) -> list[dict]:
    out = []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return out
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        desc = (item.findtext("description") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        ts = int(time.time())
        for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z"):
            try:
                ts = int(datetime.strptime(pub, fmt).timestamp())
                break
            except Exception:
                pass
        # Strip HTML tags from description naively
        desc = re.sub(r"<[^>]+>", "", desc)[:500]
        if title:
            out.append({"title": title, "summary": desc, "url": link, "ts": ts})
    return out


def _synthetic_events(seed: Optional[int] = None) -> list[dict]:
    """Generate plausible deterministic news so the pipeline can run offline."""
    base = seed or int(time.time() // 3600)
    rng_seed = base
    templates = [
        ("CoinDesk-style", "BTC rallies as ETF inflows hit fresh record"),
        ("CoinDesk-style", "Fed minutes signal hawkish stance, BTC sees brief pullback"),
        ("CoinDesk-style", "Major exchange reports temporary withdrawal pause"),
        ("CoinDesk-style", "On-chain whale moves 5000 BTC after weeks dormant"),
        ("CoinDesk-style", "SEC delays decision on spot Bitcoin ETF amendment"),
        ("CoinDesk-style", "Miner outflows drop to monthly low, accumulation trend continues"),
        ("CoinDesk-style", "Liquidation cascade triggers $200m short squeeze"),
        ("CoinDesk-style", "MicroStrategy announces additional BTC purchase"),
        ("CoinDesk-style", "Regulatory clarity proposal passes committee vote"),
        ("CoinDesk-style", "Hack on cross-chain bridge drains $80m in assets"),
    ]
    events = []
    now = int(time.time())
    for i, (src, head) in enumerate(templates):
        offset_h = ((rng_seed + i * 7) % 24) + i
        events.append({
            "title": head,
            "summary": head + " — analysts weigh implications for short-term price action.",
            "url": "",
            "ts": now - offset_h * 3600,
            "source": "synthetic_" + src.split("-")[0].lower(),
        })
    return events


def _dedupe_key(source: str, title: str) -> str:
    return hashlib.sha1(f"{source}|{title}".encode("utf-8")).hexdigest()[:16]


def _existing_keys() -> set[str]:
    with session_scope() as s:
        rows = s.query(NewsItem.source, NewsItem.headline).all()
    return {_dedupe_key(src, h) for (src, h) in rows}


def ingest_news(*, force_synthetic: bool = False, timeout: float = 4.0) -> dict:
    """Pull all configured feeds, parse, classify, dedupe, persist. Returns counts."""
    items: list[dict] = []
    sources_used: list[str] = []
    if not force_synthetic:
        for (src, url) in RSS_FEEDS:
            try:
                resp = httpx.get(url, timeout=timeout,
                                 headers={"User-Agent": "SniperVRT/0.2"})
                if resp.status_code == 200:
                    parsed = _parse_rss(resp.text)
                    for p in parsed:
                        p["source"] = src
                    items.extend(parsed)
                    sources_used.append(src)
            except Exception as e:
                log.debug("RSS failed for %s: %s", src, e)
    if not items:
        items = _synthetic_events()
        sources_used = sorted({i["source"] for i in items})

    existing = _existing_keys()
    inserted = 0
    now = int(time.time())
    with session_scope() as s:
        for it in items:
            key = _dedupe_key(it["source"], it["title"])
            if key in existing:
                continue
            text = f"{it['title']} {it.get('summary','')}"
            topic, sentiment, importance, matched = _classify(text)
            s.add(NewsItem(
                ts=int(it.get("ts", now)), fetched_at=now,
                source=it["source"], topic=topic,
                headline=it["title"][:1000],
                summary=(it.get("summary") or "")[:2000],
                url=(it.get("url") or "")[:500],
                sentiment=float(sentiment),
                importance=float(importance),
                keywords=matched,
            ))
            existing.add(key)
            inserted += 1
    return {"ok": True, "sources": sources_used, "fetched": len(items), "inserted": inserted}


def recent_news(*, limit: int = 50, topic: Optional[str] = None,
                min_importance: float = 0.0) -> list[dict]:
    with session_scope() as s:
        q = s.query(NewsItem)
        if topic:
            q = q.filter(NewsItem.topic == topic)
        if min_importance > 0:
            q = q.filter(NewsItem.importance >= min_importance)
        rows = q.order_by(NewsItem.ts.desc()).limit(limit).all()
        return [{
            "id": n.id, "ts": n.ts, "source": n.source, "topic": n.topic,
            "headline": n.headline, "summary": n.summary,
            "url": n.url, "sentiment": n.sentiment, "importance": n.importance,
            "keywords": n.keywords,
            "react_1h": n.market_reaction_1h, "react_24h": n.market_reaction_24h,
        } for n in rows]


def attribute_market_reactions() -> dict:
    """For each NewsItem with no reaction yet, compute realized 1h / 24h log-return
    if we have at least 24h of price data after the event."""
    df = load_candles(limit=5000)
    if df.empty:
        return {"ok": False, "reason": "no price data"}
    df_ts = df["ts"].astype(int).values
    closes = df["close"].astype(float).values
    tf = TIMEFRAME_SECONDS.get("1h", 3600)
    now = int(time.time())
    updated = 0
    with session_scope() as s:
        rows = s.query(NewsItem).filter(
            (NewsItem.market_reaction_24h.is_(None)) | (NewsItem.market_reaction_1h.is_(None))
        ).all()
        for n in rows:
            if now - n.ts < 24 * 3600:
                continue  # not yet enough future
            # Find index of first candle at or after event
            i0 = None
            for i, ts in enumerate(df_ts):
                if ts >= n.ts:
                    i0 = i
                    break
            if i0 is None:
                continue
            try:
                p0 = float(closes[i0])
                # 1h later
                i1 = i0 + max(1, int(3600 / tf))
                if i1 < len(closes):
                    p1 = float(closes[i1])
                    if p0 > 0 and p1 > 0:
                        n.market_reaction_1h = math.log(p1 / p0)
                # 24h later
                i24 = i0 + max(1, int(86400 / tf))
                if i24 < len(closes):
                    p24 = float(closes[i24])
                    if p0 > 0 and p24 > 0:
                        n.market_reaction_24h = math.log(p24 / p0)
                updated += 1
            except (IndexError, ValueError):
                continue
    return {"ok": True, "updated": updated}
