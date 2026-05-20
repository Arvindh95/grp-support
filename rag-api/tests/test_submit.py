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


# ── multipart/form-data intake ────────────────────────────────────────────────

def test_submit_multipart_file_becomes_attachment(client, good_headers):
    """A multipart upload: `payload` JSON field + native file part. The file
    is folded into rfs.attachments with base64 content."""
    import base64
    import json as _json

    payload = _json.dumps({"rfs": {"lodge_id": "LDG-MP",
                                   "notes": "See the attached note."}})
    r = client.post(
        "/rfs/analyze",
        data={"payload": payload},
        files={"files": ("note.txt", b"hello from the file", "text/plain")},
        headers=good_headers,
    )
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]

    from app import _submit_meta
    atts = _submit_meta.load_submit_meta(job_id)["rfs"]["attachments"]
    assert len(atts) == 1
    assert atts[0]["filename"] == "note.txt"
    assert atts[0]["content_type"] == "text/plain"
    assert base64.b64decode(atts[0]["content_b64"]) == b"hello from the file"


def test_submit_multipart_requires_payload_field(client, good_headers):
    r = client.post(
        "/rfs/analyze",
        files={"files": ("a.txt", b"x", "text/plain")},
        headers=good_headers,
    )
    assert r.status_code == 400
    assert "payload" in r.json()["error"]["message"]


def test_submit_multipart_rejects_word_file(client, good_headers):
    """A .docx uploaded via multipart is rejected with the convert hint."""
    import json as _json
    payload = _json.dumps({"rfs": {"lodge_id": "LDG-MP-DOCX",
                                   "notes": "see attached"}})
    docx_ct = ("application/vnd.openxmlformats-officedocument."
               "wordprocessingml.document")
    r = client.post(
        "/rfs/analyze",
        data={"payload": payload},
        files={"files": ("spec.docx", b"PK fake", docx_ct)},
        headers=good_headers,
    )
    assert r.status_code == 400
    assert "convert it to PDF" in r.json()["error"]["message"]
