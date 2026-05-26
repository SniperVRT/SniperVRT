"""Streamlit dashboard. Intentionally tiny: scanner, ranked signals,
rejections, risk status, bankroll, PnL.

Run with: `streamlit run kalshi_engine/dashboard.py`
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from .config import get_settings
from .db import connect, init_db
from .risk import rules as risk_rules
from .validation.calibration import calibration_report

st.set_page_config(page_title="Kalshi Mispricing Engine", layout="wide")

settings = get_settings()
init_db()
conn = connect()

st.title("Kalshi mispricing engine")
st.caption(
    f"mode={settings.execution_mode.value} | bankroll=${settings.bankroll_usd:.0f} | "
    f"daily stop=${settings.daily_loss_stop_usd:.0f} | max open={settings.max_open_positions}"
)

# ---- risk strip ------------------------------------------------------------
day_pnl = risk_rules.realized_pnl_today(conn)
week_pnl = risk_rules.realized_pnl_week(conn)
open_n = risk_rules.open_position_count(conn)
daily_remaining = max(0.0, settings.daily_loss_stop_usd + day_pnl)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Day PnL", f"${day_pnl:+.2f}")
c2.metric("Week PnL", f"${week_pnl:+.2f}")
c3.metric("Daily loss remaining", f"${daily_remaining:.2f}")
c4.metric("Open positions", f"{open_n}/{settings.max_open_positions}")

# ---- ranked signals --------------------------------------------------------
st.subheader("Ranked signals (pending)")
df_signals = pd.read_sql_query(
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
st.dataframe(df_signals, use_container_width=True, hide_index=True)

# ---- rejections ------------------------------------------------------------
st.subheader("Recent rejections")
df_rej = pd.read_sql_query(
    """
    SELECT ticker, strategy, reason, detail_json, created_at
      FROM rejected_signals
     ORDER BY id DESC
     LIMIT 100
    """,
    conn,
)
st.dataframe(df_rej, use_container_width=True, hide_index=True)

# ---- open positions --------------------------------------------------------
st.subheader("Open positions")
df_pos = pd.read_sql_query(
    """
    SELECT ticker, side, qty, avg_cost_cents, opened_at
      FROM positions
     WHERE closed_at IS NULL
     ORDER BY opened_at DESC
    """,
    conn,
)
st.dataframe(df_pos, use_container_width=True, hide_index=True)

# ---- calibration -----------------------------------------------------------
st.subheader("Calibration (all strategies)")
rep = calibration_report(conn)
st.write(f"n={rep.n} Brier={rep.brier:.4f}")
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

st.caption(f"refreshed {datetime.now(timezone.utc).isoformat()}")
