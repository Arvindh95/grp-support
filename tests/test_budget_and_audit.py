def test_estimate_cost_usd():
    import api_server as A
    cost = A.estimate_cost_usd(1_000_000, 500_000, 100_000)
    # billed_input 900_000 * 3 + 500_000 * 15 + 100_000 * 0.30 / 1M
    assert 10.20 < cost < 10.40  # allow rounding wiggle


def test_audit_usage_admin_only(client, user_token):
    r = client.get("/audit/usage", headers={"Authorization": f"Bearer {user_token}"})
    assert r.status_code == 403


def test_audit_usage_returns_totals(client, admin_token):
    import api_server as A
    # Seed two audit docs in current month
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    A.requests.post(f"{A.ES_URL}/{A.AUDIT_INDEX}/_doc", json={
        "ts": now_iso, "user": "u1@x", "event": "query",
        "input_tokens": 100, "output_tokens": 50, "cached_tokens": 10,
    })
    A.requests.post(f"{A.ES_URL}/{A.AUDIT_INDEX}/_doc", json={
        "ts": now_iso, "user": "u2@x", "event": "query",
        "input_tokens": 200, "output_tokens": 80, "cached_tokens": 0,
    })
    r = client.get("/audit/usage", headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"]["input_tokens"] == 300
    assert body["total"]["output_tokens"] == 130
    assert body["total"]["cost_usd"] > 0


def test_token_budget_blocks_when_exceeded(client, monkeypatch, user_token):
    import api_server as A
    monkeypatch.setattr(A, "MONTHLY_TOKEN_BUDGET", 100)
    monkeypatch.setattr(A, "_audit_token_sum", lambda user=None: {"input": 80, "output": 30, "cached": 0, "calls": 1})

    r = client.post(
        "/query",
        headers={"Authorization": f"Bearer {user_token}"},
        json={"question": "anything"},
    )
    assert r.status_code == 429
    assert "budget" in r.json()["detail"].lower()


def test_token_budget_allows_when_under(client, monkeypatch, user_token):
    import api_server as A
    monkeypatch.setattr(A, "MONTHLY_TOKEN_BUDGET", 100_000_000)
    # Stub seed-context to avoid embedding path complexity; just ensure check_token_budget passes.
    A.check_token_budget()  # direct call — should not raise
