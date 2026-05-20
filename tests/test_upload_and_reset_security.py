"""Security fixes: chat-upload type allowlist (F6) + reset-request throttle (F7)."""


# ── F6: chat attachments must be text-extractable ─────────────────────────────

def test_chat_upload_rejects_pdf(client, user_token):
    r = client.post(
        "/upload-chat-file",
        headers={"Authorization": f"Bearer {user_token}"},
        files={"file": ("evil.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert r.status_code == 400
    assert "pdf" in r.json()["detail"].lower()


def test_chat_upload_rejects_image(client, user_token):
    r = client.post(
        "/upload-chat-file",
        headers={"Authorization": f"Bearer {user_token}"},
        files={"file": ("screenshot.png", b"\x89PNG fake", "image/png")},
    )
    assert r.status_code == 400


# ── F7: password-reset throttle ───────────────────────────────────────────────

def _seed_user(A, email):
    A.requests.post(
        f"{A.ES_URL}/{A.USERS_INDEX}/_doc",
        json={"email": email, "password_hash": A.hash_password("Passw0rd!xyz1"),
              "role": "user", "created_at": 1},
    )


def test_reset_request_throttled_mints_no_token(client, es, monkeypatch):
    """When throttled the endpoint still returns a uniform 200 (no
    enumeration) but mints no token and sends no email."""
    import api_server as A
    _seed_user(A, "victim@test")
    monkeypatch.setattr(A, "_reset_request_throttled", lambda *a, **k: True)

    r = client.post("/auth/reset-request", json={"email": "victim@test"})
    assert r.status_code == 200
    assert not any(idx == A.RESET_TOKENS_INDEX for idx, _ in es.docs)


def test_reset_request_unthrottled_mints_token(client, es, monkeypatch):
    """The normal path still issues a reset token for a real user."""
    import api_server as A
    _seed_user(A, "okuser@test")
    monkeypatch.setattr(A, "_reset_request_throttled", lambda *a, **k: False)
    monkeypatch.setattr(A, "send_email", lambda *a, **k: True)

    r = client.post("/auth/reset-request", json={"email": "okuser@test"})
    assert r.status_code == 200
    assert any(idx == A.RESET_TOKENS_INDEX for idx, _ in es.docs)
