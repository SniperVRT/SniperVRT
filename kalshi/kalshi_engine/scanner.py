"""One full scan pass: pull markets, snapshot them, quality-score them,
classify resolution risk, run strategies, combine via ensemble, persist
signals + rejections, optionally execute paper orders.

The scanner does not place live orders. In paper mode it routes accepted
signals through `paper.executor`. The risk engine still has final say.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

import structlog

from .config import ExecutionMode, Settings, get_settings
from .connectors.kalshi import KalshiClient, Market, Orderbook
from .core import filters as F
from .core import quality as Q
from .core import resolution as R
from .core.ensemble import StrategyVote, combine as ensemble_combine, persist as persist_ensemble
from .core.probability import FairEstimate, midpoint_prob
from .core.resolution import RiskClass
from .core.signals import Rejection, Signal, build_signal, rank, signal_to_row
from .db import connect, transaction, utc_now_iso
from .news.evidence import attach_to_market, evidence_summary_for
from .paper.executor import PaperExecutor
from .risk import governance as gov
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
    classified_rejects: int
    signals: list[Signal]
    rejections: list[Rejection]
    paper_orders_placed: int


# ----- persistence helpers -------------------------------------------------- #
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


def _persist_snapshot(conn: sqlite3.Connection, m: Market, book: Orderbook | None = None) -> None:
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
            json.dumps(book.yes) if book else None,
            json.dumps(book.no) if book else None,
        ),
    )


def _persist_rejection(conn: sqlite3.Connection, r: Rejection) -> None:
    conn.execute(
        "INSERT INTO rejected_signals (ticker, strategy, reason, detail_json, created_at) "
        "VALUES (?,?,?,?,?)",
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


# ----- main scan ----------------------------------------------------------- #
def scan(
    *,
    client: KalshiClient | None = None,
    conn: sqlite3.Connection | None = None,
    settings: Settings | None = None,
    limit: int = 200,
    max_pages: int | None = 1,
    fetch_orderbook: bool = False,
    evidence_provider=None,
    paper_execute: bool | None = None,
    max_resolution_risk: str = RiskClass.MEDIUM,
) -> ScanResult:
    """Run one scan pass.

    `paper_execute`: if None, auto-enabled when settings.execution_mode == PAPER.
    """
    settings = settings or get_settings()
    if paper_execute is None:
        paper_execute = settings.execution_mode == ExecutionMode.PAPER
    client = client or KalshiClient(settings)
    conn = conn or connect(settings.db_path)
    strategies = _strategies()
    paper = PaperExecutor.from_env(conn) if paper_execute else None

    started_at = utc_now_iso()
    fetched = 0
    filtered_out = 0
    classified_rejects = 0
    signals: list[Signal] = []
    rejections: list[Rejection] = []
    paper_placed = 0
    http_errors = 0
    pages = 0

    with transaction(conn):
        run_id = conn.execute(
            "INSERT INTO ingestion_runs (started_at) VALUES (?)", (started_at,),
        ).lastrowid

        try:
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

                # Fetch order book if requested (and tolerate failure).
                book: Orderbook | None = None
                if fetch_orderbook:
                    try:
                        book = client.get_orderbook(m.ticker)
                    except Exception as e:  # noqa: BLE001
                        http_errors += 1
                        log.warning("orderbook_fetch_failed", ticker=m.ticker, err=str(e))
                _persist_snapshot(conn, m, book)

                # Quality + resolution-rule classification (always persist).
                qscore = Q.score_market(m, book=book)
                Q.persist(conn, m.ticker, qscore)

                cls = R.classify(m)
                R.persist(conn, cls)
                if not R.is_tradeable(cls, max_risk=max_resolution_risk):
                    classified_rejects += 1
                    rej = Rejection(m.ticker, "resolution_class", cls.risk_class,
                                    {"score": cls.risk_score, "flags": cls.flags})
                    rejections.append(rej)
                    _persist_rejection(conn, rej)
                    continue

                evidence = evidence_provider(m) if evidence_provider else {}
                # Attach + summarise any matching news evidence from the DB.
                try:
                    attach_to_market(
                        conn, ticker=m.ticker, title=m.title,
                        subtitle=m.subtitle, category=m.category,
                    )
                    news_ev = evidence_summary_for(conn, m.ticker, limit=3)
                except Exception as e:  # noqa: BLE001
                    log.warning("evidence_attach_failed", ticker=m.ticker, err=str(e))
                    news_ev = []
                evidence = {**evidence, "quality": Q.to_dict(qscore),
                            "resolution_class": cls.risk_class,
                            "resolution_score": cls.risk_score,
                            "news_evidence": news_ev}
                implied = midpoint_prob(m.yes_bid, m.yes_ask) or 0.0

                # Run each strategy to collect votes for ensemble + persist
                # individual probability estimates for calibration tracking.
                votes: list[StrategyVote] = []
                for strat in strategies:
                    try:
                        if strat.name == "mean_reversion":
                            fair = strat.estimate(m, evidence, conn=conn)
                        else:
                            fair = strat.estimate(m, evidence)
                    except Exception as e:  # noqa: BLE001
                        log.warning("strategy_failed", strat=strat.name,
                                    ticker=m.ticker, err=str(e))
                        continue
                    if fair is None:
                        continue
                    votes.append(StrategyVote(strategy=strat.name, fair=fair))
                    _persist_probability_estimate(
                        conn, m.ticker, strat.name, implied, fair, 0.0, 0.0, evidence,
                    )

                if not votes:
                    continue

                # Ensemble combine
                ens = ensemble_combine(
                    ticker=m.ticker, votes=votes,
                    yes_bid=m.yes_bid, yes_ask=m.yes_ask,
                    no_bid=m.no_bid, no_ask=m.no_ask,
                )
                if ens is None:
                    continue
                ensemble_id = persist_ensemble(conn, ens)

                if ens.recommendation == "hold":
                    rejections.append(Rejection(
                        m.ticker, "ensemble", "no_actionable_recommendation",
                        {"edge": ens.edge, "agreement": ens.agreement,
                         "flags": ens.flags},
                    ))
                    _persist_rejection(conn, rejections[-1])
                    continue

                # Build the final signal off the ensemble-combined estimate
                combined_fair = FairEstimate(
                    fair_prob=ens.combined_fair,
                    confidence=ens.combined_confidence,
                    uncertainty_lo=ens.band_lo,
                    uncertainty_hi=ens.band_hi,
                )
                outcome = build_signal(
                    market=m, fair=combined_fair, strategy="ensemble",
                    settings=settings,
                    evidence={"ensemble_id": ensemble_id, "votes":
                              [v.strategy for v in votes], **evidence},
                )
                if isinstance(outcome, Rejection):
                    rejections.append(outcome)
                    _persist_rejection(conn, outcome)
                    continue

                # Per-trade risk gate
                risk = can_take_trade(
                    conn=conn, ticker=m.ticker,
                    proposed_size_usd=outcome.suggested_size_usd,
                    settings=settings,
                )
                if not risk.allowed:
                    rej = Rejection(m.ticker, "ensemble", f"risk:{risk.reason}",
                                    risk.detail or {})
                    rejections.append(rej)
                    _persist_rejection(conn, rej)
                    continue

                # Portfolio-level governance gate
                gd = gov.evaluate_portfolio(
                    conn=conn, settings=settings,
                    proposed_size_usd=outcome.suggested_size_usd,
                    category=m.category, strategy=outcome.strategy,
                )
                if not gd.allowed:
                    rej = Rejection(m.ticker, "ensemble", f"governance:{gd.reason}",
                                    gd.detail or {})
                    rejections.append(rej)
                    _persist_rejection(conn, rej)
                    continue

                _persist_signal(conn, outcome)
                signals.append(outcome)

                # Optional paper execution
                if paper is not None and book is not None:
                    order, fill = paper.submit_buy(
                        signal=outcome, book=book, ensemble_id=ensemble_id,
                    )
                    if fill is not None:
                        paper_placed += 1
        finally:
            conn.execute(
                "UPDATE ingestion_runs SET finished_at=?, pages_fetched=?, "
                "markets_fetched=?, markets_persisted=?, signals_emitted=?, "
                "rejections=?, http_errors=? WHERE id=?",
                (utc_now_iso(), pages, fetched, fetched,
                 len(signals), len(rejections), http_errors, run_id),
            )

    ranked = rank(signals)
    log.info(
        "scan_complete",
        fetched=fetched, filtered_out=filtered_out,
        classified_rejects=classified_rejects,
        signals=len(ranked), rejections=len(rejections),
        paper_orders=paper_placed,
        ts=datetime.now(timezone.utc).isoformat(),
    )
    return ScanResult(
        fetched=fetched, filtered_out=filtered_out,
        classified_rejects=classified_rejects,
        signals=ranked, rejections=rejections,
        paper_orders_placed=paper_placed,
    )
