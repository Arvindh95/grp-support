"""/health and /ready."""
from __future__ import annotations

import requests
from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from ..deps import get_config, get_redis
from ..models import Readiness, ReadinessDeps


router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok"}


def _check_es(cfg) -> bool:
    try:
        r = requests.get(
            f"{cfg.es_url}/_cluster/health",
            auth=(cfg.es_user, cfg.es_password),
            verify=cfg.es_verify_tls,
            timeout=2,
        )
        return r.status_code == 200
    except requests.RequestException:
        return False


def _check_ollama() -> bool:
    try:
        r = requests.get("http://127.0.0.1:11434/api/tags", timeout=2)
        return r.status_code == 200
    except requests.RequestException:
        return False


def _check_anthropic(cfg) -> bool:
    # Cheap: just verify the key looks like an Anthropic key. Hitting the
    # API on every /ready call would burn quota.
    return bool(cfg.anthropic_api_key and cfg.anthropic_api_key.startswith("sk-ant-"))


def _check_redis() -> bool:
    try:
        return get_redis().ping()
    except Exception:
        return False


@router.get("/ready")
def ready():
    cfg = get_config()
    deps = ReadinessDeps(
        elasticsearch=_check_es(cfg),
        ollama=_check_ollama(),
        anthropic=_check_anthropic(cfg),
        redis=_check_redis(),
    )
    all_up = all([deps.elasticsearch, deps.ollama, deps.anthropic, deps.redis])
    out = Readiness(status="ready" if all_up else "degraded", deps=deps)
    code = status.HTTP_200_OK if all_up else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(status_code=code, content=out.model_dump(mode="json"))
