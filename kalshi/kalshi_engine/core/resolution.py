"""Resolution-rule intelligence.

Classifies every market into one of:

  LOW_RISK_OBJECTIVE       — clear source, hard cutoff, objective rule
  MEDIUM_RISK_INTERPRETIVE — clear source but some interpretive language
  HIGH_RISK_AMBIGUOUS      — vague rule or weak source
  REJECT_SUBJECTIVE        — opinion-based / qualitative
  REJECT_SOURCE_UNCLEAR    — no extractable source

Produces a 0..1 `risk_score` (higher = worse), a list of ambiguity flags,
plus best-effort extraction of source URL/name and cutoff timestamp.

Purely deterministic; no LLM call. A later phase can layer Claude on top
for the genuinely ambiguous cases, but the bulk of markets can be
classified by structure alone.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from ..connectors.kalshi import Market
from ..db import utc_now_iso


# Trusted source names — extend conservatively.
_TRUSTED_SOURCES = (
    "bls", "bea", "eia", "federal reserve", "fomc", "fed",
    "noaa", "nws", "nhc", "usgs", "cdc", "nih", "treasury",
    "sec", "irs", "epa", "nasa", "uscensus", "census bureau",
    "imf", "world bank", "wto", "ecb", "boj", "bank of england",
    "kalshi", "coinmarketcap", "coingecko", "espn", "ap", "reuters",
)

_SUBJECTIVE_PATTERNS = (
    r"\bin the opinion of\b",
    r"\bsubjectively\b",
    r"\bgenerally\b",
    r"\bbroadly\b",
    r"\baesthetic\b",
    r"\bperceived\b",
    r"\bsignificant\b",      # almost always interpretive in rules
)

_VAGUE_PATTERNS = (
    r"\bapproximately\b",
    r"\baround\b",
    r"\bsometime\b",
    r"\bsoon\b",
    r"\beventually\b",
    r"\babout\b",
)

_OBJECTIVE_MARKERS = (
    r"\b(at|by|before|on)\s+\d{1,2}:\d{2}",   # time cutoff
    r"\b(at|by|before|on)\s+\d{4}-\d{2}-\d{2}",
    r"\b(at or above|at or below|equal to|greater than|less than)\b",
    r"\b(no later than|on or before)\b",
)

_URL_RE = re.compile(r"https?://\S+", re.I)
_TIME_RE = re.compile(r"\b(\d{1,2}:\d{2})\s*(am|pm|ET|UTC|EST|EDT|PT|PST|PDT|CT)?\b", re.I)
_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


class RiskClass:
    LOW = "LOW_RISK_OBJECTIVE"
    MEDIUM = "MEDIUM_RISK_INTERPRETIVE"
    HIGH = "HIGH_RISK_AMBIGUOUS"
    REJECT_SUBJECTIVE = "REJECT_SUBJECTIVE"
    REJECT_SOURCE_UNCLEAR = "REJECT_SOURCE_UNCLEAR"


@dataclass(frozen=True)
class ResolutionClassification:
    ticker: str
    risk_class: str
    risk_score: float          # 0..1, higher = worse
    flags: list[str]
    source_url: Optional[str]
    source_name: Optional[str]
    cutoff_at: Optional[str]   # ISO if extractable


def _extract_source(text: str, declared: str | None) -> tuple[Optional[str], Optional[str]]:
    url_match = _URL_RE.search(text)
    url = url_match.group(0) if url_match else None

    # Prefer the connector-supplied declared source if present.
    name: Optional[str] = declared.strip() if declared else None
    if not name:
        tl = text.lower()
        for s in _TRUSTED_SOURCES:
            # word-bounded match so "ap" doesn't fire on "happens", "appears", etc.
            if re.search(rf"\b{re.escape(s)}\b", tl):
                name = s
                break
    return url, name


def _extract_cutoff(close_time: datetime | None, text: str) -> Optional[str]:
    if close_time is not None:
        return close_time.isoformat()
    d = _DATE_RE.search(text)
    if d:
        return d.group(1)
    return None


def classify(m: Market) -> ResolutionClassification:
    title = (m.title or "") + " " + (m.subtitle or "")
    rules = (m.rules_primary or "") + " " + (m.rules_secondary or "")
    text = (title + " " + rules).strip()
    text_l = text.lower()

    flags: list[str] = []
    score = 0.0

    if not rules.strip():
        flags.append("no_rules_text")
        score += 0.4

    # Subjective wording is a hard fail.
    subj_hits = [p for p in _SUBJECTIVE_PATTERNS if re.search(p, text_l)]
    for h in subj_hits:
        flags.append(f"subjective:{h.strip(chr(92)+'b ')[:40]}")
    if subj_hits:
        score += 0.5

    vague_hits = [p for p in _VAGUE_PATTERNS if re.search(p, text_l)]
    for h in vague_hits:
        flags.append("vague_language")
    if vague_hits:
        score += 0.2 * min(3, len(vague_hits))

    objective_hits = [p for p in _OBJECTIVE_MARKERS if re.search(p, text_l)]
    if objective_hits:
        score -= 0.15
    else:
        flags.append("no_objective_marker")
        score += 0.15

    url, name = _extract_source(text, m.settlement_source)
    if not url and not name:
        flags.append("source_unclear")
        score += 0.3

    if "twitter" in text_l or "x.com" in text_l:
        flags.append("social_source")
        score += 0.2

    if "news reports" in text_l or "as reported by major" in text_l:
        flags.append("news_aggregate_source")
        score += 0.15

    if "inclusive" in text_l or "exclusive" in text_l or "excluding" in text_l:
        # Explicit boundary language is a GOOD sign — small score reduction
        score -= 0.05

    if not m.close_time:
        flags.append("no_close_time")
        score += 0.2

    score = max(0.0, min(1.0, score))

    # Final classification
    if subj_hits:
        risk_class = RiskClass.REJECT_SUBJECTIVE
    elif not url and not name:
        risk_class = RiskClass.REJECT_SOURCE_UNCLEAR
    elif score >= 0.6:
        risk_class = RiskClass.HIGH
    elif score >= 0.3:
        risk_class = RiskClass.MEDIUM
    else:
        risk_class = RiskClass.LOW

    return ResolutionClassification(
        ticker=m.ticker,
        risk_class=risk_class,
        risk_score=score,
        flags=flags,
        source_url=url,
        source_name=name,
        cutoff_at=_extract_cutoff(m.close_time, text),
    )


def persist(conn: sqlite3.Connection, c: ResolutionClassification) -> None:
    conn.execute(
        """
        INSERT INTO resolution_classifications (
            ticker, risk_class, risk_score, ambiguity_flags_json,
            source_url, source_name, cutoff_at, classified_at
        ) VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(ticker) DO UPDATE SET
            risk_class=excluded.risk_class,
            risk_score=excluded.risk_score,
            ambiguity_flags_json=excluded.ambiguity_flags_json,
            source_url=excluded.source_url,
            source_name=excluded.source_name,
            cutoff_at=excluded.cutoff_at,
            classified_at=excluded.classified_at
        """,
        (
            c.ticker, c.risk_class, c.risk_score, json.dumps(c.flags),
            c.source_url, c.source_name, c.cutoff_at, utc_now_iso(),
        ),
    )


def is_tradeable(c: ResolutionClassification, *, max_risk: str = RiskClass.MEDIUM) -> bool:
    """Default policy: LOW and MEDIUM are tradeable. HIGH and REJECT_* are not."""
    order = {
        RiskClass.LOW: 0,
        RiskClass.MEDIUM: 1,
        RiskClass.HIGH: 2,
        RiskClass.REJECT_SUBJECTIVE: 99,
        RiskClass.REJECT_SOURCE_UNCLEAR: 99,
    }
    return order[c.risk_class] <= order[max_risk]
