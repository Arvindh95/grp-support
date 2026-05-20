"""API-key auth — shares the `grp-api-keys` ES index minted by grp-api admin.

We never accept JWTs or cookies here. Service-to-service only.
"""
from __future__ import annotations

import hashlib
import threading
import time
from typing import TYPE_CHECKING

import requests
from fastapi import HTTPException, Request, status

from .deps import get_config

if TYPE_CHECKING:
    from .config import Config


API_KEYS_INDEX = "grp-api-keys"
USERS_INDEX = "grp-users"
_CACHE_TTL_SECONDS = 30


# (hash) -> (expires_at_monotonic, principal dict | None)
_cache: dict[str, tuple[float, dict | None]] = {}
_cache_lock = threading.Lock()


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _es_search(cfg: "Config", index: str, query: dict) -> list | None:
    """Return ES hits, or None on any transport/HTTP error (fail-closed)."""
    try:
        r = requests.post(
            f"{cfg.es_url}/{index}/_search",
            auth=(cfg.es_user, cfg.es_password),
            verify=cfg.es_verify_tls,
            json={"query": query, "size": 1},
            timeout=5,
        )
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    return r.json().get("hits", {}).get("hits", [])


def _resolve_principal(cfg: "Config", key_hash: str) -> dict | None:
    """Validate the API key AND re-check its owner.

    A key is only valid while its owner still exists as a user in grp-users.
    The owner's role is read LIVE from grp-users — never the (possibly stale)
    role snapshot stored on the key document — so a deleted or demoted user
    cannot keep an elevated key.
    """
    key_hits = _es_search(cfg, API_KEYS_INDEX, {
        "bool": {"filter": [
            {"term": {"key_hash": key_hash}},
            {"term": {"revoked": False}},
        ]}
    })
    if not key_hits:
        return None
    key_src = key_hits[0]["_source"]
    owner_email = key_src.get("owner", "")
    if not owner_email:
        return None

    user_hits = _es_search(cfg, USERS_INDEX, {"term": {"email": owner_email}})
    if not user_hits:
        # Owner deleted (or ES unreachable) — the key is dead.
        return None
    user_src = user_hits[0]["_source"]

    return {
        "email": owner_email,
        "role": user_src.get("role", "user"),   # live role, not the key snapshot
        "key_id": key_hits[0]["_id"],
        "key_name": key_src.get("name", ""),
    }


def _principal_for_key(cfg: "Config", raw_key: str) -> dict | None:
    h = _sha256_hex(raw_key)
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(h)
        if cached and cached[0] > now:
            return cached[1]

    principal = _resolve_principal(cfg, h)
    with _cache_lock:
        _cache[h] = (now + _CACHE_TTL_SECONDS, principal)
    return principal


def require_api_key(request: Request) -> dict:
    """FastAPI dependency. Returns principal dict or raises 401."""
    auth_hdr = request.headers.get("authorization", "")
    if not auth_hdr.lower().startswith("apikey "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            "Missing ApiKey header")
    raw = auth_hdr.split(" ", 1)[1].strip()
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Empty API key")

    principal = _principal_for_key(get_config(), raw)
    if not principal:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid API key")

    # Attach to request state so downstream (rate limit, logging) can read it.
    request.state.principal = principal
    return principal


def invalidate_cache() -> None:
    """Test/admin hook: drop the entire cache."""
    with _cache_lock:
        _cache.clear()
