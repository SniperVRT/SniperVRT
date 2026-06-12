"""SniperVRT Mission Control — the one dashboard for everything.

Written for someone who has NEVER seen this system before. Every number
has a plain-English explanation next to it.

Run:  streamlit run dashboard/app.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "kalshi"))
sys.path.insert(0, str(ROOT / "copy_trade"))

KALSHI_DB = Path(os.environ.get("KALSHI_DB_PATH", ROOT / "kalshi" / "data" / "kalshi.db"))
COPY_DB = Path(os.environ.get("COPYTRADE_DB_PATH", ROOT / "copy_trade" / "data" / "copy_trade.db"))
UNIFIED_DB = Path(os.environ.get("UNIFIED_DB_PATH", ROOT / "data" / "unified.db"))

st.set_page_config(page_title="SniperVRT Mission Control", layout="wide",
                   page_icon="🎯")


# ---------- helpers ----------------------------------------------------------
def _conn(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    return c


def _df(conn: sqlite3.Connection | None, sql: str, params=()) -> pd.DataFrame:
    if conn is None:
        return pd.DataFrame()
    try:
        return pd.read_sql_query(sql, conn, params=params)
    except Exception:
        return pd.DataFrame()


def _one(conn: sqlite3.Connection | None, sql: str, default=None):
    if conn is None:
        return default
    try:
        row = conn.execute(sql).fetchone()
        return row[0] if row and row[0] is not None else default
    except Exception:
        return default


def money(x) -> str:
    try:
        return f"${float(x):,.2f}"
    except (TypeError, ValueError):
        return "—"


def pct(x) -> str:
    try:
        return f"{float(x):.1%}"
    except (TypeError, ValueError):
        return "—"


# ---------- sidebar: what is this? -------------------------------------------
with st.sidebar:
    st.title("🎯 SniperVRT")
    st.caption("Mission Control")
    page = st.radio("View", [
        "📋 Start Here (read me first)",
        "💰 Money — where is it & how is it doing",
        "🤖 Decisions — what the system chose & why",
        "🏦 Copy-Trading (Hyperliquid)",
        "🎲 Prediction Markets (Kalshi)",
        "🛡️ Safety & Risk",
        "🚦 Go-Live Checklist",
        "🧪 Testing & Validation",
        "📖 Glossary",
    ])
    st.divider()
    st.caption(f"Refreshed {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC")
    if st.button("🔄 Refresh now"):
        st.rerun()

ct = _conn(COPY_DB)
kc = _conn(KALSHI_DB)
uc = _conn(UNIFIED_DB)


# ---------- persistent status strip ------------------------------------------
def status_strip():
    c1, c2, c3, c4, c5 = st.columns(5)
    # Copy-trade equity
    snap = _df(ct, "SELECT * FROM portfolio_snapshots ORDER BY captured_at DESC LIMIT 1")
    eq = None
    dd = None
    if not snap.empty:
        r = snap.iloc[0]
        eq = float(r["total_capital"]) + float(r["realized_pnl"]) + float(r["unrealized_pnl"])
        dd = float(r["drawdown_pct"])
    c1.metric("Copy-trade equity", money(eq),
              help="Total value of the copy-trading account: starting capital plus all profit/loss.")
    c2.metric("Drawdown", pct(dd),
              help="How far below the highest-ever value we currently are. 0% is best. The system force-stops at 15%.")
    open_events = _one(ct, "SELECT COUNT(*) FROM safety_events WHERE resolved_at IS NULL", 0)
    c3.metric("Safety stops", str(open_events),
              delta=None if not open_events else "ATTENTION",
              help="Automatic emergency brakes that have fired and not been cleared. Should be 0.")
    live_subs = _one(ct, "SELECT COUNT(*) FROM subscriptions WHERE status='active' AND mode='live'", 0)
    paper_subs = _one(ct, "SELECT COUNT(*) FROM subscriptions WHERE status='active' AND mode='paper'", 0)
    c4.metric("Live / paper positions", f"{live_subs} / {paper_subs}",
              help="Live = real money copying a trader. Paper = simulated with fake money to test the system.")
    klocks = _one(kc, "SELECT COUNT(*) FROM governance_locks WHERE released_at IS NULL", 0)
    c5.metric("Kalshi locks", str(klocks),
              help="Safety locks on the prediction-market system. Should be 0 unless something went wrong.")
    if open_events or klocks:
        st.error("⚠️ A safety mechanism is engaged. Check the Safety & Risk page before doing anything else.")


status_strip()
st.divider()


# ---------- pages -------------------------------------------------------------
if page.startswith("📋"):
    st.header("What is this system?")
    st.markdown("""
