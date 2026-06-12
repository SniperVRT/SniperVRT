"""Tests for isotonic calibration."""

from __future__ import annotations

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "kalshi"))

from kalshi_engine.validation.isotonic import pav, predict


def test_pav_monotonic_output():
    preds = [0.1, 0.2, 0.3, 0.4, 0.5]
    outs = [0, 0, 0, 1, 1]
    bps = pav(preds, outs)
    ys = [y for _, y in bps]
    for i in range(len(ys) - 1):
        assert ys[i] <= ys[i + 1] + 1e-9


def test_predict_interpolates():
    bps = [(0.0, 0.0), (0.5, 0.5), (1.0, 1.0)]
    assert predict(bps, 0.25) == pytest.approx(0.25)
    assert predict(bps, -0.1) == 0.0
    assert predict(bps, 1.5) == 1.0


def test_pav_fixes_inversion():
    preds = [0.1, 0.2, 0.3]
    outs = [1, 0, 1]  # middle is inverted
    bps = pav(preds, outs)
    ys = [y for _, y in bps]
    for i in range(len(ys) - 1):
        assert ys[i] <= ys[i + 1] + 1e-9


def test_predict_empty():
    assert predict([], 0.5) == 0.5
