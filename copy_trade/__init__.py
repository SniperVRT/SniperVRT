"""Copy-trade meta-allocator.

Programmatically subscribes follower accounts to top-ranked master traders
on Bitget (the only major platform with a programmatic subscriber API).
Masters are ranked on Sharpe + Calmar rather than the platform's raw-ROI
leaderboard. Capital allocation is score-weighted with per-master caps.

Quick-start:
  copy-trade init-db
  copy-trade poll-leaderboard          # scrape leaderboard, score traders
  copy-trade scores --top 20           # inspect rankings
  copy-trade rebalance                 # dry-run: see proposed subscribe/unsub
  copy-trade rebalance --live          # LIVE: execute real subscriptions
  copy-trade worker --live             # continuous background worker
"""
