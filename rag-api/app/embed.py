"""Ollama bge-m3 embedding client.

Wraps the local Ollama daemon. Same dimensions as the existing `grp-manuals`
and `rfs-tickets-*` indices (1024d, cosine).
"""
from __future__ import annotations

import os
from typing import Any

import requests


OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "bge-m3")
_TIMEOUT_SECONDS = 15


class EmbedError(RuntimeError):
    pass


def embed_text(text: str) -> list[float]:
    """Return a 1024-d float vector for `text`. Raises EmbedError on failure."""
    if not text:
        raise EmbedError("empty input")
    try:
        r = requests.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": text},
            timeout=_TIMEOUT_SECONDS,
        )
    except requests.RequestException as e:
        raise EmbedError(f"ollama unreachable: {e}") from e
    if r.status_code != 200:
        raise EmbedError(f"ollama status {r.status_code}: {r.text[:200]}")
    body: dict[str, Any] = r.json()
    vec = body.get("embedding")
    if not isinstance(vec, list) or not vec:
        raise EmbedError(f"unexpected response: {body!r}")
    return vec
