from datetime import datetime, timezone

from kalshi_engine.validation.metrics import (
    brier, log_loss, performance_report,
)


def test_brier_perfect_classifier():
    samples = [(0.99, 1), (0.01, 0)]
    assert brier(samples) < 0.001


def test_log_loss_decreases_with_better_predictions():
    bad = log_loss([(0.5, 1), (0.5, 0)])
    good = log_loss([(0.9, 1), (0.1, 0)])
    assert good < bad


def test_performance_report_empty(conn):
    rep = performance_report(conn)
    assert rep.n == 0
    assert rep.profit_factor == 0.0


def test_performance_report_with_data(conn):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO markets (ticker, title, status, first_seen_at, last_seen_at) "
        "VALUES ('X','t','active',?,?)", (now, now),
    )
    conn.execute(
        "INSERT INTO probability_estimates (ticker, strategy, implied_prob, fair_prob, "
        "confidence, uncertainty_lo, uncertainty_hi, edge, expected_value, created_at) "
        "VALUES ('X','news_lag',0.4,0.65,0.7,0.6,0.7,0.25,0.2,?)", (now,),
    )
    conn.execute(
        "INSERT INTO resolutions (ticker, outcome, resolved_at) VALUES ('X','yes',?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO paper_positions (ticker, side, qty, avg_cost_cents, opened_at, "
        "closed_at, realized_pnl_usd, strategy) VALUES "
        "('X','yes',1,40,?,?,3.0,'news_lag')", (now, now),
    )
    rep = performance_report(conn)
    assert rep.n >= 1
    assert rep.profit_factor > 0
    assert rep.win_rate > 0
