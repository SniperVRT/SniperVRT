from fastapi.testclient import TestClient

from backend.app.main import app


client = TestClient(app)


def test_intel_routes_listed():
    r = client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json()["paths"]
    expected = [
        "/api/intel/news/ingest", "/api/intel/news",
        "/api/intel/sentiment/snapshot", "/api/intel/sentiment",
        "/api/intel/micro/collect", "/api/intel/micro",
        "/api/intel/regime/snapshot", "/api/intel/regime/history",
        "/api/intel/edges/discover", "/api/intel/edges",
        "/api/intel/walk-forward/run", "/api/intel/walk-forward",
        "/api/intel/monte-carlo/run", "/api/intel/monte-carlo",
        "/api/intel/agents", "/api/intel/agents/run-all",
        "/api/intel/findings", "/api/intel/memory", "/api/intel/traces",
        "/api/intel/scheduler/status", "/api/intel/scheduler/start",
        "/api/intel/scheduler/stop",
    ]
    for p in expected:
        assert p in paths, f"missing {p}"


def test_intel_news_ingest_returns_counts():
    r = client.post("/api/intel/news/ingest", params={"force_synthetic": True})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"]
    assert "fetched" in body and "inserted" in body


def test_intel_agents_run_all():
    client.post("/api/data/refresh")
    r = client.post("/api/intel/agents/run-all")
    assert r.status_code == 200
    assert r.json()["ok"]


def test_intel_scheduler_status():
    r = client.get("/api/intel/scheduler/status")
    assert r.status_code == 200
    assert "tasks" in r.json()


def test_intel_edges_discover():
    client.post("/api/data/refresh")
    client.post("/api/intel/news/ingest", params={"force_synthetic": True})
    r = client.post("/api/intel/edges/discover")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"]
    assert body["tested"] > 0
