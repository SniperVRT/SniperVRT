#!/usr/bin/env bash
# Quick backtest of one strategy. Usage: scripts/run_backtest.sh [strategy_name]
set -euo pipefail
STRAT="${1:-ensemble}"
cd "$(dirname "$0")/.."
export PYTHONPATH="$(pwd):${PYTHONPATH:-}"
python3 -c "
import json
from backend.app.core.db import init_db
from backend.app.data import ensure_dataset, load_candles
from backend.app.strategies import build_strategy
from backend.app.backtest.engine import run_backtest
init_db(); ensure_dataset()
df = load_candles()
strat = build_strategy('$STRAT')
result = run_backtest(strat, df)
print(json.dumps({
  'strategy': '$STRAT',
  'total_return_pct': result.total_return_pct,
  'sharpe': result.sharpe,
  'max_drawdown_pct': result.max_drawdown_pct,
  'win_rate': result.win_rate,
  'profit_factor': result.profit_factor,
  'num_trades': result.num_trades,
  'final_equity': result.final_equity,
}, indent=2))
"
