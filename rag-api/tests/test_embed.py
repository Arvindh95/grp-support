"""Ollama embedding client — fully mocked."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import embed
from app.embed import EmbedError


def _resp(status_code=200, body=None):
    return SimpleNamespace(status_code=status_code, text="", json=lambda: body or {})


def test_embed_returns_vector(monkeypatch):
    vec = [0.1] * 1024
    monkeypatch.setattr(embed.requests, "post",
                        lambda *a, **kw: _resp(200, {"embedding": vec}))
    out = embed.embed_text("hello world")
    assert out == vec
    assert len(out) == 1024


def test_embed_raises_on_empty_input():
    with pytest.raises(EmbedError):
        embed.embed_text("")


def test_embed_raises_on_unreachable(monkeypatch):
    import requests
    def boom(*a, **kw):
        raise requests.ConnectionError("nope")
    monkeypatch.setattr(embed.requests, "post", boom)
    with pytest.raises(EmbedError):
        embed.embed_text("hi")


def test_embed_raises_on_non_200(monkeypatch):
    monkeypatch.setattr(embed.requests, "post",
                        lambda *a, **kw: _resp(500, {"error": "x"}))
    with pytest.raises(EmbedError):
        embed.embed_text("hi")


def test_embed_raises_on_missing_field(monkeypatch):
    monkeypatch.setattr(embed.requests, "post",
                        lambda *a, **kw: _resp(200, {"oops": "no embedding"}))
    with pytest.raises(EmbedError):
        embed.embed_text("hi")
