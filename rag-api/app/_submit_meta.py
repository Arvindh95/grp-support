"""Per-job submit-time metadata that is private to the server.

Stored separately from the Job model so it never leaks via GET /jobs/{id}.
The worker reads this when running the pipeline + delivering the webhook.

Stored fields:
  callback_url, callback_secret_hint  — webhook config
  rfs                                  — the original RFS payload, fed to
                                         the pipeline by the worker
"""
from __future__ import annotations

import json
from typing import Any

from .deps import get_config, get_redis


def _key(job_id: str) -> str:
    return f"rag:submit:{job_id}"


def save_submit_meta(job_id: str, *, callback_url: str | None,
                     callback_secret_hint: str | None,
                     rfs: dict[str, Any] | None = None) -> None:
    cfg = get_config()
    data: dict[str, Any] = {
        "callback_url": callback_url,
        "callback_secret_hint": callback_secret_hint,
    }
    if rfs is not None:
        data["rfs"] = rfs
    get_redis().set(_key(job_id), json.dumps(data), ex=cfg.job_ttl_seconds)


def load_submit_meta(job_id: str) -> dict:
    raw = get_redis().get(_key(job_id))
    if raw is None:
        return {}
    return json.loads(raw)


def purge_attachment_content(job_id: str) -> None:
    """Strip heavy base64 attachment bodies from stored submit-meta once the
    job has been processed. The bytes are only needed while the pipeline runs;
    afterwards they are dead weight in Redis for the rest of JOB_TTL. Callback
    config and lightweight RFS fields (filenames, content types) are kept.
    The key's remaining TTL is preserved."""
    redis = get_redis()
    key = _key(job_id)
    raw = redis.get(key)
    if raw is None:
        return
    data = json.loads(raw)
    rfs = data.get("rfs")
    if not isinstance(rfs, dict):
        return
    attachments = rfs.get("attachments") or []
    stripped = False
    for att in attachments:
        if isinstance(att, dict) and att.get("content_b64"):
            att["content_b64"] = None
            stripped = True
    if not stripped:
        return
    ttl = redis.ttl(key)
    ex = ttl if isinstance(ttl, int) and ttl > 0 else get_config().job_ttl_seconds
    redis.set(key, json.dumps(data), ex=ex)
