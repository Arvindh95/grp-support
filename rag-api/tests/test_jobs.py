"""GET /jobs/{id}, cancel."""
from __future__ import annotations


def test_get_unknown_job_404(client, good_headers):
    r = client.get("/jobs/00000000-0000-0000-0000-000000000000",
                   headers=good_headers)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_get_returns_job_state(client, good_headers, sample_rfs):
    job_id = client.post("/rfs/analyze", json=sample_rfs,
                         headers=good_headers).json()["job_id"]
    r = client.get(f"/jobs/{job_id}", headers=good_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["job_id"] == job_id
    assert body["status"] == "queued"
    assert body["rfs_lodge_id"] == "LDG-TEST-1"


def test_user_cannot_cancel(client, good_headers, sample_rfs):
    job_id = client.post("/rfs/analyze", json=sample_rfs,
                         headers=good_headers).json()["job_id"]
    r = client.post(f"/jobs/{job_id}/cancel", headers=good_headers)
    assert r.status_code == 403


def test_admin_can_cancel_queued(admin_client, good_headers, sample_rfs):
    job_id = admin_client.post("/rfs/analyze", json=sample_rfs,
                               headers=good_headers).json()["job_id"]
    r = admin_client.post(f"/jobs/{job_id}/cancel", headers=good_headers)
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"


def test_cancel_terminal_job_409(admin_client, good_headers, sample_rfs):
    job_id = admin_client.post("/rfs/analyze", json=sample_rfs,
                               headers=good_headers).json()["job_id"]
    admin_client.post(f"/jobs/{job_id}/cancel", headers=good_headers)
    r = admin_client.post(f"/jobs/{job_id}/cancel", headers=good_headers)
    assert r.status_code == 409


# ── job ownership isolation ───────────────────────────────────────────────────

def test_other_key_cannot_read_job(client, good_headers, sample_rfs, monkeypatch):
    """A job created by one API key is invisible (404) to a different
    non-admin key, even with the exact job UUID."""
    from app import auth
    job_id = client.post("/rfs/analyze", json=sample_rfs,
                         headers=good_headers).json()["job_id"]
    other = {"email": "other@x.com", "role": "user",
             "key_id": "k-other", "key_name": "other-key"}
    monkeypatch.setattr(auth, "_principal_for_key", lambda *a, **k: other)
    r = client.get(f"/jobs/{job_id}", headers=good_headers)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_admin_key_can_read_any_job(client, good_headers, sample_rfs, monkeypatch):
    """An admin key may read a job owned by a different key."""
    from app import auth
    job_id = client.post("/rfs/analyze", json=sample_rfs,
                         headers=good_headers).json()["job_id"]
    admin = {"email": "root@x.com", "role": "admin",
             "key_id": "k-admin-2", "key_name": "admin-key"}
    monkeypatch.setattr(auth, "_principal_for_key", lambda *a, **k: admin)
    r = client.get(f"/jobs/{job_id}", headers=good_headers)
    assert r.status_code == 200
    assert r.json()["job_id"] == job_id
