def test_apikey_create_requires_admin(client, user_token):
    r = client.post(
        "/api-keys",
        headers={"Authorization": f"Bearer {user_token}"},
        json={"name": "x", "owner": "joe@test"},
    )
    assert r.status_code == 403


def test_apikey_lifecycle(client, admin_token, user_token):
    # Mint
    r = client.post(
        "/api-keys",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "slackbot", "owner": "joe@test"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    raw = body["key"]
    key_id = body["id"]
    assert raw.startswith("grp_")

    # Use the key on a protected route
    r = client.get("/auth/me", headers={"Authorization": f"ApiKey {raw}"})
    assert r.status_code == 200
    assert r.json()["email"] == "joe@test"

    # List
    r = client.get("/api-keys", headers={"Authorization": f"Bearer {admin_token}"})
    assert any(k["id"] == key_id for k in r.json())

    # Revoke
    r = client.delete(f"/api-keys/{key_id}", headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200

    # Revoked key no longer authenticates
    r = client.get("/auth/me", headers={"Authorization": f"ApiKey {raw}"})
    assert r.status_code == 401


def test_apikey_unknown_rejected(client):
    r = client.get("/auth/me", headers={"Authorization": "ApiKey grp_does_not_exist"})
    assert r.status_code == 401


def test_apikey_owner_must_exist(client, admin_token):
    r = client.post(
        "/api-keys",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"name": "x", "owner": "ghost@nowhere"},
    )
    assert r.status_code == 404
