"""Env-driven config. Loaded once at process start."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(s: str | None, default: bool = False) -> bool:
    if s is None:
        return default
    return s.lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    # Shared with grp-api
    es_url: str
    es_user: str
    es_password: str
    es_verify_tls: bool

    # Redis — DB 3 to avoid CSAI-OCR (db 0) and any future grp-api use.
    redis_url: str

    # Anthropic
    anthropic_api_key: str

    # Webhook signing default secret (per-key secrets come from registry later)
    webhook_default_secret: str

    # Rate limit
    rate_limit_per_minute: int

    # Limits
    max_body_bytes: int
    max_attachment_bytes: int   # total decoded size of all attachments per RFS
    idempotency_ttl_seconds: int
    job_ttl_seconds: int

    # Worker
    worker_concurrency: int
    worker_poll_interval_seconds: float

    # Budget
    monthly_token_budget: int   # 0 = unlimited

    # Observability
    log_level: str
    trace_index_prefix: str

    # Pipeline — when False, the Classifier's short_circuit verdict is
    # ignored and every RFS runs the full 5-agent pipeline.
    short_circuit_enabled: bool = True


def load_config() -> Config:
    return Config(
        es_url=os.environ.get("ES_URL", "https://localhost:9200"),
        es_user=os.environ.get("ES_USER", "elastic"),
        es_password=os.environ["ES_PASSWORD"],
        es_verify_tls=_bool(os.environ.get("ES_VERIFY_TLS"), default=False),
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/3"),
        anthropic_api_key=os.environ["ANTHROPIC_API_KEY"],
        webhook_default_secret=os.environ.get("WEBHOOK_DEFAULT_SECRET", ""),
        rate_limit_per_minute=int(os.environ.get("RATE_LIMIT_PER_MINUTE", "60")),
        max_body_bytes=int(os.environ.get("MAX_BODY_BYTES", str(40 * 1024 * 1024))),
        max_attachment_bytes=int(os.environ.get(
            "MAX_ATTACHMENT_BYTES", str(25 * 1024 * 1024))),
        idempotency_ttl_seconds=int(os.environ.get("IDEMPOTENCY_TTL", str(24 * 3600))),
        job_ttl_seconds=int(os.environ.get("JOB_TTL", str(7 * 24 * 3600))),
        worker_concurrency=int(os.environ.get("WORKER_CONCURRENCY", "2")),
        worker_poll_interval_seconds=float(os.environ.get("WORKER_POLL_INTERVAL", "0.5")),
        monthly_token_budget=int(os.environ.get("MONTHLY_TOKEN_BUDGET", "0")),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        trace_index_prefix=os.environ.get("TRACE_INDEX_PREFIX", "rag-api-trace"),
        short_circuit_enabled=_bool(os.environ.get("SHORT_CIRCUIT_ENABLED"),
                                    default=True),
    )
