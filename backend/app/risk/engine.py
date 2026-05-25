"""Risk engine — every trade decision passes through here. Logs all blocks."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from backend.app.core.config import get_config, get_settings
from backend.app.core.db import session_scope
from backend.app.models import RiskBlock, PaperAccount, Position
from backend.app.strategies.indicators import atr


@dataclass
class RiskCheck:
    allowed: bool
    size_base: float = 0.0
    stop_price: Optional[float] = None
    take_profit: Optional[float] = None
    blocks: list[str] = field(default_factory=list)
    notes: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "size_base": self.size_base,
            "stop_price": self.stop_price,
            "take_profit": self.take_profit,
            "blocks": list(self.blocks),
            "notes": self.notes,
        }


class RiskEngine:
    """Stateless-feeling but consults the DB for cooldowns/daily PnL/etc."""

    def __init__(self) -> None:
        self.cfg = get_config().risk
        self.settings = get_settings()

    def _log_block(self, rule: str, detail: str, strategy: str, payload: dict | None = None) -> None:
        with session_scope() as s:
            s.add(RiskBlock(
                ts=int(time.time()),
                rule=rule, detail=detail, strategy=strategy,
                payload=payload or {},
            ))

    def evaluate(
        self,
        *,
        account: PaperAccount,
        df: pd.DataFrame,
        signal_side: str,           # long/short/flat
        confidence: float,
        stop_pct: float | None,
        tp_pct: float | None,
        open_positions: list[Position],
        data_quality_score: float,
        strategy: str = "",
    ) -> RiskCheck:
        blocks: list[str] = []
        notes: dict = {}

        # Hard kill switch
        if self.settings.kill_switch:
            blocks.append("KILL_SWITCH")
        # Account halt flag
        if account.halted:
            blocks.append(f"ACCOUNT_HALTED:{account.halt_reason}")
        if signal_side == "flat":
            blocks.append("NO_SIGNAL")

        # Confidence floor
        if confidence < self.cfg.min_confidence:
            blocks.append(f"LOW_CONFIDENCE:{confidence:.3f}<{self.cfg.min_confidence}")

        # Max open positions
        if len(open_positions) >= self.cfg.max_open_positions:
            blocks.append(f"MAX_OPEN_POSITIONS:{len(open_positions)}>={self.cfg.max_open_positions}")

        # Data quality
        if data_quality_score < self.cfg.min_data_quality:
            blocks.append(f"DATA_QUALITY:{data_quality_score:.3f}<{self.cfg.min_data_quality}")

        # Cooldown after consecutive losses
        if account.cooldown_remaining > 0:
            blocks.append(f"COOLDOWN:{account.cooldown_remaining}")

        # Daily loss limit
        if account.daily_anchor_equity > 0:
            day_change = (account.equity - account.daily_anchor_equity) / account.daily_anchor_equity
            notes["day_change_pct"] = round(day_change, 5)
            if day_change <= -self.cfg.max_daily_loss_pct:
                blocks.append(f"DAILY_LOSS:{day_change:.3%}")

        # Drawdown
        if account.peak_equity > 0:
            dd = (account.equity - account.peak_equity) / account.peak_equity
            notes["drawdown_pct"] = round(dd, 5)
            if dd <= -self.cfg.max_total_drawdown_pct:
                blocks.append(f"MAX_DRAWDOWN:{dd:.3%}")

        # Volatility shutdown
        atr_pct = 0.0
        if not df.empty:
            atr_v = atr(df, 14).iloc[-1]
            close = float(df["close"].iloc[-1])
            atr_pct = float(atr_v / close) if close > 0 else 0.0
            notes["atr_pct"] = round(atr_pct, 5)
            if atr_pct > self.cfg.volatility_shutdown_atr_pct:
                blocks.append(f"VOLATILITY:{atr_pct:.3%}>{self.cfg.volatility_shutdown_atr_pct:.3%}")

        # Sizing
        last_price = float(df["close"].iloc[-1]) if not df.empty else 0.0
        size_base = 0.0
        stop_price = None
        take_profit = None
        if last_price > 0 and signal_side in ("long", "short"):
            max_notional = account.equity * self.cfg.max_position_size_pct
            # Risk-aware sizing: if stop_pct given, size so a stop loss = max 1% of equity
            risk_per_trade = account.equity * 0.01
            use_stop = max(0.005, float(stop_pct) if stop_pct else 0.02)
            risk_notional = risk_per_trade / use_stop
            notional = min(max_notional, risk_notional)
            size_base = round(notional / last_price, 8)
            if size_base * last_price < 10:  # absolute minimum notional
                blocks.append("MIN_NOTIONAL")
                size_base = 0.0
            if signal_side == "long":
                stop_price = last_price * (1 - use_stop)
                take_profit = last_price * (1 + (tp_pct or use_stop * 1.8))
            else:
                stop_price = last_price * (1 + use_stop)
                take_profit = last_price * (1 - (tp_pct or use_stop * 1.8))

        allowed = len(blocks) == 0 and size_base > 0
        if not allowed and signal_side in ("long", "short"):
            self._log_block(
                rule=blocks[0] if blocks else "ZERO_SIZE",
                detail=",".join(blocks) or "size 0",
                strategy=strategy,
                payload={"confidence": confidence, **notes},
            )
        return RiskCheck(
            allowed=allowed, size_base=size_base, stop_price=stop_price,
            take_profit=take_profit, blocks=blocks, notes=notes,
        )
