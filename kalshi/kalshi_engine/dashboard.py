"""Streamlit operational dashboard.

Intentionally tight. Pages:
  - Overview / risk strip
  - Signal queue (pending) with approve / reject buttons
  - Paper trades + open positions
  - Rejections & quality
  - Calibration & performance

Run with: `streamlit run kalshi_engine/dashboard.py`
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from .config import get_settings
from .db import connect, init_db, utc_now_iso
from .reports.daily import build_daily_report, render_daily_report_text
from .risk import rules as risk_rules
from .validation.calibration import calibration_report
from .validation.metrics import performance_report

st.set_page_config(page_title="Kalshi mispricing engine", layout="wide")

settings = get_settings()
init_db()
conn = connect()

# -- Risk strip --------------------------------------------------------------
day_pnl = risk_rules.realized_pnl_today(conn)
week_pnl = risk_rules.realized_pnl_week(conn)
open_n = risk_rules.open_position_count(conn)
daily_remaining = max(0.0, settings.daily_loss_stop_usd + day_pnl)

st.title("Kalshi mispricing engine")
top1, top2, top3, top4, top5 = st.columns(5)
top1.metric("Mode", settings.execution_mode.value.upper())
top2.metric("Day PnL", f"${day_pnl:+.2f}")
top3.metric("Week PnL", f"${week_pnl:+.2f}")
top4.metric("Daily loss remaining", f"${daily_remaining:.2f}")
top5.metric("Open positions", f"{open_n}/{settings.max_open_positions}")

live_ready = (
    settings.execution_mode.value != "live"
    or daily_remaining > 0
)
st.write(
    f"**Status:** {'LIVE_LOCKED — manual approval required' if settings.execution_mode.value != 'live' else 'LIVE_READY'}"
)

page = st.sidebar.radio(
    "Pages",
    ["Signals", "Paper", "Rejections & Quality", "Calibration", "Daily report"],
)

# -- Signals page ------------------------------------------------------------
if page == "Signals":
    st.subheader("Ranked pending signals")
    df = pd.read_sql_query(
        """
        SELECT s.id, s.ticker, s.side, s.strategy,
               ROUND(s.fair_prob, 3) AS fair,
               ROUND(s.implied_prob, 3) AS implied,
               ROUND(s.edge, 3) AS edge,
               ROUND(s.expected_value, 3) AS ev_per_dollar,
               ROUND(s.confidence, 2) AS conf,
               s.max_price_cents, s.suggested_size_usd, s.thesis, s.created_at
          FROM signals s
         WHERE s.status = 'pending'
         ORDER BY s.edge * s.confidence DESC
         LIMIT 200
        """,
        conn,
    )
    st.dataframe(df, use_container_width=True, hide_index=True)

    if not df.empty:
        sig_id = st.number_input("Signal ID to decide on", min_value=int(df["id"].min()),
                                 max_value=int(df["id"].max()), value=int(df["id"].iloc[0]))
        notes = st.text_input("Notes")
        col1, col2, col3 = st.columns(3)
        if col1.button("Approve"):
            conn.execute(
                "INSERT INTO approvals (signal_id, decision, notes, created_at) VALUES (?,?,?,?)",
                (sig_id, "approve", notes, utc_now_iso()),
            )
            conn.execute(
                "UPDATE signals SET status='approved', decided_at=? WHERE id=?",
                (utc_now_iso(), sig_id),
            )
            st.success(f"approved {sig_id}")
        if col2.button("Reject"):
            conn.execute(
                "INSERT INTO approvals (signal_id, decision, notes, created_at) VALUES (?,?,?,?)",
                (sig_id, "reject", notes, utc_now_iso()),
            )
            conn.execute(
                "UPDATE signals SET status='rejected', decided_at=? WHERE id=?",
                (utc_now_iso(), sig_id),
            )
            st.warning(f"rejected {sig_id}")
        if col3.button("Watchlist"):
            conn.execute(
                "INSERT INTO approvals (signal_id, decision, notes, created_at) VALUES (?,?,?,?)",
                (sig_id, "watchlist", notes, utc_now_iso()),
            )
            st.info(f"watchlisted {sig_id}")

# -- Paper page --------------------------------------------------------------
elif page == "Paper":
    st.subheader("Open paper positions")
    df_pos = pd.read_sql_query(
        """
        SELECT id, ticker, side, qty, avg_cost_cents, last_mark_cents,
               strategy, opened_at
          FROM paper_positions
         WHERE closed_at IS NULL
         ORDER BY opened_at DESC
        """,
        conn,
    )
    st.dataframe(df_pos, use_container_width=True, hide_index=True)

    st.subheader("Recent paper fills")
    df_f = pd.read_sql_query(
        """
        SELECT pf.id, po.ticker, po.side, pf.qty, pf.price_cents, pf.fee_usd, pf.filled_at
          FROM paper_fills pf
          JOIN paper_orders po ON po.id = pf.order_id
         ORDER BY pf.id DESC
         LIMIT 200
        """,
        conn,
    )
    st.dataframe(df_f, use_container_width=True, hide_index=True)

    st.subheader("Paper PnL by day")
    df_pnl = pd.read_sql_query("SELECT * FROM paper_pnl ORDER BY day DESC LIMIT 30", conn)
    st.dataframe(df_pnl, use_container_width=True, hide_index=True)

# -- Rejections & Quality ---------------------------------------------------
elif page == "Rejections & Quality":
    st.subheader("Recent rejections")
    df_rej = pd.read_sql_query(
        """
        SELECT ticker, strategy, reason, detail_json, created_at
          FROM rejected_signals
         ORDER BY id DESC
         LIMIT 200
        """,
        conn,
    )
    st.dataframe(df_rej, use_container_width=True, hide_index=True)

    st.subheader("Latest quality scores")
    df_q = pd.read_sql_query(
        """
        SELECT mq.ticker, ROUND(mq.total_score, 3) AS total,
               ROUND(mq.spread_score,2) AS spread,
               ROUND(mq.liquidity_score,2) AS liquidity,
               ROUND(mq.volume_score,2) AS volume,
               ROUND(mq.tradability_score,2) AS tradability,
               ROUND(mq.rules_score,2) AS rules,
               mq.flags_json, mq.captured_at
          FROM market_quality mq
         ORDER BY mq.id DESC LIMIT 200
        """,
        conn,
    )
    st.dataframe(df_q, use_container_width=True, hide_index=True)

    st.subheader("Resolution classifications")
    df_rc = pd.read_sql_query(
        """
        SELECT ticker, risk_class, ROUND(risk_score, 3) AS score,
               source_name, source_url, cutoff_at, ambiguity_flags_json, classified_at
          FROM resolution_classifications
         ORDER BY classified_at DESC LIMIT 200
        """,
        conn,
    )
    st.dataframe(df_rc, use_container_width=True, hide_index=True)

# -- Calibration page --------------------------------------------------------
elif page == "Calibration":
    rep = calibration_report(conn)
    perf = performance_report(conn)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Brier", f"{rep.brier:.4f}")
    c2.metric("Log loss", f"{perf.log_loss:.4f}")
    c3.metric("Profit factor", f"{perf.profit_factor:.2f}")
    c4.metric("Max drawdown", f"${perf.max_drawdown:.2f}")
    if rep.buckets:
        df_cal = pd.DataFrame([{
            "bucket": f"{b.bucket_lo:.0%}–{b.bucket_hi:.0%}",
            "n": b.n,
            "avg_predicted": b.avg_predicted,
            "realized_rate": b.realized_rate,
        } for b in rep.buckets])
        st.bar_chart(df_cal.set_index("bucket")[["avg_predicted", "realized_rate"]])
    else:
        st.info("No resolved markets yet — calibration will populate after settlements land.")

# -- Daily report ------------------------------------------------------------
elif page == "Daily report":
    rep = build_daily_report(conn)
    st.code(render_daily_report_text(rep))

st.caption(f"refreshed {datetime.now(timezone.utc).isoformat()}")
