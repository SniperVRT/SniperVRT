"""One full scan pass: pull markets, snapshot them, run filters, run all
strategies, persist signals + rejections, return a ranked list.

The scanner does not place orders. In `signal` mode it just stores results
for the dashboard / CLI to display. In `paper` or `live` mode it still
requires the risk engine to approve before any execution path runs.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

import structlog

from .config import Settings, get_settings
from .connectors.kalshi import KalshiClient, Market
from .core import filters as F
from .core.probability import FairEstimate, midpoint_prob
from .core.signals import Rejection, Signal, build_signal, rank, signal_to_row
from .db import connect, transaction, utc_now_iso
from .risk.rules import can_take_trade
from .strategies import (
    NewsLagStrategy,
    OverreactionMeanReversionStrategy,
    ResolutionRuleMispricingStrategy,
)

log = structlog.get_logger("scanner")


@dataclass
class ScanResult:
    fetched: int
    filtered_out: int
    signals: list[Signal]
    rejections: list[Rejection]


def _persist_market(conn: sqlite3.Connection, m: Market) -> None:
    now = utc_now_iso()
    conn.execute(
        """
        INSERT INTO markets (
            ticker, event_ticker, series_ticker, title, subtitle, category, status,
            rules_primary, rules_secondary, open_time, close_time,
            expected_expiration_time, settlement_source, raw_json,
            first_seen_at, last_seen_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(ticker) DO UPDATE SET
            title=excluded.title,
            subtitle=excluded.subtitle,
            category=excluded.category,
            status=excluded.status,
            rules_primary=excluded.rules_primary,
            rules_secondary=excluded.rules_secondary,
            open_time=excluded.open_time,
            close_time=excluded.close_time,
            expected_expiration_time=excluded.expected_expiration_time,
            settlement_source=excluded.settlement_source,
            raw_json=excluded.raw_json,
            last_seen_at=excluded.last_seen_at
        """,
        (
            m.ticker, m.event_ticker, m.series_ticker, m.title, m.subtitle,
            m.category, m.status, m.rules_primary, m.rules_secondary,
            m.open_time.isoformat() if m.open_time else None,
            m.close_time.isoformat() if m.close_time else None,
            m.expected_expiration_time.isoformat() if m.expected_expiration_time else None,
            m.settlement_source, json.dumps(m.raw, default=str),
            now, now,
        ),
    )


def _persist_snapshot(conn: sqlite3.Connection, m: Market, book: dict | None = None) -> None:
    conn.execute(
        """
        INSERT INTO market_snapshots (
            ticker, captured_at, yes_bid, yes_ask, no_bid, no_ask,
            last_price, volume, volume_24h, open_interest, liquidity,
            yes_book_json, no_book_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            m.ticker, utc_now_iso(),
            m.yes_bid, m.yes_ask, m.no_bid, m.no_ask,
            m.last_price, m.volume, m.volume_24h, m.open_interest, m.liquidity,
            json.dumps((book or {}).get("yes")) if book else None,
            json.dumps((book or {}).get("no")) if book else None,
        ),
    )


def _persist_rejection(conn: sqlite3.Connection, r: Rejection) -> None:
    conn.execute(
        """
        INSERT INTO rejected_signals (ticker, strategy, reason, detail_json, created_at)
        VALUES (?,?,?,?,?)
        """,
        (r.ticker, r.strategy, r.reason, json.dumps(r.detail), utc_now_iso()),
    )


def _persist_signal(conn: sqlite3.Connection, s: Signal) -> int:
    row = signal_to_row(s)
    cur = conn.execute(
        """
        INSERT INTO signals (
            ticker, side, strategy, fair_prob, implied_prob, edge,
            expected_value, confidence, max_price_cents,
            suggested_size_usd, thesis, evidence_json, status, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'pending', ?)
        """,
        (
            row["ticker"], row["side"], row["strategy"], row["fair_prob"],
            row["implied_prob"], row["edge"], row["expected_value"],
            row["confidence"], row["max_price_cents"], row["suggested_size_usd"],
            row["thesis"], row["evidence_json"], utc_now_iso(),
        ),
    )
    return int(cur.lastrowid)


