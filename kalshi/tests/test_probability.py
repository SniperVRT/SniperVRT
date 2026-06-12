from kalshi_engine.core.probability import (
    FairEstimate,
    build_edge_report,
    expected_value_per_dollar,
    implied_yes_prob,
    kelly_fraction,
    midpoint_prob,
)


def test_implied_yes_prob_basic():
    assert implied_yes_prob(42) == 0.42
    assert implied_yes_prob(None) is None
    assert implied_yes_prob(0) is None
    assert implied_yes_prob(100) is None


def test_midpoint():
    assert midpoint_prob(40, 44) == 0.42
    assert midpoint_prob(None, 44) is None


def test_expected_value_after_fees_positive_edge():
    # Fair = 55%, paying 42 cents YES -> positive EV per dollar
    ev = expected_value_per_dollar(0.55, 42, "yes")
    assert ev > 0


def test_expected_value_after_fees_zero_edge():
    # Fair = market -> EV should be negative once fees apply
    ev = expected_value_per_dollar(0.50, 50, "yes")
    assert ev < 0


def test_edge_report_picks_better_side():
    fair = FairEstimate(fair_prob=0.55, confidence=0.7,
                        uncertainty_lo=0.50, uncertainty_hi=0.60)
    r = build_edge_report(yes_bid=40, yes_ask=42, no_bid=56, no_ask=58, fair=fair)
    assert r is not None
    assert r.side == "yes"
    assert r.edge > 0
    assert r.expected_value > 0


def test_edge_report_no_book_returns_none():
    fair = FairEstimate(0.5, 0.5, 0.4, 0.6)
    assert build_edge_report(yes_bid=None, yes_ask=None,
                             no_bid=None, no_ask=None, fair=fair) is None


def test_overlap_flag_true_when_band_straddles_price():
    fair = FairEstimate(0.52, 0.7, 0.45, 0.55)
    r = build_edge_report(yes_bid=48, yes_ask=50, no_bid=50, no_ask=52, fair=fair)
    assert r is not None
    # market at 0.50 is inside [0.45, 0.55]
    assert r.overlaps_market is True


def test_kelly_caps_to_user_cap():
    f = kelly_fraction(0.99, 50, "yes", cap=0.10)
    assert 0 <= f <= 0.10
