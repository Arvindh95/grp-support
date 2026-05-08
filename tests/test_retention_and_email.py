def test_retention_admin_only(client, user_token):
    r = client.post("/admin/retention/run", headers={"Authorization": f"Bearer {user_token}"})
    assert r.status_code == 403


def test_retention_runs(client, admin_token):
    r = client.post(
        "/admin/retention/run?audit_days=30&chats_days=180",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "audit" in body and "chats" in body


def test_send_email_no_smtp_configured(monkeypatch):
    import api_server as A
    monkeypatch.setattr(A, "SMTP_HOST", "")
    assert A.send_email("a@b", "subj", "body") is False


def test_reset_request_returns_ok_for_unknown_email(client):
    # Anti-enumeration: should return 200 even for unknown emails
    r = client.post("/auth/reset-request", json={"email": "nobody@nowhere"})
    assert r.status_code == 200


def test_reset_confirm_rejects_bad_token(client):
    r = client.post("/auth/reset-confirm", json={"token": "garbage", "new_password": "abcdefgh"})
    assert r.status_code == 400


def test_slack_notify_no_webhook(monkeypatch):
    import api_server as A
    monkeypatch.setattr(A, "SLACK_WEBHOOK_URL", "")
    A.notify_slack("hello")  # should be a no-op, not raise
