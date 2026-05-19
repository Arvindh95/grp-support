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
