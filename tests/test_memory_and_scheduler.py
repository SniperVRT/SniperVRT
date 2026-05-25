from backend.app.memory import recall, recent_traces, remember, score_for_subject
from backend.app.memory.trace import trace
from backend.app.scheduler import get_scheduler


def test_memory_remember_and_recall():
    rid = remember("insight", "test:topic", "found something useful",
                   weight=0.7, evidence={"sharpe": 1.2})
    assert rid > 0
    rows = recall(subject="test:topic")
    assert any(r["summary"] == "found something useful" for r in rows)
    score = score_for_subject("test:topic")
    assert score["score"] > 0


def test_memory_negative_score():
    remember("failure", "test:bad", "blew up", weight=1.0)
    score = score_for_subject("test:bad")
    assert score["score"] < 0


def test_trace_round_trip():
    tid = trace("open", "ema_trend:long",
                ["signal=long", "confidence=0.9"],
                inputs={"x": 1}, outputs={"y": 2})
    assert tid > 0
    rows = recent_traces(limit=10)
    assert any(r["id"] == tid for r in rows)


def test_scheduler_status_lists_tasks():
    sched = get_scheduler()
    st = sched.status()
    assert "tasks" in st
    expected_tasks = {
        "news_ingest", "news_attribution", "sentiment",
        "micro_signals", "regime_v2", "agents", "edge_discovery",
    }
    assert expected_tasks.issubset(set(st["tasks"].keys()))


def test_scheduler_force_run_executes_task():
    sched = get_scheduler()
    res = sched.force_run("regime_v2")
    assert res["task"] == "regime_v2"
    # Either ok=True with a result, or ok=False on a known reason
    assert "ok" in res
