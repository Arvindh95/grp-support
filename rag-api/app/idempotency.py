"""Idempotency-Key handling.

Caller sends `Idempotency-Key: <uuid>`. We hash (key, body) and store
{job_id, body_hash} for `idempotency_ttl_seconds`.

- Same key, same body → return original job_id (replay).
- Same key, different body → 409 idempotency_conflict.
- No key → no protection; the caller owns retry-safety.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .deps import get_config, get_redis


def _idem_key(token: str) -> str:
    return f"rag:idem:{hashlib.sha256(token.encode()).hexdigest()}"


def _body_hash(body: dict) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass
class IdempotencyHit:
    job_id: str
    body_hash: str


def check(token: str, body: dict) -> tuple[IdempotencyHit | None, bool]:
    """Look up token. Returns (existing_record_or_None, conflict_bool).

    - (None, False)        — first sight, caller should proceed.
    - (record, False)      — replay; caller should return the original job.
    - (record, True)       — token reuse with different body; caller should 409.
    """
    raw = get_redis().get(_idem_key(token))
    if raw is None:
        return None, False
    rec = json.loads(raw)
    hit = IdempotencyHit(job_id=rec["job_id"], body_hash=rec["body_hash"])
    if hit.body_hash != _body_hash(body):
        return hit, True
    return hit, False


def remember(token: str, body: dict, job_id: str) -> None:
    cfg = get_config()
    payload = json.dumps({"job_id": job_id, "body_hash": _body_hash(body)})
    get_redis().set(_idem_key(token), payload, ex=cfg.idempotency_ttl_seconds)
