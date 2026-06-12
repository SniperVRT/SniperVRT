"""Isotonic calibration for Kalshi fair-prob predictions.

Uses a pure-Python isotonic regression (PAV algorithm) — no scipy/sklearn
dependency. Trains on resolved markets where we have (predicted_fair,
realised_outcome) pairs. Predictions then mapped through the learned
monotonic function so calibration matches realised frequencies.
"""

from __future__ import annotations

import json
import pickle
import sqlite3
from pathlib import Path


def pav(predictions: list[float], outcomes: list[int]) -> list[tuple[float, float]]:
    """Pool Adjacent Violators monotonic regression.

    Returns sorted (x, calibrated_y) breakpoints; predict via linear interp.
    """
    if not predictions:
        return []
    paired = sorted(zip(predictions, outcomes))
    xs = [p for p, _ in paired]
    ys = [float(o) for _, o in paired]
    weights = [1.0] * len(ys)
    # Merge backward when out of order
    i = 0
    while i < len(ys) - 1:
        if ys[i] > ys[i + 1]:
            new_w = weights[i] + weights[i + 1]
            new_y = (ys[i] * weights[i] + ys[i + 1] * weights[i + 1]) / new_w
            ys[i] = new_y
            weights[i] = new_w
            ys.pop(i + 1)
            weights.pop(i + 1)
            xs.pop(i + 1)  # collapse x to leftmost
            if i > 0:
                i -= 1
        else:
            i += 1
    return list(zip(xs, ys))


def predict(breakpoints: list[tuple[float, float]], x: float) -> float:
    if not breakpoints:
        return x
    if x <= breakpoints[0][0]:
        return breakpoints[0][1]
    if x >= breakpoints[-1][0]:
        return breakpoints[-1][1]
    for i in range(len(breakpoints) - 1):
        x0, y0 = breakpoints[i]
        x1, y1 = breakpoints[i + 1]
        if x0 <= x <= x1:
            if x1 == x0:
                return y0
            t = (x - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return x


def fit_from_db(conn: sqlite3.Connection) -> list[tuple[float, float]]:
    rows = conn.execute(
        "SELECT p.fair_prob, r.outcome FROM probability_estimates p "
        "JOIN resolutions r ON r.ticker = p.ticker "
        "WHERE r.outcome IN (0, 1)"
    ).fetchall()
    if not rows:
        return []
    preds = [float(r["fair_prob"]) for r in rows]
    outs = [int(r["outcome"]) for r in rows]
    return pav(preds, outs)


def save(breakpoints, path: Path = Path("./data/isotonic_kalshi.pkl")) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        pickle.dump(breakpoints, f)


def load(path: Path = Path("./data/isotonic_kalshi.pkl")):
    if not path.exists():
        return []
    with path.open("rb") as f:
        return pickle.load(f)
