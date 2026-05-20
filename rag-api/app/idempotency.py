"""Idempotency-Key handling — atomic.

Caller sends `Idempotency-Key: <token>`. We hash (key, body) and store
`{job_id, body_hash}` for `idempotency_ttl_seconds`.

- Same key, same body → return the original job_id (replay).
- Same key, different body → 409 idempotency_conflict.
- No key → no protection; the caller owns retry-safety.

The claim is made with an atomic Redis `SET NX`, carrying a job_id that the
caller pre-generated. Two concurrent requests with the same key therefore
cannot both win the claim — exactly one creates a job, the other replays it.
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
class Decision:
    """Outcome of an idempotency claim.

    action == "proceed"  — claim won; caller must create the job with `new_job_id`.
    action == "replay"   — same key + body already used; return `job_id`.
    action == "conflict" — same key, different body; return 409 (`job_id` is the
                           original job, for the error payload).
    """
    action: str
    job_id: str | None = None


def begin(token: str, body: dict, new_job_id: str) -> Decision:
    """Atomically claim `token`.

    On `proceed` the record already holds `new_job_id`; the caller must then
    create the job with that id, and call `release()` if creation fails.
    """
    h = _body_hash(body)
    key = _idem_key(token)
    r = get_redis()
    ttl = get_config().idempotency_ttl_seconds
    record = json.dumps({"body_hash": h, "job_id": new_job_id})

    # Atomic: only one concurrent caller can create the key.
    if r.set(key, record, nx=True, ex=ttl):
        return Decision("proceed")

    raw = r.get(key)
    if raw is None:
        # The holder's record expired in the race window — try once more.
        if r.set(key, record, nx=True, ex=ttl):
            return Decision("proceed")
        raw = r.get(key)
        if raw is None:
            return Decision("proceed")

    rec = json.loads(raw)
    if rec.get("body_hash") != h:
        return Decision("conflict", rec.get("job_id"))
    return Decision("replay", rec.get("job_id"))


def release(token: str) -> None:
    """Drop a claim. Call only when job creation failed after begin()=proceed,
    so a subsequent retry can claim the key cleanly."""
    get_redis().delete(_idem_key(token))
