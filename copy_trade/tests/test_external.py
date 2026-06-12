"""Tests for external/twitter.py and external/onchain.py."""

from __future__ import annotations

import pytest

from copy_trade.external.twitter import (
    TweetRecord, aggregate_sentiment, score_text,
)
from copy_trade.external.onchain import aggregate_signal


def test_score_text_bullish():
    assert score_text("going long, this is a moon breakout") > 0


def test_score_text_bearish():
    assert score_text("short the dump, bearish") < 0


def test_score_text_neutral():
    assert score_text("hello world weather is nice") == 0.0


def test_score_text_empty():
    assert score_text("") == 0.0
    assert score_text(None) == 0.0  # type: ignore[arg-type]


def test_aggregate_sentiment_weights_engagement():
    tweets = [
        TweetRecord("1", "a", "bullish moon", "", likes=100, retweets=50),
        TweetRecord("2", "a", "bearish dump", "", likes=0, retweets=0),
    ]
    out = aggregate_sentiment(tweets)
    assert out["n"] == 2
    # high-engagement bullish tweet should pull weighted score positive
    assert out["weighted"] > out["score"] or out["weighted"] > 0


def test_aggregate_sentiment_empty():
    out = aggregate_sentiment([])
    assert out == {"n": 0, "score": 0.0, "weighted": 0.0}


def test_onchain_aggregate_handles_empty():
    out = aggregate_signal(None)
    assert out["n_coins"] == 0
    out = aggregate_signal([])
    assert out["n_coins"] == 0
    out = aggregate_signal([{}])  # missing second element
    assert out["n_coins"] == 0


def test_onchain_aggregate_basic_funding():
    meta_ctxs = [
        {"universe": []},
        [
            {"funding": "0.0001", "openInterest": "10000"},
            {"funding": "0.00005", "openInterest": "5000"},
        ],
    ]
    out = aggregate_signal(meta_ctxs)
    assert out["n_coins"] == 2
    assert out["risk_on_score"] > 0
    assert out["total_oi"] == pytest.approx(15000.0)


def test_onchain_aggregate_score_bounded():
    meta_ctxs = [
        {"universe": []},
        [{"funding": "0.5", "openInterest": "1"}],  # extreme funding
    ]
    out = aggregate_signal(meta_ctxs)
    assert -1.0 <= out["risk_on_score"] <= 1.0
