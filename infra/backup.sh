#!/usr/bin/env bash
# Atomic SQLite backups of all SniperVRT DBs. Run via cron every 6h.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TS=$(date -u +%Y%m%d-%H%M%S)
mkdir -p "$ROOT/data/backups"

for src in "$ROOT/kalshi/data/kalshi.db" \
           "$ROOT/copy_trade/data/copy_trade.db" \
           "$ROOT/data/unified.db"; do
  if [[ -f "$src" ]]; then
    name=$(basename "$src" .db)
    sqlite3 "$src" ".backup '$ROOT/data/backups/${name}_${TS}.db'"
  fi
done

# Keep last 14 days
find "$ROOT/data/backups" -name "*.db" -mtime +14 -delete 2>/dev/null || true
echo "backups written: $ROOT/data/backups (TS=$TS)"
