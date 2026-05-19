"""Idempotency-Key handling."""
from __future__ import annotations


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
