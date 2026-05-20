"""Admin-set Claude API key (/settings/anthropic-key)."""


def test_anthropic_key_status_unset(client, admin_token):
    r = client.get("/settings/anthropic-key",
                    headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is False
    assert body["source"] == "environment"
    assert body["hint"] is None


def test_anthropic_key_status_requires_admin(client, user_token):
    r = client.get("/settings/anthropic-key",
                   headers={"Authorization": f"Bearer {user_token}"})
    assert r.status_code == 403


def test_anthropic_key_set_requires_admin(client, user_token):
    r = client.put("/settings/anthropic-key",
                   headers={"Authorization": f"Bearer {user_token}"},
                   json={"key": "sk-ant-whatever"})
    assert r.status_code == 403


def test_anthropic_key_set_rejects_bad_prefix(client, admin_token):
    r = client.put("/settings/anthropic-key",
                   headers={"Authorization": f"Bearer {admin_token}"},
                   json={"key": "definitely-not-a-claude-key"})
    assert r.status_code == 400


def test_anthropic_key_set_then_status(client, admin_token):
    """A valid-shaped key (validation is stubbed by the fake Anthropic client)
    is stored, and the status flips to configured / source=ui."""
    r = client.put("/settings/anthropic-key",
                   headers={"Authorization": f"Bearer {admin_token}"},
                   json={"key": "sk-ant-test-abcdefghijklmnopqrstuvwxyz"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    s = client.get("/settings/anthropic-key",
                   headers={"Authorization": f"Bearer {admin_token}"}).json()
    assert s["configured"] is True
    assert s["source"] == "ui"
    assert s["hint"]                         # masked, non-empty
    assert "abcdefghij" not in (s["hint"] or "")   # never the full key
