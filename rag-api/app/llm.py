"""Anthropic Claude wrapper for agent calls.

Conventions
-----------
- Every agent has a *cached prefix* (system blocks, marked `cache_control`)
  and a *dynamic suffix* (the user-turn message body).
- All agents output JSON only. `call_agent_json()` extracts and parses; one
  retry on parse failure with a stricter reminder appended.
- Returns usage so the orchestrator can build an `AgentStep`.

The real Anthropic client lives in `deps.get_anthropic()`. Tests monkeypatch
`call_messages` (the single boundary touching the SDK).
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from .deps import get_anthropic

log = logging.getLogger("rag-api.llm")


class LLMError(RuntimeError):
    """Non-retryable LLM failure (bad input, budget exhaustion, etc.)."""


class LLMOverloaded(RuntimeError):
    """Retryable: model returned 529 or similar."""


class LLMParseError(RuntimeError):
    """Retryable once: response did not contain valid JSON."""


@dataclass
class LLMUsage:
    input_tokens: int = 0
    input_cache_read_tokens: int = 0
    input_cache_write_tokens: int = 0
    output_tokens: int = 0


@dataclass
class LLMResult:
    text: str
    parsed: dict[str, Any] | None
    usage: LLMUsage
    duration_ms: int
    stop_reason: str | None


# ── Single boundary with the SDK — easy to monkeypatch ─────────────────────────

def call_messages(
    *,
    model: str,
    system: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    max_tokens: int,
) -> Any:
    """Thin wrapper around `client.messages.create`. Returns the SDK Message."""
    client = get_anthropic()
    return client.messages.create(
        model=model,
        system=system,
        messages=messages,
        max_tokens=max_tokens,
    )


# ── Helpers ────────────────────────────────────────────────────────────────────

def cached_system(text: str) -> list[dict[str, Any]]:
    """Build a single system block marked as ephemeral-cache."""
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def _extract_text(message: Any) -> str:
    parts = []
    for block in getattr(message, "content", []) or []:
        # SDK objects expose .type and .text; mocks may use dicts.
        btype = getattr(block, "type", None) or (
            block.get("type") if isinstance(block, dict) else None
        )
        if btype != "text":
            continue
        text = getattr(block, "text", None) if not isinstance(block, dict) else block.get("text")
        if text:
            parts.append(text)
    return "".join(parts)


def _extract_usage(message: Any) -> LLMUsage:
    u = getattr(message, "usage", None)
    if u is None:
        return LLMUsage()
    def _g(name: str) -> int:
        val = getattr(u, name, None)
        if val is None and isinstance(u, dict):
            val = u.get(name)
        return int(val or 0)
    return LLMUsage(
        input_tokens=_g("input_tokens"),
        input_cache_read_tokens=_g("cache_read_input_tokens"),
        input_cache_write_tokens=_g("cache_creation_input_tokens"),
        output_tokens=_g("output_tokens"),
    )


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)
_BARE_JSON_RE = re.compile(r"(\{.*\})", re.DOTALL)


def parse_json(text: str) -> dict[str, Any]:
    """Strict-ish JSON extraction. Accepts fenced or bare JSON objects."""
    text = text.strip()
    if not text:
        raise LLMParseError("empty response")

    # Pure JSON
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    m = _JSON_FENCE_RE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    m = _BARE_JSON_RE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError as e:
            raise LLMParseError(f"bracket-bound JSON did not parse: {e}") from e

    raise LLMParseError("no JSON object found in response")


# ── Public surface ─────────────────────────────────────────────────────────────

STRICT_JSON_REMINDER = (
    "\n\nIMPORTANT: Your previous response did not contain valid JSON. "
    "Respond with ONE JSON object only. No prose, no markdown fences. "
    "Do not include explanations."
)


def call_agent_json(
    *,
    model: str,
    system_prompt: str,
    user_payload: dict[str, Any] | str,
    max_tokens: int,
    retry_on_parse_error: bool = True,
    attachment_blocks: list[dict[str, Any]] | None = None,
) -> LLMResult:
    """Call an agent and require a JSON object response.

    `attachment_blocks`, when given, are native Claude content blocks
    (image / document / text) appended after the JSON payload in the user
    turn — used to hand files to the Analyst and Verifier.

    Raises LLMOverloaded on 5xx-style transient failures, LLMError on
    permanent failures, LLMParseError if parsing still fails after one retry.
    """
    system_blocks = cached_system(system_prompt)
    user_content = user_payload if isinstance(user_payload, str) else json.dumps(
        user_payload, separators=(",", ":")
    )
    if attachment_blocks:
        content: Any = [{"type": "text", "text": user_content},
                        *attachment_blocks]
    else:
        content = user_content
    messages = [{"role": "user", "content": content}]

    start = time.monotonic()
    try:
        msg = call_messages(model=model, system=system_blocks, messages=messages,
                            max_tokens=max_tokens)
    except Exception as e:
        # Map a few well-known SDK errors. Anything we can't classify is a
        # hard failure — orchestrator decides whether to retry.
        name = e.__class__.__name__
        if name in ("APIStatusError", "InternalServerError", "APIConnectionError"):
            raise LLMOverloaded(str(e)) from e
        if name in ("RateLimitError",):
            raise LLMOverloaded(str(e)) from e
        if name in ("AuthenticationError", "PermissionDeniedError"):
            raise LLMError(f"auth: {e}") from e
        raise LLMError(f"{name}: {e}") from e

    text = _extract_text(msg)
    usage = _extract_usage(msg)
    duration_ms = int((time.monotonic() - start) * 1000)
    stop_reason = getattr(msg, "stop_reason", None)

    try:
        parsed = parse_json(text)
    except LLMParseError:
        if not retry_on_parse_error:
            raise
        log.warning('"llm.parse_retry model=%s"', model)
        messages2 = list(messages) + [
            {"role": "assistant", "content": text},
            {"role": "user", "content": STRICT_JSON_REMINDER},
        ]
        try:
            msg2 = call_messages(model=model, system=system_blocks,
                                 messages=messages2, max_tokens=max_tokens)
        except Exception as e:
            raise LLMOverloaded(str(e)) from e
        text2 = _extract_text(msg2)
        usage2 = _extract_usage(msg2)
        usage = LLMUsage(
            input_tokens=usage.input_tokens + usage2.input_tokens,
            input_cache_read_tokens=usage.input_cache_read_tokens + usage2.input_cache_read_tokens,
            input_cache_write_tokens=usage.input_cache_write_tokens + usage2.input_cache_write_tokens,
            output_tokens=usage.output_tokens + usage2.output_tokens,
        )
        duration_ms = int((time.monotonic() - start) * 1000)
        parsed = parse_json(text2)   # raises if still unparseable
        text = text2
        stop_reason = getattr(msg2, "stop_reason", None)

    return LLMResult(text=text, parsed=parsed, usage=usage,
                     duration_ms=duration_ms, stop_reason=stop_reason)