**SniperVRT runs two automated trading systems**, both designed to make money with
strict safety limits:

### 1. 🏦 Copy-Trading (Hyperliquid)
We don't pick crypto trades ourselves. Instead, the system finds **the best
human traders** on a crypto exchange called Hyperliquid and automatically
puts our money behind them (this is called "copying" or depositing into
their **vault**). When they profit, we profit (they keep up to 10% of the
gains as their fee).

The clever part: Hyperliquid ranks traders by raw profit, which is a bad
signal — a lucky gambler looks the same as a skilled trader. **Our system
re-ranks them by consistency and risk control** (the "Sharpe ratio" — see
Glossary), and spreads our money across up to 10 of the best, never more
than 30% with any single trader.

### 2. 🎲 Prediction Markets (Kalshi)
Kalshi is a US-regulated exchange where you trade YES/NO contracts on real
events ("Will inflation be above 3%?"). Each contract costs 1–99 cents and
pays $1 if you're right. **Our system estimates the true probability of
each event** and only trades when the market's price is meaningfully wrong
— like a card-counter who only bets when the odds are in their favor.

### Both systems share these safety rules
- **Nothing trades real money by default.** Multiple switches must all be ON.
- **Paper mode** (fake money, real data) must succeed first.
- **Automatic stops**: portfolio down 15% → everything halts.
- **Every decision is recorded** in a database — you can audit anything.

### Where to look first
1. **💰 Money** — current value and profit/loss
2. **🛡️ Safety & Risk** — anything broken or halted?
3. **🚦 Go-Live Checklist** — what stands between us and live trading
""")

elif page.startswith("💰"):
    st.header("💰 Money — where is it and how is it doing")
    st.caption("Everything about capital: how much, where, and whether we're winning.")

    snaps = _df(ct, "SELECT * FROM portfolio_snapshots ORDER BY captured_at")
    if snaps.empty:
        st.info("No portfolio data yet. The system records a money snapshot every "
                "15 minutes once the worker is running. Start it with: "
                "`copy-trade worker`")
    else:
        snaps["equity"] = snaps["total_capital"] + snaps["realized_pnl"] + snaps["unrealized_pnl"]
        st.subheader("Equity over time")
        st.caption("The single most important chart: total account value. Up and to the right = good.")
        st.line_chart(snaps.set_index("captured_at")["equity"])

        latest = snaps.iloc[-1]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Starting capital", money(latest["total_capital"]),
                  help="The money we put in.")
        c2.metric("Realized P&L", money(latest["realized_pnl"]),
                  help="Profit/loss that is locked in — we already withdrew it from traders' vaults.")
        c3.metric("Unrealized P&L", money(latest["unrealized_pnl"]),
                  help="Profit/loss on paper — still sitting inside traders' vaults, could still change.")
        c4.metric("Deployed", money(latest["deployed_usdt"]),
                  help="How much of our capital is currently working (deposited with traders) vs sitting idle.")

    st.subheader("Money per trader")
    st.caption("Each row is one trader we're copying. 'Allocated' is how much of our money rides with them.")
    subs = _df(ct, """
        SELECT s.mode, m.nickname AS trader, s.master_uid AS vault,
               s.allocated_usdt AS allocated, s.subscribed_at,
               (SELECT total_pnl FROM subscription_pnl
                WHERE subscription_id = s.id ORDER BY id DESC LIMIT 1) AS pnl
        FROM subscriptions s JOIN masters m ON m.uid = s.master_uid
        WHERE s.status = 'active' ORDER BY s.mode, allocated DESC
    """)
    if subs.empty:
        st.info("Not copying anyone yet. Run `copy-trade rebalance` (dry-run) to see "
                "what the system would do, or `copy-trade paper-rebalance` to simulate.")
    else:
        st.dataframe(subs, use_container_width=True)

elif page.startswith("🤖"):
    st.header("🤖 Decisions — what the system chose and why")
    st.caption("The system's 'thought process': how it ranks traders and what it decided to do with money.")

    st.subheader("Latest trader rankings")
    st.markdown("""
