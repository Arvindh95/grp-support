"""LLM wrapper — parse, retry, usage extraction."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import llm
from app.llm import LLMParseError, LLMOverloaded, LLMError


def _mk_msg(text: str, *, stop_reason: str = "end_turn",
            input_tokens: int = 100, output_tokens: int = 50,
            cache_read: int = 0, cache_write: int = 0):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_write,
        ),
    )


def test_parse_json_pure():
    assert llm.parse_json('{"a": 1}') == {"a": 1}


def test_parse_json_fenced():
    text = 'Here is the answer:\n```json\n{"a": 1}\n```\n'
    assert llm.parse_json(text) == {"a": 1}


def test_parse_json_bare_in_prose():
    text = 'Sure thing: {"a": 1, "b": [2,3]} done'
    assert llm.parse_json(text) == {"a": 1, "b": [2, 3]}


def test_parse_json_raises_on_garbage():
    with pytest.raises(LLMParseError):
        llm.parse_json("no json here, just words")


def test_call_agent_json_happy(monkeypatch):
    monkeypatch.setattr(llm, "call_messages",
                        lambda **kw: _mk_msg('{"category":"how-to","confidence":0.9}'))
    res = llm.call_agent_json(model="claude-haiku-4-5",
                              system_prompt="be a classifier",
                              user_payload={"notes": "hello"},
                              max_tokens=200)
    assert res.parsed == {"category": "how-to", "confidence": 0.9}
    assert res.usage.input_tokens == 100
    assert res.usage.output_tokens == 50
    assert res.duration_ms >= 0


def test_call_agent_json_retry_on_parse(monkeypatch):
    calls = {"n": 0}
    def fake(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return _mk_msg("I don't follow the format, sorry.")
        return _mk_msg('{"category":"how-to"}', input_tokens=50, output_tokens=10)
    monkeypatch.setattr(llm, "call_messages", fake)
    res = llm.call_agent_json(model="claude-haiku-4-5",
                              system_prompt="x",
                              user_payload="y",
                              max_tokens=200)
    assert res.parsed == {"category": "how-to"}
    assert calls["n"] == 2
    # Usage aggregated across both calls.
    assert res.usage.input_tokens == 150
    assert res.usage.output_tokens == 60


def test_call_agent_json_gives_up_after_one_retry(monkeypatch):
    monkeypatch.setattr(llm, "call_messages",
                        lambda **kw: _mk_msg("still no json"))
    with pytest.raises(LLMParseError):
        llm.call_agent_json(model="claude-haiku-4-5",
                            system_prompt="x", user_payload="y", max_tokens=200)


def test_call_agent_json_no_retry_when_disabled(monkeypatch):
    monkeypatch.setattr(llm, "call_messages",
                        lambda **kw: _mk_msg("no json"))
    with pytest.raises(LLMParseError):
        llm.call_agent_json(model="claude-haiku-4-5", system_prompt="x",
                            user_payload="y", max_tokens=200,
                            retry_on_parse_error=False)


def test_overloaded_mapped_to_retryable(monkeypatch):
    class APIStatusError(Exception):
        pass
    def fake(**kw):
        raise APIStatusError("529 overloaded")
    monkeypatch.setattr(llm, "call_messages", fake)
    with pytest.raises(LLMOverloaded):
        llm.call_agent_json(model="claude-haiku-4-5", system_prompt="x",
                            user_payload="y", max_tokens=200)


def test_auth_error_mapped_to_terminal(monkeypatch):
    class AuthenticationError(Exception):
        pass
    monkeypatch.setattr(llm, "call_messages",
                        lambda **kw: (_ for _ in ()).throw(AuthenticationError("bad key")))
    with pytest.raises(LLMError):
        llm.call_agent_json(model="claude-haiku-4-5", system_prompt="x",
                            user_payload="y", max_tokens=200)


def test_call_messages_retries_transient(monkeypatch):
    """A transient 5xx/529 is retried with backoff until it succeeds."""
    class InternalServerError(Exception):
        pass
    calls = {"n": 0}
    def create(**kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise InternalServerError("Error code: 529 - overloaded")
        return _mk_msg('{"ok":1}')
    fake_client = SimpleNamespace(messages=SimpleNamespace(create=create))
    monkeypatch.setattr(llm, "get_anthropic", lambda: fake_client)
    monkeypatch.setattr(llm.time, "sleep", lambda _s: None)

    msg = llm.call_messages(model="m", system=[], messages=[], max_tokens=10)
    assert calls["n"] == 3   # failed twice, succeeded on the third try
    assert msg.stop_reason == "end_turn"


def test_call_messages_does_not_retry_client_error(monkeypatch):
    """A 4xx client error is raised immediately — never retried."""
    class BadRequestError(Exception):
        pass
    calls = {"n": 0}
    def create(**kw):
        calls["n"] += 1
        raise BadRequestError("Error code: 400 - bad request")
    fake_client = SimpleNamespace(messages=SimpleNamespace(create=create))
    monkeypatch.setattr(llm, "get_anthropic", lambda: fake_client)
    monkeypatch.setattr(llm.time, "sleep", lambda _s: None)

    with pytest.raises(BadRequestError):
        llm.call_messages(model="m", system=[], messages=[], max_tokens=10)
    assert calls["n"] == 1   # not retried


def test_call_messages_gives_up_after_max_retries(monkeypatch):
    """A persistent transient error is raised after exhausting retries."""
    class InternalServerError(Exception):
        pass
    calls = {"n": 0}
    def create(**kw):
        calls["n"] += 1
        raise InternalServerError("529 overloaded")
    fake_client = SimpleNamespace(messages=SimpleNamespace(create=create))
    monkeypatch.setattr(llm, "get_anthropic", lambda: fake_client)
    monkeypatch.setattr(llm.time, "sleep", lambda _s: None)

    with pytest.raises(InternalServerError):
        llm.call_messages(model="m", system=[], messages=[], max_tokens=10)
    # 1 initial + llm_max_retries (default 5) = 6 attempts.
    assert calls["n"] == 6


def test_cached_system_marks_ephemeral():
    blocks = llm.cached_system("hello")
    assert blocks == [{"type": "text", "text": "hello",
                       "cache_control": {"type": "ephemeral"}}]


def test_extract_usage_handles_missing(monkeypatch):
    msg = SimpleNamespace(content=[SimpleNamespace(type="text", text="{}")],
                          stop_reason="end_turn", usage=None)
    monkeypatch.setattr(llm, "call_messages", lambda **kw: msg)
    res = llm.call_agent_json(model="m", system_prompt="s",
                              user_payload="u", max_tokens=10)
    assert res.usage.input_tokens == 0
    assert res.usage.output_tokens == 0
