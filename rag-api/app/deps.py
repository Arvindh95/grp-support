"""Singleton accessors for config, redis client, anthropic client.

Lazy-init keeps tests cheap and decouples module import from process env.
"""
from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import redis
    import anthropic
    from .config import Config


_lock = threading.Lock()
_config: "Config | None" = None
_redis: "redis.Redis | None" = None
_anthropic: "anthropic.Anthropic | None" = None

# Live-reloadable Claude key: the one stored in the grp-settings ES index
# (set via the admin UI) overrides the env ANTHROPIC_API_KEY. Cached ~60s so
# a UI change takes effect without a restart.
_SETTINGS_INDEX = "grp-settings"
_ANTHROPIC_KEY_TTL = 60
_anthropic_key_cache: dict = {"key": None, "at": 0.0}
_anthropic_client_key: str | None = None


def get_config() -> "Config":
    global _config
    if _config is None:
        with _lock:
            if _config is None:
                from .config import load_config
                _config = load_config()
    return _config


def set_config(cfg: "Config") -> None:
    """Test hook only."""
    global _config
    with _lock:
        _config = cfg


def get_redis() -> "redis.Redis":
    global _redis
    if _redis is None:
        with _lock:
            if _redis is None:
                import redis
                cfg = get_config()
                _redis = redis.Redis.from_url(cfg.redis_url, decode_responses=True)
    return _redis


def set_redis(client) -> None:
    """Test hook — inject fakeredis."""
    global _redis
    with _lock:
        _redis = client


def _stored_anthropic_key(cfg: "Config") -> str | None:
    """The admin-set Claude key from the grp-settings ES index, or None.
    Cached ~60s; on an ES error keeps the last known value and backs off."""
    now = time.monotonic()
    if now - _anthropic_key_cache["at"] < _ANTHROPIC_KEY_TTL:
        return _anthropic_key_cache["key"]
    import requests
    try:
        r = requests.get(
            f"{cfg.es_url}/{_SETTINGS_INDEX}/_doc/anthropic",
            auth=(cfg.es_user, cfg.es_password),
            verify=cfg.es_verify_tls, timeout=5,
        )
        key = None
        if r.status_code == 200:
            key = (r.json().get("_source") or {}).get("key") or None
        _anthropic_key_cache["key"] = key
        _anthropic_key_cache["at"] = now
        return key
    except requests.RequestException:
        _anthropic_key_cache["at"] = now   # back off — retry in ~60s
        return _anthropic_key_cache["key"]


def get_anthropic() -> "anthropic.Anthropic":
    """Anthropic client built with the effective key (UI-set value overrides
    env). The client is rebuilt whenever the effective key changes."""
    global _anthropic, _anthropic_client_key
    cfg = get_config()
    key = _stored_anthropic_key(cfg) or cfg.anthropic_api_key
    with _lock:
        if _anthropic is None or _anthropic_client_key != key:
            import anthropic
            _anthropic = anthropic.Anthropic(api_key=key)
            _anthropic_client_key = key
    return _anthropic


def reset_all() -> None:
    """Test hook — wipe all singletons."""
    global _config, _redis, _anthropic, _anthropic_client_key
    with _lock:
        _config = None
        _redis = None
        _anthropic = None
        _anthropic_client_key = None
        _anthropic_key_cache["key"] = None
        _anthropic_key_cache["at"] = 0.0
