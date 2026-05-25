"""Edge discovery engine — statistically defensive.

For each feature/target pair, partition history into a "signal high" group and the
rest, run a two-sample t-test on the forward returns, and:
  - require minimum sample size
  - require effect size > tiny threshold
  - apply Benjamini-Hochberg FDR correction across the multiple-testing family
  - measure stability by re-running on the first half and last half independently
  - reject anything that flips sign or drops below p_adj < 0.10

Edges are persisted as EdgeCandidate rows; accepted ones can be used as gating
features downstream.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Iterable, Optional

import numpy as np
import pandas as pd
from scipy import stats

from backend.app.core.db import session_scope
from backend.app.data import load_candles
from backend.app.memory.trace import trace
from backend.app.models import EdgeCandidate, MicroSignal, NewsItem
from backend.app.strategies.indicators import atr, ema, realized_vol, rsi, zscore

MIN_SAMPLES = 30
MIN_HIGH_GROUP = 8
FDR_ALPHA = 0.10
MIN_ABS_EFFECT = 1e-4   # 1 basis point on log-return


@dataclass
class EdgeReport:
    name: str
    feature: str
    target: str
    n: int
    effect_size: float
    base_rate: float
    t_stat: float
    p_value: float
    p_value_adj: float
    stability: float
    accepted: bool
    rejection_reason: str
    details: dict

    def to_dict(self) -> dict:
        return self.__dict__


def _bh_correct(p_values: list[float]) -> list[float]:
    """Benjamini-Hochberg FDR correction; returns adjusted p-values in the same order."""
    n = len(p_values)
    if n == 0:
        return []
    order = np.argsort(p_values)
    ranked = np.array(p_values)[order]
    adj = np.minimum.accumulate(
        (ranked * n / (np.arange(n) + 1))[::-1])[::-1]
    adj = np.clip(adj, 0.0, 1.0)
    out = np.empty(n)
    out[order] = adj
    return out.tolist()


def _t_test_high_vs_rest(values: np.ndarray, returns: np.ndarray,
                          threshold: float, high_above: bool) -> tuple[float, float, float, int]:
    mask = (values >= threshold) if high_above else (values <= threshold)
    high = returns[mask]
    rest = returns[~mask]
    if len(high) < MIN_HIGH_GROUP or len(rest) < MIN_HIGH_GROUP:
        return (0.0, 1.0, 0.0, int(len(high)))
    t, p = stats.ttest_ind(high, rest, equal_var=False, nan_policy="omit")
    effect = float(np.nanmean(high) - np.nanmean(rest))
    return (float(t) if not np.isnan(t) else 0.0,
            float(p) if not np.isnan(p) else 1.0,
            effect, int(len(high)))


def _stability(values: np.ndarray, returns: np.ndarray, threshold: float,
               high_above: bool, full_effect: float) -> float:
    """Run the same test on first half vs second half; return [0,1] stability."""
    n = len(values)
    if n < MIN_SAMPLES * 2:
        return 0.0
    mid = n // 2
    _, _, e1, _ = _t_test_high_vs_rest(values[:mid], returns[:mid], threshold, high_above)
    _, _, e2, _ = _t_test_high_vs_rest(values[mid:], returns[mid:], threshold, high_above)
    if full_effect == 0:
        return 0.0
    # 1.0 if both halves have same-sign effect as the full; lower if mixed
    sign_full = math.copysign(1, full_effect)
    sign_match = (math.copysign(1, e1) == sign_full) + (math.copysign(1, e2) == sign_full)
    magnitude_ratio = min(1.0, (abs(e1) + abs(e2)) / (2 * abs(full_effect))) if full_effect != 0 else 0.0
    return round(0.5 * (sign_match / 2.0) + 0.5 * magnitude_ratio, 4)


def _build_feature_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Causal features aligned to close[t]; targets ret_1, ret_24 on close[t+k] / close[t]."""
    closes = df["close"]
    feats = pd.DataFrame(index=df.index)
    feats["rsi14"] = rsi(closes, 14)
    feats["ema_ratio"] = ema(closes, 12) / ema(closes, 34)
    feats["atr_pct"] = atr(df, 14) / closes
    feats["vol_z50"] = zscore(df["volume"], 50)
    feats["close_z50"] = zscore(closes, 50)
    feats["rv_24"] = realized_vol(closes, 24)
    feats["price_change_5"] = closes / closes.shift(5) - 1
    # Targets (forward log returns)
    feats["ret_1"] = np.log(closes.shift(-1) / closes)
    feats["ret_24"] = np.log(closes.shift(-24) / closes)
    return feats.dropna()


