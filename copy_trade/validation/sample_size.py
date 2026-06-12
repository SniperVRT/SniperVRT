"""Sample-size guards to prevent reporting metrics on too little data."""

from __future__ import annotations


class InsufficientSamplesError(ValueError):
    pass


def require_min_samples(n: int, min_n: int, metric_name: str) -> None:
    if n < min_n:
        raise InsufficientSamplesError(
            f"{metric_name}: n={n} < required {min_n}"
        )


def confidence_tag(n: int) -> str:
    if n < 20:
        return "insufficient"
    if n < 50:
        return "low_confidence"
    return "ok"
