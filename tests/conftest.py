"""Test fixtures: stub external dependencies (ES, Ollama, Anthropic) so the
suite runs offline. Tests focus on auth, signing, budget, retention, and
response shaping — they do not exercise the real Claude/ES path."""

import os
import sys
import types
from pathlib import Path

# Set required env BEFORE api_server imports.
os.environ.setdefault("ES_PASSWORD", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("JWT_SECRET", "test-secret-do-not-use-in-prod-test-secret-do-not-use")
os.environ.setdefault("ES_URL", "http://es-stub")
os.environ.setdefault("OLLAMA_URL", "http://ollama-stub")
os.environ.setdefault("IMG_DIR", str(Path(__file__).parent / "_imgs"))
os.environ.setdefault("IMG_BASE", "http://img-stub:8080")
os.environ.setdefault("IMG_PUBLIC_BASE", "http://img-stub:8080")

import pytest
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class _ESStore:
    """Tiny in-memory ES stand-in with the operations the API actually uses."""
    def __init__(self):
        self.docs: dict[tuple[str, str], dict] = {}  # (index, id) -> source
        self._next_id = 0

    def reset(self):
        self.docs.clear()
        self._next_id = 0

    def gen_id(self) -> str:
        self._next_id += 1
        return f"id_{self._next_id}"


@pytest.fixture()
def es():
    return _ESStore()


@pytest.fixture()
def client(monkeypatch, es):
    """FastAPI TestClient with all external HTTP calls stubbed."""
    import requests as _real_requests

    # Build a fake `requests` API — only the calls api_server.py makes.
    def _fake_post(url, **kw):
        body = kw.get("json") or {}
        resp = MagicMock()
        resp.status_code = 200

        # Embedding (Ollama)
        if "/api/embeddings" in url:
            resp.json.return_value = {"embedding": [0.1] * 1024}
            return resp

        # ES create-index PUT handled by _fake_put

        # ES _search
        if "/_search" in url:
            idx = url.split("/")[-2]
            hits = []
            q = body.get("query", {})
            for (i, _id), src in es.docs.items():
                if i != idx:
                    continue
                if _matches(q, src):
                    hits.append({"_id": _id, "_source": src})
            # Aggregations stub: sum + value_count + terms (very loose)
            aggs = {}
            for name, agg in (body.get("aggs", {}) or {}).items():
                if "sum" in agg:
                    field = agg["sum"]["field"]
                    aggs[name] = {"value": sum(int(s.get(field) or 0) for _, s in [(("_",), h["_source"]) for h in hits])}
                elif "value_count" in agg:
                    aggs[name] = {"value": len(hits)}
                elif "terms" in agg:
                    field = agg["terms"]["field"].replace(".keyword", "")
                    buckets = {}
                    for h in hits:
                        v = h["_source"].get(field)
                        if v is None:
                            continue
                        b = buckets.setdefault(v, {"key": v, "doc_count": 0})
                        b["doc_count"] += 1
                    aggs[name] = {"buckets": list(buckets.values())}
            resp.json.return_value = {
                "hits": {"hits": hits[: body.get("size", 10) or 10000]},
                "aggregations": aggs,
            }
            return resp

        # ES _doc (index, no id) → auto id
        if url.endswith("/_doc") or "/_doc?" in url:
            idx = url.split("/_doc")[0].rsplit("/", 1)[-1]
            new_id = es.gen_id()
            es.docs[(idx, new_id)] = body
            resp.json.return_value = {"_id": new_id, "result": "created"}
            resp.status_code = 201
            return resp

        # ES _doc/<id>
        if "/_doc/" in url:
            idx_part, id_part = url.split("/_doc/")
            idx = idx_part.rsplit("/", 1)[-1]
            _id = id_part.split("?")[0]
            es.docs[(idx, _id)] = body
            resp.json.return_value = {"_id": _id, "result": "created"}
            resp.status_code = 201
            return resp

        # ES _update_by_query
        if "/_update_by_query" in url:
            idx = url.split("/_update_by_query")[0].rsplit("/", 1)[-1]
            updated = 0
            for (i, _id), src in list(es.docs.items()):
                if i != idx:
                    continue
                if _matches(body.get("query", {}), src):
                    src.update(_apply_script(body.get("script", {}), src))
                    updated += 1
            resp.json.return_value = {"updated": updated}
            return resp

        # ES _delete_by_query
        if "/_delete_by_query" in url:
            idx = url.split("/_delete_by_query")[0].rsplit("/", 1)[-1]
            removed = 0
            for (i, _id), src in list(es.docs.items()):
                if i != idx:
                    continue
                if _matches(body.get("query", {}), src):
                    es.docs.pop((i, _id))
                    removed += 1
            resp.json.return_value = {"deleted": removed}
            return resp

        # ES _update/<id>
        if "/_update/" in url:
            idx_part, rest = url.split("/_update/")
            idx = idx_part.rsplit("/", 1)[-1]
            _id = rest.split("?")[0]
            doc = es.docs.get((idx, _id))
            if doc is None:
                resp.status_code = 404
                resp.json.return_value = {"result": "not_found"}
                return resp
            doc.update(body.get("doc", {}))
            resp.status_code = 200
            return resp

        resp.status_code = 200
        resp.json.return_value = {}
        return resp

    def _fake_get(url, **kw):
        resp = MagicMock()
        resp.status_code = 200
        if "/_doc/" in url:
            idx_part, id_part = url.split("/_doc/")
            idx = idx_part.rsplit("/", 1)[-1]
            _id = id_part.split("?")[0]
            doc = es.docs.get((idx, _id))
            if doc is None:
                resp.status_code = 404
                resp.json.return_value = {"found": False}
            else:
                resp.json.return_value = {"_source": doc}
            return resp
        if "/_count" in url:
            idx = url.split("/_count")[0].rsplit("/", 1)[-1]
            resp.json.return_value = {"count": sum(1 for k in es.docs if k[0] == idx)}
            return resp
        # generic GET on index returns 200 (assume exists)
        resp.json.return_value = {}
        return resp

    def _fake_put(url, **kw):
        resp = MagicMock()
        resp.status_code = 200
        # ES create index: just succeed
        if "/_doc/" in url:
            idx_part, id_part = url.split("/_doc/")
            idx = idx_part.rsplit("/", 1)[-1]
            _id = id_part.split("?")[0]
            es.docs[(idx, _id)] = kw.get("json") or {}
            resp.status_code = 201
            return resp
        return resp

    def _fake_delete(url, **kw):
        resp = MagicMock()
        resp.status_code = 200
        if "/_doc/" in url:
            idx_part, id_part = url.split("/_doc/")
            idx = idx_part.rsplit("/", 1)[-1]
            _id = id_part.split("?")[0]
            if (idx, _id) in es.docs:
                del es.docs[(idx, _id)]
                resp.status_code = 200
            else:
                resp.status_code = 404
            return resp
        return resp

    monkeypatch.setattr("requests.post", _fake_post)
    monkeypatch.setattr("requests.get", _fake_get)
    monkeypatch.setattr("requests.put", _fake_put)
    monkeypatch.setattr("requests.delete", _fake_delete)

    # Stub anthropic before import
    fake_anthropic = types.ModuleType("anthropic")
    fake_anthropic.APIStatusError = type("APIStatusError", (Exception,), {})
    fake_anthropic.APITimeoutError = type("APITimeoutError", (Exception,), {})

    class _FakeAnthropicClient:
        def __init__(self, *a, **kw): pass
        class messages:
            @staticmethod
            def create(**kw):
                m = MagicMock()
                m.content = [MagicMock(type="text", text="ok\n```sources\n{\"manuals\":[],\"tickets\":[],\"scripts\":[]}\n```")]
                m.stop_reason = "end_turn"
                m.usage = MagicMock(input_tokens=10, output_tokens=5, cache_read_input_tokens=0)
                return m
    fake_anthropic.Anthropic = _FakeAnthropicClient
    monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)

    # Now import the app
    if "api_server" in sys.modules:
        del sys.modules["api_server"]
    import api_server  # noqa
    from fastapi.testclient import TestClient

    # Trigger startup hook to seed indices in our store (no-op, but exercises code path).
    with TestClient(api_server.app) as c:
        yield c


