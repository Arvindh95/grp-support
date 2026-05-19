"""/health and /ready."""
from __future__ import annotations


def test_health_no_auth_required():
    from fastapi.testclient import TestClient
    from app.main import app

    c = TestClient(app)
    r = c.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_ready_no_auth_required_returns_shape():
    from fastapi.testclient import TestClient
    from app.main import app

    c = TestClient(app)
    r = c.get("/ready")
    # Deps unreachable in tests → 503 with shape preserved
    assert r.status_code in (200, 503)
    body = r.json()
    assert "deps" in body
    for k in ("elasticsearch", "ollama", "anthropic", "redis"):
        assert k in body["deps"]