The system scores every trader 0–1. **Higher = better.** The score mixes:
- **40%** Sharpe ratio (steady profits beat lucky streaks)
- **25%** Calmar ratio (profit relative to worst losing streak)
- **15%** profit factor, **10%** win rate, **10%** consistency

A trader can be **blocked** ("eligible = 0") even with a high score —
the reason column tells you why (too new, drawdown too big, vault too small...).
""")
    scores = _df(ct, """
        SELECT ts.rank_at_time AS rank, m.nickname AS trader,
               ts.master_uid AS vault, ROUND(ts.composite_score, 4) AS score,
               ROUND(ts.sharpe_est, 2) AS sharpe, ROUND(ts.calmar_est, 2) AS calmar,
               ts.eligible, ts.filter_reason AS blocked_because, ts.scored_at
        FROM trader_scores ts JOIN masters m ON m.uid = ts.master_uid
        WHERE ts.scored_at = (SELECT MAX(scored_at) FROM trader_scores)
        ORDER BY ts.rank_at_time LIMIT 30
    """)
    if scores.empty:
        st.info("No rankings yet — run `copy-trade poll-leaderboard` to scan traders.")
    else:
        st.dataframe(scores, use_container_width=True)

    st.subheader("Allocation decisions (history)")
    st.markdown("""
Every time the system rebalances, it logs one row per trader:
- **subscribe** — start copying (deposit money)
- **keep** — no change needed
- **rebalance** — adjust how much money rides with them
- **unsubscribe** — stop copying (withdraw)
""")
    allocs = _df(ct, """
        SELECT decided_at, master_uid AS vault, action,
               ROUND(allocated_usdt, 2) AS target_usd, ROUND(score, 4) AS score
        FROM allocations ORDER BY decided_at DESC LIMIT 50
    """)
    if allocs.empty:
        st.info("No allocation decisions yet — run `copy-trade rebalance` (safe dry-run).")
    else:
        st.dataframe(allocs, use_container_width=True)

    st.subheader("Did the rankings actually predict profits?")
    st.caption("Spearman correlation between our scores and later real profits. "
               "Above +0.2 = the ranking works. Near 0 = ranking is noise. "
               "Negative = ranking is actively wrong.")
    try:
        from copy_trade.validation.tracker import score_prediction_accuracy
        if ct:
            acc = score_prediction_accuracy(ct)
            corr = acc.get("rank_correlation")
            if corr is None:
                st.info(f"Not enough data yet (need at least 3 traders with PnL history; have {acc.get('n', 0)}).")
            else:
                st.metric("Rank correlation", f"{corr:+.2f}")
    except Exception as e:
        st.warning(f"Could not compute: {e}")

elif page.startswith("🏦"):
    st.header("🏦 Copy-Trading on Hyperliquid")
    st.caption("Detailed view of the trader-copying system.")

    st.subheader("Traders we know about")
    masters = _df(ct, """
        SELECT m.nickname AS trader, m.uid AS vault, m.active,
               m.first_seen_at, m.last_seen_at,
               (SELECT COUNT(*) FROM trader_snapshots WHERE master_uid = m.uid) AS snapshots
        FROM masters m ORDER BY m.active DESC, m.last_seen_at DESC LIMIT 100
    """)
    n_active = _one(ct, "SELECT COUNT(*) FROM masters WHERE active=1", 0)
    n_gone = _one(ct, "SELECT COUNT(*) FROM masters WHERE active=0", 0)
    c1, c2 = st.columns(2)
    c1.metric("Traders on leaderboard now", str(n_active))
    c2.metric("Traders that disappeared", str(n_gone),
              help="Traders that fell off the leaderboard. We keep their history — "
                   "if many of our past picks disappeared, our picking is bad (survivorship check).")
    if not masters.empty:
        st.dataframe(masters, use_container_width=True)
    else:
        st.info("Nothing scanned yet. Run `copy-trade poll-leaderboard` — it's free and read-only.")

    st.subheader("Lockups")
    st.markdown("Hyperliquid rule: after depositing with a trader, money is **locked for 24 hours** — "
                "we can't withdraw until the lock expires. This shows what's locked right now.")
    now_ms = int(time.time() * 1000)
    locked = _df(ct, f"""
        SELECT m.nickname AS trader, s.allocated_usdt AS amount,
               datetime(s.lockup_until_ms / 1000, 'unixepoch') AS unlocks_at_utc
        FROM subscriptions s JOIN masters m ON m.uid = s.master_uid
        WHERE s.status='active' AND s.lockup_until_ms > {now_ms}
    """)
    if locked.empty:
        st.success("Nothing locked — all funds are withdrawable right now.")
    else:
        st.dataframe(locked, use_container_width=True)

    st.subheader("Backtests")
    st.caption("Replays of the strategy against history. 'Return' is hypothetical profit; "
               "'Sharpe' above 1 is good; 'max_drawdown' is the worst dip along the way.")
    bts = _df(ct, """
        SELECT id, started_at, tick_count AS ticks,
               ROUND(total_return, 4) AS return, ROUND(sharpe, 2) AS sharpe,
               ROUND(max_drawdown, 4) AS max_drawdown, notes
        FROM backtest_runs ORDER BY id DESC LIMIT 20
    """)
    if bts.empty:
        st.info("No backtests yet. Needs ~7 days of leaderboard snapshots, then run `copy-trade backtest`.")
    else:
        st.dataframe(bts, use_container_width=True)

elif page.startswith("🎲"):
    st.header("🎲 Prediction Markets (Kalshi)")
    st.caption("The system scans event markets and flags ones it believes are mispriced.")

    if kc is None:
        st.info("Kalshi database not found. Run `kalshi-engine init-db` then `kalshi-engine scan` inside kalshi/.")
    else:
        st.subheader("Current trade ideas (signals)")
        st.markdown("""
