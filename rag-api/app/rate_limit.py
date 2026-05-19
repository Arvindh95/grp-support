"""Per-API-key sliding-window rate limit, backed by Redis.

Implementation: fixed 60-second buckets. INCR + EXPIRE on the per-bucket key.
Cheap, monotonic, no clock skew issues. Caller sees standard RateLimit-* headers.
"""
from __future__ import annotations

import time

from fastapi import HTTPException, Request, Response, status

from .deps import get_config, get_redis


def _bucket_key(principal_id: str, bucket: int) -> str:
    return f"rag:rl:{principal_id}:{bucket}"


def enforce(request: Request, response: Response) -> None:
    """FastAPI dependency. Must be called AFTER `require_api_key` so principal is set.

    Raises 429 with Retry-After when over the budget.
    """
    principal = getattr(request.state, "principal", None)
    if not principal:
        return  # nothing to rate-limit against

    cfg = get_config()
    limit = cfg.rate_limit_per_minute
    if limit <= 0:
        return

    now = int(time.time())
    bucket = now // 60
    key_id = principal.get("key_id") or principal.get("email") or "anon"
    key = _bucket_key(key_id, bucket)

    r = get_redis()
    pipe = r.pipeline()
    pipe.incr(key)
    pipe.expire(key, 90)   # > 60s so the bucket survives the whole window
    count, _ = pipe.execute()

    remaining = max(0, limit - int(count))
    reset_in = 60 - (now % 60)

    response.headers["RateLimit-Limit"] = str(limit)
    response.headers["RateLimit-Remaining"] = str(remaining)
    response.headers["RateLimit-Reset"] = str(reset_in)

    if int(count) > limit:
        # 429 response — headers above are already on `response`, but FastAPI
        # builds a fresh response on HTTPException, so re-emit via detail headers.
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
            headers={
                "Retry-After": str(reset_in),
                "RateLimit-Limit": str(limit),
                "RateLimit-Remaining": "0",
                "RateLimit-Reset": str(reset_in),
            },
        )
