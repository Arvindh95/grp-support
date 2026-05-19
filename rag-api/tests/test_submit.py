"""POST /rfs/analyze contract tests."""
from __future__ import annotations


def test_submit_returns_202_with_job_id(client, good_headers, sample_rfs):
    r = client.post("/rfs/analyze", json=sample_rfs, headers=good_headers)
    assert r.status_code == 202, r.text
    body = r.json()
    assert "job_id" in body
    assert body["status"] == "queued"
    assert body["poll_url"].startswith("/jobs/")
    assert r.headers["Location"] == body["poll_url"]
    # Rate-limit headers visible
    assert "RateLimit-Limit" in r.headers
    assert "RateLimit-Remaining" in r.headers


def test_submit_requires_apikey(sample_rfs):
    from app.main import app
    from fastapi.testclient import TestClient

    c = TestClient(app)
    r = c.post("/rfs/analyze", json=sample_rfs)
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


def test_submit_rejects_missing_notes(client, good_headers):
    bad = {"rfs": {"lodge_id": "LDG-X"}}
    r = client.post("/rfs/analyze", json=bad, headers=good_headers)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_request"


def test_submit_rejects_extra_fields(client, good_headers, sample_rfs):
    sample_rfs["unknown_top_level"] = "boom"
    r = client.post("/rfs/analyze", json=sample_rfs, headers=good_headers)
    assert r.status_code == 400


def test_submit_persists_job(client, good_headers, sample_rfs):
    from app import queue
    r = client.post("/rfs/analyze", json=sample_rfs, headers=good_headers)
    job_id = r.json()["job_id"]
    job = queue.get_job(job_id)
    assert job is not None
    assert job.rfs_lodge_id == "LDG-TEST-1"
    assert job.status.value == "queued"
