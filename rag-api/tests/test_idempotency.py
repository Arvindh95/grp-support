"""Idempotency-Key handling."""
from __future__ import annotations

from app import idempotency


# ── atomic claim unit tests ───────────────────────────────────────────────────

def test_begin_first_caller_proceeds():
    d = idempotency.begin("tok-A", {"x": 1}, "job-A1")
    assert d.action == "proceed"


def test_begin_same_body_replays_original_job():
    idempotency.begin("tok-B", {"x": 1}, "job-B1")
    d = idempotency.begin("tok-B", {"x": 1}, "job-B2")
    assert d.action == "replay"
    assert d.job_id == "job-B1"          # the first job, never the racer's id


def test_begin_different_body_conflicts():
    idempotency.begin("tok-C", {"x": 1}, "job-C1")
    d = idempotency.begin("tok-C", {"x": 2}, "job-C2")
    assert d.action == "conflict"
    assert d.job_id == "job-C1"


def test_release_frees_the_claim():
    idempotency.begin("tok-D", {"x": 1}, "job-D1")
    idempotency.release("tok-D")
    d = idempotency.begin("tok-D", {"x": 1}, "job-D2")
    assert d.action == "proceed"         # key is claimable again


# ── route-level behaviour ─────────────────────────────────────────────────────

def test_same_key_same_body_replays(client, good_headers, sample_rfs):
    headers = {**good_headers, "Idempotency-Key": "11111111-1111-1111-1111-111111111111"}
    r1 = client.post("/rfs/analyze", json=sample_rfs, headers=headers)
    r2 = client.post("/rfs/analyze", json=sample_rfs, headers=headers)
    assert r1.status_code == 202
    assert r2.status_code == 202
    assert r1.json()["job_id"] == r2.json()["job_id"]


def test_same_key_diff_body_conflicts(client, good_headers, sample_rfs):
    headers = {**good_headers, "Idempotency-Key": "22222222-2222-2222-2222-222222222222"}
    r1 = client.post("/rfs/analyze", json=sample_rfs, headers=headers)
    sample_rfs["rfs"]["notes"] = "totally different problem"
    r2 = client.post("/rfs/analyze", json=sample_rfs, headers=headers)
    assert r1.status_code == 202
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "idempotency_conflict"
