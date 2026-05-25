from backend.app.data import ensure_dataset
from backend.app.paper.runtime import get_runtime


def test_paper_runtime_ticks_and_persists():
    ensure_dataset(limit=600)
    rt = get_runtime()
    rt.reset()
    # First tick: signal must be evaluated (open or block)
    out = rt.tick_once()
    assert out["ok"]
    # Tick a few more times; state persists
    for _ in range(3):
        rt.tick_once()
    status = rt.status()
    assert status["equity"] > 0
    assert isinstance(status["open_positions"], list)


def test_live_remains_locked():
    from backend.app.live import get_live_gate
    g = get_live_gate()
    st = g.status()
    assert st["locked_config"] is True
    assert st["ready_for_live"] is False