Each row = the system thinks a market is priced wrong:
- **fair** = what WE think the probability is
- **implied** = what the MARKET price says the probability is
- **edge** = the gap. Bigger gap = bigger opportunity (if we're right)
- Status 'pending' = waiting for a human to approve before any real trade
""")
        sigs = _df(kc, """
            SELECT ticker, side, ROUND(fair_prob, 2) AS fair,
                   ROUND(implied_prob, 2) AS implied, ROUND(edge, 3) AS edge,
                   ROUND(confidence, 2) AS confidence,
                   ROUND(suggested_size_usd, 2) AS size_usd, status, created_at
            FROM signals ORDER BY created_at DESC LIMIT 30
        """)
        if sigs.empty:
            st.info("No signals yet — run `kalshi-engine scan` to scan markets.")
        else:
            st.dataframe(sigs, use_container_width=True)

        st.subheader("Why ideas were rejected")
        st.caption("The system rejects far more than it accepts — that's by design. "
                   "This shows the most common rejection reasons.")
        rejs = _df(kc, """
            SELECT reason, COUNT(*) AS count FROM rejected_signals
            GROUP BY reason ORDER BY count DESC LIMIT 15
        """)
        if not rejs.empty:
            st.bar_chart(rejs.set_index("reason")["count"])
        else:
            st.info("No rejections logged yet.")

        st.subheader("Paper trading results")
        ppnl = _df(kc, "SELECT * FROM paper_pnl ORDER BY date_iso DESC LIMIT 30")
        if not ppnl.empty:
            st.dataframe(ppnl, use_container_width=True)
        else:
            st.info("No paper trades yet — run `kalshi-engine scan --paper --orderbook`.")

        st.subheader("Calibration — is our probability model honest?")
        st.markdown("""
When we say "70% likely", does it happen ~70% of the time? Perfect calibration
means the dots below sit on the diagonal. This needs resolved markets, so it
fills in over weeks.
""")
        cal = _df(kc, """
            SELECT p.fair_prob, r.outcome FROM probability_estimates p
            JOIN resolutions r ON r.ticker = p.ticker WHERE r.outcome IN (0, 1)
        """)
        if cal.empty:
            st.info("No resolved markets yet — calibration appears after markets settle.")
        else:
            cal["bucket"] = (cal["fair_prob"] * 10).astype(int) / 10
            agg = cal.groupby("bucket").agg(predicted=("fair_prob", "mean"),
                                             actual=("outcome", "mean"))
            st.line_chart(agg)

elif page.startswith("🛡️"):
    st.header("🛡️ Safety & Risk")
    st.caption("Every automatic brake in the system and whether any have fired.")

    st.subheader("The safety systems, in plain English")
    st.markdown("""
| Brake | What it does | Trigger |
|---|---|---|
| **Portfolio stop** | Halts ALL new copying | Account down 15% from peak |
| **Vault watchdog** | Auto-withdraws from ONE trader | That trader down 20% since we joined |
| **Concentration lock** | Blocks new deposits | Traders too similar (correlation > 0.5) or one human controls > 30% of our money |
| **Liquidity cap** | Blocks new deposits | More than 80% of money is in 24h lockups |
| **Daily stop (Kalshi)** | Halts prediction trades for the day | Down $10 in a day |
| **Manual approval** | Human must click approve | Every live trade, always |
""")

    st.subheader("Safety events (fired brakes)")
    events = _df(ct, "SELECT event_at, event_type, detail, resolved_at FROM safety_events ORDER BY id DESC LIMIT 30")
    if events.empty:
        st.success("✅ No safety events have ever fired.")
    else:
        unresolved = events[events["resolved_at"].isna()]
        if not unresolved.empty:
            st.error(f"⚠️ {len(unresolved)} UNRESOLVED events — these are blocking new trades.")
        st.dataframe(events, use_container_width=True)

    st.subheader("Kalshi governance locks")
    glocks = _df(kc, "SELECT name, reason, engaged_at, released_at FROM governance_locks ORDER BY id DESC LIMIT 20")
    if glocks.empty:
        st.success("✅ No Kalshi locks have ever engaged.")
    else:
        st.dataframe(glocks, use_container_width=True)

    st.subheader("Stress test — what would a crash cost us?")
    st.caption("Hypothetical scenarios against current positions. 'Breach' means the loss "
               "would trip the 15% emergency stop.")
    try:
        from copy_trade.config import CopyTradeSettings
        from copy_trade.risk.stress import stress_test
        if ct:
            s = CopyTradeSettings(db_path=COPY_DB)
            rows = stress_test(ct, s)
            if rows:
                st.dataframe(pd.DataFrame(rows), use_container_width=True)
            else:
                st.info("No live positions yet, so a crash would cost $0.")
    except Exception as e:
        st.warning(f"Could not run stress test: {e}")

    st.subheader("Risk snapshots")
    risk = _df(ct, "SELECT * FROM portfolio_risk_snapshots ORDER BY captured_at DESC LIMIT 10")
    if not risk.empty:
        st.dataframe(risk, use_container_width=True)
    else:
        st.info("Run `copy-trade risk-snapshot` to record correlation / concentration / liquidity metrics.")

elif page.startswith("🚦"):
    st.header("🚦 Go-Live Checklist")
    st.markdown("""
Before the system is allowed to touch real money, **every item below must be
green**. This is enforced in code — the `--live` flag refuses to run otherwise.
""")
    try:
        from copy_trade.config import CopyTradeSettings
        from copy_trade.validation.promotion import check_ready_for_live
        if ct:
            s = CopyTradeSettings(db_path=COPY_DB)
            rep = check_ready_for_live(ct, s)
            if rep.ready:
                st.success("🟢 LIVE READY — all gates passed.")
            else:
                st.warning(f"🔒 LIVE LOCKED — {len(rep.blockers)} gates still red.")
            gate_help = {
                "history_days": "We need ≥ 7 days of trader data before trusting any ranking. Fix: run `copy-trade poll-leaderboard` daily (it's free).",
                "paper_runs_7d": "The simulator must run ≥ 3 times in the last week. Fix: run `copy-trade paper-rebalance`.",
                "spearman": "Our rankings must actually predict profits (correlation ≥ 0.2). Fix: accumulate paper-trading history.",
                "testnet": "One full deposit-withdraw cycle on Hyperliquid's TEST network must succeed. Fix: `copy-trade testnet-roundtrip`.",
                "live_enabled": "The master switch in settings. Fix: set LIVE_ENABLED=true in .env (only after everything else is green!).",
                "live_dry_run": "The second master switch. Fix: set LIVE_DRY_RUN=false in .env.",
                "manual_approval": "Human-in-the-loop must stay ON. Never disable this.",
                "safety_events": "All fired brakes must be investigated and cleared.",
                "emergency_force_unwind": "Auto-withdraw-everything on crash must be enabled. Fix: set EMERGENCY_FORCE_UNWIND=true.",
            }
            for b in rep.blockers:
                explanation = next((v for k, v in gate_help.items() if k in b), "")
                st.error(f"🔴 **{b}**\n\n{explanation}")
            st.subheader("Details")
            st.json(rep.details)
    except Exception as e:
        st.warning(f"Could not evaluate: {e}")

    st.subheader("Manual pre-flight (not enforced by code, still required)")
    st.markdown("""
- [ ] Encrypted keystore created: `copy-trade keystore-init` (a fresh wallet, never your main one)
- [ ] Slack or Discord webhook set, so your phone buzzes on safety events
- [ ] Backup cron installed: `infra/backup.sh` every 6 hours
- [ ] First live amount ≤ **$200** — treat it as a final test, not an investment
- [ ] You know how to stop everything: `copy-trade lock` / kill the worker / `safety_events` stays engaged until YOU clear it
""")

elif page.startswith("🧪"):
    st.header("🧪 Testing & Validation")
    st.caption("Proof the system works before money is at risk.")

    st.subheader("Validation runs")
    vruns = _df(ct, "SELECT kind, started_at, passed_at, notes FROM validation_runs ORDER BY id DESC LIMIT 20")
    if vruns.empty:
        st.info("No validation runs yet. The key one is `copy-trade testnet-roundtrip`.")
    else:
        st.dataframe(vruns, use_container_width=True)

    st.subheader("Paper rebalance history")
    st.caption("Each row = one full simulated rebalance. The go-live gate requires ≥ 3 in the last 7 days.")
    pruns = _df(ct, "SELECT ran_at, counts_json, success FROM paper_rebalance_runs ORDER BY id DESC LIMIT 20")
    if pruns.empty:
        st.info("None yet — run `copy-trade paper-rebalance`.")
    else:
        st.dataframe(pruns, use_container_width=True)

    st.subheader("Data quality failures")
    st.caption("Bad data the system caught and refused to use (negative prices, impossible dates...). "
               "A few is normal; hundreds means an API changed shape.")
    dq = _df(ct, "SELECT captured_at, source, reason FROM data_quality_failures ORDER BY id DESC LIMIT 20")
    if dq.empty:
        st.success("✅ No bad data caught (or nothing scanned yet).")
    else:
        st.dataframe(dq, use_container_width=True)

    st.subheader("Recent system events (the log)")
    evs = _df(ct, "SELECT ts, level, event, payload_json FROM events ORDER BY id DESC LIMIT 50")
    if evs.empty:
        st.info("Event log is empty — fills once the worker runs.")
    else:
        st.dataframe(evs, use_container_width=True)

elif page.startswith("📖"):
    st.header("📖 Glossary")
    st.markdown("""
| Term | Plain English |
|---|---|
| **Vault** | A trader's public fund on Hyperliquid. Deposit = you copy them automatically. |
| **Sharpe ratio** | Profit per unit of rollercoaster. 1+ is good, 2+ is excellent. A coin-flipper has ~0. |
| **Calmar ratio** | Yearly profit divided by worst losing streak. Rewards traders who never blow up. |
| **Drawdown (MDD)** | The deepest dip from a peak. A 50% drawdown needs a 100% gain just to break even. |
| **Edge** | The gap between what we think the odds are and what the market is charging. Our profit source. |
| **Paper trading** | Fake money, real market data. The dress rehearsal. |
| **Backtest** | Replaying the strategy against history: "what WOULD have happened." |
| **Walk-forward** | A stricter backtest that never peeks at the future. |
| **Spearman correlation** | "Did our #1 pick actually do better than our #10 pick?" +1 = perfect ranking, 0 = random. |
| **Lockup** | Hyperliquid freezes deposits for 24h. We track it so we never promise money we can't move. |
| **Kelly criterion** | Math for bet sizing: bet more when edge is bigger, never enough to risk ruin. We use 1/4 strength. |
| **VaR (95%)** | "On a bad day (worst 1-in-20), expect to lose about this much." |
| **Slippage** | The price moved between deciding and executing. Death by a thousand cuts if ignored. |
| **Spread** | Gap between buy and sell price. Wide spread = expensive to trade. |
| **Calibration** | When we say 70%, it should happen 70% of the time. Honesty check for the model. |
| **Survivorship bias** | Only seeing winners (losers vanished from the leaderboard). We keep loser history to avoid it. |
| **Circuit breaker** | After repeated API failures, stop hammering and cool down 5 minutes. |
| **Testnet** | Hyperliquid's practice environment with fake money. Where we prove the plumbing works. |
""")
