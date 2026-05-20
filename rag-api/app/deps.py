"""Singleton accessors for config, redis client, anthropic client.

Lazy-init keeps tests cheap and decouples module import from process env.
"""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import redis
    import anthropic
    from .config import Config


_lock = threading.Lock()
_config: "Config | None" = None
_redis: "redis.Redis | None" = None
_anthropic: "anthropic.Anthropic | None" = None


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


def get_anthropic() -> "anthropic.Anthropic":
    global _anthropic
    if _anthropic is None:
        with _lock:
            if _anthropic is None:
                import anthropic
                cfg = get_config()
                _anthropic = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
    return _anthropic


def reset_all() -> None:
    """Test hook — wipe all singletons."""
    global _config, _redis, _anthropic
    with _lock:
        _config = None
        _redis = None
        _anthropic = None