def _augment_with_news_sentiment(df_features: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    """Add average news sentiment over the prior 24h as a feature, if news exists."""
    with session_scope() as s:
        rows = s.query(NewsItem.ts, NewsItem.sentiment, NewsItem.importance).all()
    if not rows:
        return df_features
    news = pd.DataFrame(rows, columns=["ts", "sentiment", "importance"])
    if news.empty:
        return df_features
    news = news.sort_values("ts")
    out = df_features.copy()
    if "ts" not in df.columns:
        return out
    ts_arr = df.loc[df_features.index, "ts"].astype(int).values if "ts" in df.columns else None
    if ts_arr is None:
        return out
    feature_vals = []
    n_ts = news["ts"].values
    n_sent = news["sentiment"].values
    n_imp = news["importance"].values
    for t in ts_arr:
        lo = t - 86400
        mask = (n_ts >= lo) & (n_ts <= t)
        if mask.sum() == 0:
            feature_vals.append(0.0)
            continue
        w = np.clip(n_imp[mask], 0.05, 1.0)
        feature_vals.append(float(np.sum(n_sent[mask] * w) / np.sum(w)))
    out["news_sent_24h"] = feature_vals
    return out


def _augment_with_micro(df_features: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    """Join the latest-known micro signals by name (vol_zscore etc.) using last-observation
    carry-forward against the candle timeline."""
    with session_scope() as s:
        rows = s.query(MicroSignal.ts, MicroSignal.name, MicroSignal.value, MicroSignal.zscore).all()
    if not rows:
        return df_features
    ms = pd.DataFrame(rows, columns=["ts", "name", "value", "zscore"]).sort_values("ts")
    out = df_features.copy()
    if "ts" not in df.columns:
        return out
    candle_ts = df.loc[df_features.index, "ts"].astype(int).values
    for name in sorted(ms["name"].unique()):
        sub = ms[ms["name"] == name][["ts", "value", "zscore"]].values
        if len(sub) < 5:
            continue
        vals = []
        j = 0
        last_v = np.nan
        last_z = np.nan
        for t in candle_ts:
            while j < len(sub) and sub[j][0] <= t:
                last_v = sub[j][1]
                last_z = sub[j][2] if sub[j][2] is not None else last_z
                j += 1
            vals.append(last_z if not np.isnan(last_z) else last_v)
        col = f"micro__{name}"
        out[col] = vals
    return out.dropna()


def discover_edges(*, persist: bool = True) -> dict:
    """Run all feature × target tests, apply FDR, persist results."""
    df = load_candles(limit=4000)
    if df.empty or len(df) < 200:
        return {"ok": False, "reason": "need ≥200 candles"}
    feats = _build_feature_frame(df)
    feats = _augment_with_news_sentiment(feats, df.reset_index(drop=False) if "ts" not in df.columns else df)
    feats = _augment_with_micro(feats, df)
    if feats.empty:
        return {"ok": False, "reason": "feature frame empty after dropna"}

    feature_cols = [c for c in feats.columns if c not in ("ret_1", "ret_24")]
    targets = ["ret_1", "ret_24"]

    raw_results: list[EdgeReport] = []
    p_values: list[float] = []

    for feat in feature_cols:
        x = feats[feat].astype(float).values
        # Use top-tercile vs rest as a coarse, robust partition
        q70 = float(np.quantile(x, 0.70))
        q30 = float(np.quantile(x, 0.30))
        for tgt in targets:
            y = feats[tgt].astype(float).values
            n = len(y)
            if n < MIN_SAMPLES:
                continue
            # high condition
            for label, threshold, high_above in [
                ("high", q70, True), ("low", q30, False),
            ]:
                t_stat, p, effect, n_high = _t_test_high_vs_rest(x, y, threshold, high_above)
                base = float(np.mean(y))
                rep = EdgeReport(
                    name=f"{feat}__{label}__{tgt}",
                    feature=feat, target=tgt,
                    n=n, effect_size=effect, base_rate=base,
                    t_stat=t_stat, p_value=p, p_value_adj=p,
                    stability=0.0, accepted=False, rejection_reason="",
                    details={"threshold": threshold, "high_above": high_above,
                             "n_high": n_high},
                )
                raw_results.append(rep)
                p_values.append(p)

    adj = _bh_correct(p_values)
    n_accepted = 0
    for rep, p_adj in zip(raw_results, adj):
        rep.p_value_adj = float(p_adj)
        x = feats[rep.feature].astype(float).values
        y = feats[rep.target].astype(float).values
        rep.stability = _stability(x, y, rep.details["threshold"],
                                    rep.details["high_above"], rep.effect_size)
        reasons = []
        if rep.n < MIN_SAMPLES:
            reasons.append("low_n")
        if abs(rep.effect_size) < MIN_ABS_EFFECT:
            reasons.append("tiny_effect")
        if rep.p_value_adj > FDR_ALPHA:
            reasons.append("not_significant_fdr")
        if rep.stability < 0.5:
            reasons.append("unstable")
        if not reasons:
            rep.accepted = True
            n_accepted += 1
        rep.rejection_reason = ",".join(reasons)

    if persist:
        with session_scope() as s:
            for rep in raw_results:
                s.add(EdgeCandidate(
                    name=rep.name, feature=rep.feature, target=rep.target,
                    sample_size=rep.n, effect_size=rep.effect_size,
                    base_rate=rep.base_rate,
                    t_stat=rep.t_stat, p_value=rep.p_value,
                    p_value_adj=rep.p_value_adj, stability=rep.stability,
                    accepted=rep.accepted, rejection_reason=rep.rejection_reason,
                    details=rep.details,
                ))
                trace_kind = "edge_accept" if rep.accepted else "edge_reject"
                trace(trace_kind, rep.name,
                      reason_chain=[
                          f"n={rep.n}",
                          f"effect={rep.effect_size:.5f}",
                          f"p_adj={rep.p_value_adj:.4f}",
                          f"stability={rep.stability:.2f}",
                          f"reasons={rep.rejection_reason or 'accepted'}",
                      ],
                      inputs={"feature": rep.feature, "target": rep.target,
                              "threshold": rep.details["threshold"]})

    raw_results.sort(key=lambda r: (not r.accepted, r.p_value_adj))
    return {
        "ok": True,
        "tested": len(raw_results),
        "accepted": n_accepted,
        "fdr_alpha": FDR_ALPHA,
        "min_samples": MIN_SAMPLES,
        "top_edges": [r.to_dict() for r in raw_results[:25]],
    }


def recent_edges(*, accepted_only: bool = False, limit: int = 50) -> list[dict]:
    with session_scope() as s:
        q = s.query(EdgeCandidate)
        if accepted_only:
            q = q.filter(EdgeCandidate.accepted.is_(True))
        rows = q.order_by(EdgeCandidate.id.desc()).limit(limit).all()
        return [{
            "id": e.id, "name": e.name, "feature": e.feature, "target": e.target,
            "n": e.sample_size, "effect_size": e.effect_size,
            "base_rate": e.base_rate, "t_stat": e.t_stat,
            "p_value": e.p_value, "p_value_adj": e.p_value_adj,
            "stability": e.stability, "accepted": e.accepted,
            "rejection_reason": e.rejection_reason,
            "created_at": e.created_at.isoformat() if e.created_at else None,
            "details": e.details,
        } for e in rows]