def _matches(query: dict, src: dict) -> bool:
    """Naive ES query matcher: handles term, range, bool/filter, match_all, multi_match."""
    if not query or "match_all" in query:
        return True
    if "term" in query:
        for k, v in query["term"].items():
            k = k.replace(".keyword", "")
            if isinstance(v, dict):
                v = v.get("value")
            if src.get(k) != v:
                return False
        return True
    if "range" in query:
        for k, cond in query["range"].items():
            val = src.get(k)
            if val is None:
                return False
            for op, ref in cond.items():
                # Allow either ms epoch (int) or ISO string compares
                try:
                    if op == "lt"  and not (val <  ref): return False
                    if op == "lte" and not (val <= ref): return False
                    if op == "gt"  and not (val >  ref): return False
                    if op == "gte" and not (val >= ref): return False
                except TypeError:
                    return False
        return True
    if "bool" in query:
        b = query["bool"]
        for sub in b.get("filter", []) + b.get("must", []):
            if not _matches(sub, src):
                return False
        for sub in b.get("must_not", []):
            if _matches(sub, src):
                return False
        return True
    if "multi_match" in query:
        q = query["multi_match"]["query"].lower()
        for f in query["multi_match"].get("fields", []):
            f = f.split("^")[0]
            v = src.get(f, "")
            if isinstance(v, str) and q in v.lower():
                return True
        return False
    return False


def _apply_script(script: dict, src: dict) -> dict:
    """Very narrow Painless interpreter — handles `ctx._source.<field> = params.<x>`."""
    out = {}
    source = (script or {}).get("source", "")
    params = (script or {}).get("params", {})
    import re as _re
    for m in _re.finditer(r"ctx\._source\.(\w+)\s*=\s*params\.(\w+)", source):
        out[m.group(1)] = params.get(m.group(2))
    return out


@pytest.fixture()
def admin_token(client):
    """Create an admin user and return a JWT bearer token."""
    import api_server as A
    # Direct seed bypasses the admin-only register endpoint.
    A.requests.post(
        f"{A.ES_URL}/{A.USERS_INDEX}/_doc",
        json={
            "email": "admin@test", "password_hash": A.hash_password("adminpw1"),
            "name": "Admin", "role": "admin",
            "created_at": int(__import__('time').time() * 1000),
        },
    )
    r = client.post("/auth/login", json={"email": "admin@test", "password": "adminpw1"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture()
def user_token(client, admin_token):
    """Create a normal user and return a JWT bearer token."""
    r = client.post(
        "/auth/register",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"email": "joe@test", "password": "joepass1", "name": "Joe", "role": "user"},
    )
    assert r.status_code == 200, r.text
    r = client.post("/auth/login", json={"email": "joe@test", "password": "joepass1"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]
