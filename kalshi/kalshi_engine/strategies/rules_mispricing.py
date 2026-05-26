"""Resolution-rule mispricing.

Traders often price the *headline* of a market rather than the precise
settlement criteria. This strategy uses simple deterministic checks on the
rules text + optional structured `rules_evidence` to flag obvious gaps
between the public narrative and the settlement rule.

Week-1 version is rule-based and conservative. A later iteration can call
Claude to parse rules text and compare it to news evidence, but we don't
need an LLM call to start surfacing the obvious cases.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..connectors.kalshi import Market
from ..core.probability import FairEstimate, midpoint_prob


# Heuristic patterns the spec calls out: cutoff dates, official sources,
# exact-definition language that the headline often misses.
_STRICT_CUTOFF = re.compile(r"\bby\s+\d{1,2}:\d{2}\b|\bon or before\b|\bno later than\b", re.I)
_EXACT_SOURCE = re.compile(r"\b(official|as reported by|per the)\b", re.I)
_INCLUSIVE_OR_NOT = re.compile(r"\b(inclusive|exclusive|not including|excluding)\b", re.I)


@dataclass
class ResolutionRuleMispricingStrategy:
    name: str = "rules_mispricing"

    def estimate(
        self,
        market: Market,
        evidence: dict[str, Any] | None = None,
    ) -> FairEstimate | None:
        rules = (market.rules_primary or "") + " " + (market.rules_secondary or "")
        if not rules.strip():
            return None

        rule_evidence = (evidence or {}).get("rules_evidence")
        if not rule_evidence:
            return None

        # Required shape from the evidence layer:
        #   {"direction": "yes"|"no", "shift": 0..0.4, "reasons": [str, ...]}
        direction = rule_evidence.get("direction")
        shift = float(rule_evidence.get("shift", 0.0))
        if direction not in ("yes", "no") or shift <= 0:
            return None

        mid = midpoint_prob(market.yes_bid, market.yes_ask)
        if mid is None:
            return None

        # Confidence bonus when the rules text contains hard markers we can
        # latch onto.
        signal_hits = sum(
            bool(p.search(rules))
            for p in (_STRICT_CUTOFF, _EXACT_SOURCE, _INCLUSIVE_OR_NOT)
        )
        clarity_bonus = min(0.3, 0.1 * signal_hits)
        base_conf = float(rule_evidence.get("confidence", 0.6))
        conf = max(0.0, min(1.0, base_conf + clarity_bonus))

        delta = shift if direction == "yes" else -shift
        fair = max(0.01, min(0.99, mid + delta))
        half = max(0.02, 0.12 * (1 - conf))
        return FairEstimate(
            fair_prob=fair,
            confidence=conf,
            uncertainty_lo=max(0.0, fair - half),
            uncertainty_hi=min(1.0, fair + half),
        )
