"""Copy-trade meta-allocator (Hyperliquid vaults).

US-legal architecture: Hyperliquid is a non-custodial perpetuals DEX whose
**vault system IS the copy-trade primitive**. Vault depositors share PnL
pro-rata with the vault's leader trader; deposit/withdraw via REST API.

We rank vaults on Sharpe + Calmar (drawdown-adjusted) instead of raw APR
and dynamically reallocate USDC across the top performers, with:
  - 24h lockup tracking (Hyperliquid enforces this on user vaults)
  - Portfolio drawdown safety gate (blocks new deposits if equity dives)
  - Survivorship tracking (inactive vaults stay in DB for back-test)
  - Spearman rank validation (did our scoring actually predict PnL?)

Quick-start:
  pip install hyperliquid-python-sdk eth-account
  copy-trade init-db
  copy-trade poll-leaderboard       # scrape vaultSummaries + enrich top 80
  copy-trade scores --top 20        # inspect ranked vaults
  copy-trade rebalance              # dry-run: see proposed deposits
  copy-trade rebalance --live       # LIVE: execute via signed actions
  copy-trade worker --live          # continuous polling + auto-rebalance

Env vars required for --live mode:
  HYPERLIQUID_WALLET_ADDRESS=0x...
  HYPERLIQUID_PRIVATE_KEY=0x...     # NEVER commit. fresh wallet recommended.
  HYPERLIQUID_TESTNET=false         # default: true (use testnet first)
"""
