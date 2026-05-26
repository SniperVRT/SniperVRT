"""Strategy modules. Each one takes a `Market` (plus context) and returns a
`FairEstimate` or `None`. The scanner runs all enabled strategies and lets
the signal builder decide which produce a tradeable edge.
"""
from .news_lag import NewsLagStrategy
from .mean_reversion import OverreactionMeanReversionStrategy
from .rules_mispricing import ResolutionRuleMispricingStrategy

__all__ = [
    "NewsLagStrategy",
    "OverreactionMeanReversionStrategy",
    "ResolutionRuleMispricingStrategy",
]