def _persist_probability_estimate(
    conn: sqlite3.Connection, ticker: str, strategy: str,
    implied: float, fair: FairEstimate, edge: float, ev: float,
    evidence: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO probability_estimates (
            ticker, strategy, implied_prob, fair_prob, confidence,
            uncertainty_lo, uncertainty_hi, edge, expected_value,
            evidence_json, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            ticker, strategy, implied, fair.fair_prob, fair.confidence,
            fair.uncertainty_lo, fair.uncertainty_hi, edge, ev,
            json.dumps(evidence), utc_now_iso(),
        ),
    )


def _strategies():
    return [
        NewsLagStrategy(),
        OverreactionMeanReversionStrategy(),
        ResolutionRuleMispricingStrategy(),
    ]


def scan(
    *,
    client: KalshiClient | None = None,
    conn: sqlite3.Connection | None = None,
    settings: Settings | None = None,
    limit: int = 200,
    max_pages: int | None = 1,
    fetch_orderbook: bool = False,
    evidence_provider=None,
) -> ScanResult:
    """Run one scan pass.

    `evidence_provider`: optional callable `(market) -> dict` returning
    optional `{"news_signal": ..., "rules_evidence": ...}` per-market.
    Week 1 the scanner can be driven without one — strategies that need
    evidence will simply skip those markets.
    """
    settings = settings or get_settings()
    client = client or KalshiClient(settings)
    conn = conn or connect(settings.db_path)
    strategies = _strategies()

    fetched = 0
    filtered_out = 0
    signals: list[Signal] = []
    rejections: list[Rejection] = []

    with transaction(conn):
        markets: Iterable[Market] = client.iter_markets(limit=limit, max_pages=max_pages)
        for m in markets:
            fetched += 1
            _persist_market(conn, m)

            filter_res = F.evaluate(m, settings)
            if not filter_res.passed:
                filtered_out += 1
                rej = Rejection(m.ticker, "filter", filter_res.reason or "unknown",
                                filter_res.detail or {})
                rejections.append(rej)
                _persist_rejection(conn, rej)
                _persist_snapshot(conn, m)
                continue

            book = None
            if fetch_orderbook:
                try:
                    ob = client.get_orderbook(m.ticker)
                    book = {"yes": ob.yes, "no": ob.no}
                except Exception as e:  # noqa: BLE001
                    log.warning("orderbook_fetch_failed", ticker=m.ticker, err=str(e))
            _persist_snapshot(conn, m, book)

            evidence = evidence_provider(m) if evidence_provider else {}
            implied = midpoint_prob(m.yes_bid, m.yes_ask) or 0.0

            for strat in strategies:
                try:
                    if strat.name == "mean_reversion":
                        fair = strat.estimate(m, evidence, conn=conn)
                    else:
                        fair = strat.estimate(m, evidence)
                except Exception as e:  # noqa: BLE001
                    log.warning("strategy_failed", strat=strat.name, ticker=m.ticker, err=str(e))
                    continue
                if fair is None:
                    continue

                outcome = build_signal(
                    market=m, fair=fair, strategy=strat.name,
                    settings=settings, evidence=evidence,
                )
                if isinstance(outcome, Rejection):
                    rejections.append(outcome)
                    _persist_rejection(conn, outcome)
                    _persist_probability_estimate(
                        conn, m.ticker, strat.name, implied, fair, 0.0, 0.0, evidence,
                    )
                    continue

                # Risk gate even for paper / signal mode — surfaces problems early.
                risk = can_take_trade(
                    conn=conn, ticker=m.ticker,
                    proposed_size_usd=outcome.suggested_size_usd,
                    settings=settings,
                )
                if not risk.allowed:
                    rej = Rejection(m.ticker, strat.name,
                                    f"risk:{risk.reason}", risk.detail or {})
                    rejections.append(rej)
                    _persist_rejection(conn, rej)
                    continue

                _persist_signal(conn, outcome)
                _persist_probability_estimate(
                    conn, m.ticker, strat.name, implied, fair,
                    outcome.edge, outcome.expected_value, evidence,
                )
                signals.append(outcome)

    ranked = rank(signals)
    log.info(
        "scan_complete",
        fetched=fetched, filtered_out=filtered_out,
        signals=len(ranked), rejections=len(rejections),
        ts=datetime.now(timezone.utc).isoformat(),
    )
    return ScanResult(fetched=fetched, filtered_out=filtered_out,
                      signals=ranked, rejections=rejections)
