from fastapi.testclient import TestClient

from backend.app.main import app


client = TestClient(app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_system_status():
    r = client.get("/api/system/status")
    assert r.status_code == 200
    body = r.json()
    assert "live" in body
    assert body["live"]["locked_config"] is True


def test_strategies_listed():
    r = client.get("/api/strategies")
    assert r.status_code == 200
    names = {s["name"] for s in r.json()["strategies"]}
    assert {"ema_trend", "rsi_meanrev", "breakout", "vol_regime", "ensemble"}.issubset(names)


def test_backtest_route_runs():
    client.post("/api/data/refresh")  # synthetic fallback
    r = client.post("/api/backtest/run", json={
        "strategy_type": "ema_trend", "limit": 800,
        "starting_equity": 10000.0, "allow_short": True,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert "sharpe" in body and "equity_curve" in body
    assert isinstance(body["run_id"], int)


def test_live_unlock_requires_confirm_string():
    r = client.post("/api/live/unlock", json={"reason": "test", "confirm": "wrong"})
    assert r.status_code == 400


def test_paper_tick_via_api():
    r = client.post("/api/paper/tick")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_governance_validate():
    r = client.post("/api/governance/validate")
    assert r.status_code == 200
    body = r.json()
    assert "decision" in body
    assert body["live_locked"] is True
