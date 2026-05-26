# Kalshi Mispricing Engine

A modular probability mispricing engine for Kalshi event markets. Its only
job is to scan markets, estimate true probabilities, compare them to the
market's implied price, and surface trades only when the edge clears fees,
spread, liquidity, uncertainty, and resolution risk.

> First version intentionally narrow: scan, estimate, compare, reject most,
> size tiny, log everything, review outcomes. Profit comes from discipline,
> not a bigger brain.

## Layout

```
kalshi/
  kalshi_engine/
    config.py          # env, bankroll, risk caps
    db.py              # SQLite connection + migrations
    schema.sql         # full storage schema
    connectors/
      kalshi.py        # public/auth REST client + market ingestion
    core/
      probability.py   # implied prob, fair prob, edge, EV, confidence
      filters.py       # liquidity / spread / time-to-close / vague rules
      signals.py       # signal builder + ranker
    risk/
      rules.py         # daily/weekly stop, max open, cooldown, sizing
    strategies/
      news_lag.py
      mean_reversion.py
      rules_mispricing.py
    validation/
      calibration.py   # Brier, calibration buckets, EV-vs-realized
    scanner.py         # one full scan pass
    cli.py             # `python -m kalshi_engine.cli scan`
    dashboard.py       # Streamlit UI
  tests/               # pytest: probability, risk, filters, db
```

## Quick start

```bash
cd kalshi
python -m venv .venv && source .venv/bin/activate
pip install -e .[dev]
cp .env.example .env       # paste KALSHI_API_KEY_ID / private key path
python -m kalshi_engine.cli init-db
python -m kalshi_engine.cli scan --limit 200 --dry-run
streamlit run kalshi_engine/dashboard.py
pytest
```

## Risk defaults (override in `.env`)

| Cap                       | Default |
|---------------------------|---------|
| Max position per market   | $5      |
| Max open positions        | 3       |
| Daily loss stop           | $10     |
| Weekly loss stop          | $30     |
| Min edge to act           | 5%      |
| Min confidence            | 0.6     |
| Manual approval required  | yes     |

Nothing executes live until you flip `EXECUTION_MODE=live` AND approve each
trade. Default mode is `signal` — the engine only prints what it would do.
