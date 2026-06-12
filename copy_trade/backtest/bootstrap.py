"""Bootstrap confidence intervals."""

from __future__ import annotations

import math
import random as _r
from typing import Callable


def bootstrap_ci(values: list[float],
                 stat_fn: Callable[[list[float]], float] | None = None,
                 *,
                 n_resamples: int = 1000,
                 ci: float = 0.95,
                 seed: int = 42) -> dict[str, float | None]:
    if not values or len(values) < 5:
        return {"mean": None, "lo": None, "hi": None, "n": len(values)}
    fn = stat_fn or (lambda xs: sum(xs) / len(xs))
    rng = _r.Random(seed)
    samples = []
    n = len(values)
    for _ in range(n_resamples):
        resample = [values[rng.randrange(n)] for _ in range(n)]
        samples.append(fn(resample))
    samples.sort()
    lo_idx = int((1 - ci) / 2 * n_resamples)
    hi_idx = int((1 - (1 - ci) / 2) * n_resamples) - 1
    return {"mean": fn(values),
            "lo": samples[lo_idx], "hi": samples[hi_idx], "n": n}


def sharpe(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    std = math.sqrt(var)
    if std < 1e-12:
        return 0.0
    return (mean / std) * math.sqrt(365)
