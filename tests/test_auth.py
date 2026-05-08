def test_health_no_auth(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_protected_route_requires_token(client):
    r = client.get("/auth/me")
    assert r.status_code == 401


def test_login_success(client, admin_token):
    r = client.get("/auth/me", headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "admin@test"
    assert body["role"] == "admin"


def test_login_wrong_password(client, admin_token):
    r = client.post("/auth/login", json={"email": "admin@test", "password": "wrong"})
    assert r.status_code == 401


def test_register_requires_admin(client, user_token):
    r = client.post(
        "/auth/register",
        headers={"Authorization": f"Bearer {user_token}"},
        json={"email": "x@y", "password": "abcdefg1", "role": "user"},
    )
    assert r.status_code == 403


def test_admin_can_list_users(client, admin_token, user_token):
    r = client.get("/auth/users", headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200
    emails = {u["email"] for u in r.json()}
    assert {"admin@test", "joe@test"} <= emails


def test_user_cannot_list_users(client, user_token):
    r = client.get("/auth/users", headers={"Authorization": f"Bearer {user_token}"})
    assert r.status_code == 403


def test_admin_cannot_delete_self(client, admin_token):
    r = client.delete(
        "/auth/users/admin@test",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 400


def test_change_password_requires_old(client, user_token):
    r = client.post(
        "/auth/change-password",
        headers={"Authorization": f"Bearer {user_token}"},
        json={"old_password": "wrong", "new_password": "newpass11"},
    )
    assert r.status_code == 401


def test_change_password_min_length(client, user_token):
    r = client.post(
        "/auth/change-password",
        headers={"Authorization": f"Bearer {user_token}"},
        json={"old_password": "joepass1", "new_password": "short"},
    )
    assert r.status_code == 400
