"""Test scaffolding.

- Replaces the redis client with fakeredis so tests run without a real Redis.
- Stubs the API-key lookup so tests don't need a live grp-api-keys ES index.
- Disables the in-process worker pool (tests submit jobs and inspect queue
  state directly; running the pipeline stub mid-test makes assertions racy).
"""
from __future__ import annotations

import os

os.environ.setdefault("ES_PASSWORD", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test")
os.environ.setdefault("RAG_RUN_WORKER", "0")

import fakeredis
import pytest
from fastapi.testclient import TestClient

from app import auth, deps
from app.config import load_config


@pytest.fixture(autouse=True)
def _reset_singletons():
    deps.reset_all()
    deps.set_config(load_config())
    deps.set_redis(fakeredis.FakeRedis(decode_responses=True))
    auth.invalidate_cache()
    yield
    deps.reset_all()


@pytest.fixture
def admin_principal(monkeypatch):
    principal = {"email": "admin@x.com", "role": "admin",
                 "key_id": "k-admin", "key_name": "admin-test-key"}
    monkeypatch.setattr(auth, "_principal_for_key", lambda *_a, **_k: principal)
    return principal


@pytest.fixture
def user_principal(monkeypatch):
    principal = {"email": "user@x.com", "role": "user",
                 "key_id": "k-user", "key_name": "user-test-key"}
    monkeypatch.setattr(auth, "_principal_for_key", lambda *_a, **_k: principal)
    return principal


@pytest.fixture
def client(user_principal):
    from app.main import app
    return TestClient(app)


@pytest.fixture
def admin_client(admin_principal):
    from app.main import app
    return TestClient(app)


@pytest.fixture
def good_headers():
    return {"Authorization": "ApiKey grp_testkey"}


@pytest.fixture
def sample_rfs():
    return {
        "rfs": {
            "lodge_id": "LDG-TEST-1",
            "notes": "User cannot log in after license renewal. "
                     "Browser shows 'license expired' even though payment "
                     "was completed on the new portal yesterday.",
        },
    }
