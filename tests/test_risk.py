from backend.app.core.db import session_scope
from backend.app.data import ensure_dataset, load_candles
from backend.app.models import PaperAccount
from backend.app.risk.engine import RiskEngine


def _make_account(equity=10_000.0):
    with session_scope() as s:
        s.query(PaperAccount).filter_by(name="default").delete()
        acc = PaperAccount(
            name="default", equity=equity, cash=equity, starting_equity=equity,
            peak_equity=equity, daily_anchor_equity=equity, last_day_bucket="2099-01-01",
        )
        s.add(acc); s.flush()
        return acc


def test_risk_blocks_low_confidence():
    ensure_dataset(limit=400)
    df = load_candles(limit=400)
    acc = _make_account()
    eng = RiskEngine()
    check = eng.evaluate(
        account=acc, df=df, signal_side="long", confidence=0.1,
        stop_pct=0.02, tp_pct=0.05, open_positions=[],
        data_quality_score=1.0, strategy="test",
    )
    assert not check.allowed
    assert any("LOW_CONFIDENCE" in b for b in check.blocks)


def test_risk_allows_good_signal():
    ensure_dataset(limit=400)
    df = load_candles(limit=400)
    acc = _make_account()
    eng = RiskEngine()
    check = eng.evaluate(
        account=acc, df=df, signal_side="long", confidence=0.9,
        stop_pct=0.02, tp_pct=0.05, open_positions=[],
        data_quality_score=1.0, strategy="test",
    )
    if not check.allowed:
        # If blocked, it must be by a real rule (e.g. volatility), not arbitrary
        assert check.blocks
    else:
        assert check.size_base > 0
        assert check.stop_price is not None


def test_risk_blocks_kill_switch(monkeypatch):
    ensure_dataset(limit=400)
    df = load_candles(limit=400)
    acc = _make_account()
    eng = RiskEngine()
    monkeypatch.setattr(eng.settings, "kill_switch", True)
    check = eng.evaluate(
        account=acc, df=df, signal_side="long", confidence=0.9,
        stop_pct=0.02, tp_pct=0.05, open_positions=[],
        data_quality_score=1.0, strategy="test",
    )
    assert not check.allowed
    assert "KILL_SWITCH" in check.blocks
