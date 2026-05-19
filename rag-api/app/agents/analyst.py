"""Agent 3 — Analyst (Sonnet).

Contract: rag-api/contracts/03-analyst.md

The single Sonnet step. Reads RFS + ClassifierOutput + retrieved chunks,
produces a structured analysis with claims and chunk-id citations. Hard
rule: every claim and every recommended_action has >= 1 citation drawn
from the supplied retrieved_context.

Cache strategy
--------------
System prompt is marked ephemeral-cache. Dynamic suffix is RFS + chunks
(rebuilt each call). Per contract the cache hit on Sonnet input is the
~55% target, which is the dominant cost lever.

Retry policy
------------
- output_unparseable → llm helper already retries once.
- claim_without_citation / citation_unresolved → ONE retry with the
  validator's complaint appended.
- After both fail: surface verifier_flags.unsupported_claim and return.
  Downstream Verifier/Formatter still produce a usable Analysis.

Opus escalation
---------------
A future Verifier verdict of `must_retry` triggers a one-shot retry on
claude-opus-4-7 via `retry_with_opus()`. Same prompt, same context,
different model. Capped at 2% of traffic at the orchestrator level.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

from .. import llm
from ..models import AgentName, AgentStep, AgentStepStatus, RFS
from ..retrieval import RetrievedChunk
from .classifier import ClassifierOutput

log = logging.getLogger("rag-api.agent.analyst")

MODEL_DEFAULT = "claude-sonnet-4-6"
MODEL_RETRY = "claude-opus-4-7"
MAX_TOKENS = 2200


# ── System prompt (cached) ─────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a GRP / Acumatica ERP support analyst.

You will receive:
  - rfs: a support ticket payload (lodge_id, notes, relatedarea, ...)
  - classifier_output: category + tags from a prior agent
  - retrieved_context: a list of evidence chunks pulled from manuals,
    past tickets, and code scripts. Each chunk has a `chunk_id`, a
    `source` ("manual" | "rfs_ticket" | "code_script"), a `locator`,
    a `text` body, and a `score`.

Produce ONE JSON object only. No prose, no markdown fences.

Schema:
{
  "summary": "<1-3 sentence problem restatement, <=600 chars>",
  "likely_cause": "<root-cause hypothesis or null, <=1000 chars>",
  "recommended_actions": [
    {
      "step": <int, 1-based>,
      "detail": "<concrete next step, <=600 chars>",
      "citations": ["<chunk_id>", ...]    // >=1 entry, all from retrieved_context
    },
    ...
  ],
  "claims": [
    {
      "id": "claim-1",
      "text": "<factual claim that supports the analysis>",
      "supports_step": [<int>, ...],
      "citations": ["<chunk_id>", ...]    // >=1 entry, all from retrieved_context
    },
    ...
  ],
  "confidence": <float 0..1>,
  "open_questions": ["<unanswered prerequisite>", ...]
}

HARD RULES — violating any of these is unacceptable:
  1. Every recommended_action has citations.length >= 1.
  2. Every claim has citations.length >= 1.
  3. Every cited chunk_id MUST exist in retrieved_context. Never invent ids.
  4. State ONLY facts you can support from retrieved_context. If evidence
     is missing, lower `confidence` and add an `open_questions` entry.
     Do not fabricate URLs, version numbers, screen codes, or commands.
  5. Output is valid JSON only.

Confidence calibration:
  >=0.80 = a manual chunk directly documents the fix.
  0.50-0.80 = best guess from past tickets with similar symptoms.
  <0.50 = retrieval did not surface anything strong; mostly open_questions.

Style:
  - Recommended actions are concrete: a command, a UI path, a config flag.
    Avoid vague verbs like "investigate", "look into", "check things".
  - Use lodge_ids from retrieved_context where relevant (e.g. "as in
    LDG-90211"), and they must be cited.
  - Keep summary short. Keep likely_cause short. Spend tokens on actions.

If retrieved_context is empty: return confidence=0.0, a one-line summary
that says retrieval found nothing, one open_question listing what would
help, and one recommended_action that says "Gather more context: <X>".
That recommended_action's citations array may be empty IF AND ONLY IF
retrieved_context is also empty.
"""


# ── Output types ───────────────────────────────────────────────────────────────

@dataclass
class AnalystAction:
    step: int
    detail: str
    citations: list[str]


@dataclass
class AnalystClaim:
    id: str
    text: str
    supports_step: list[int]
    citations: list[str]


@dataclass
class AnalystOutput:
    summary: str
    likely_cause: str | None
    recommended_actions: list[AnalystAction]
    claims: list[AnalystClaim]
    confidence: float
    open_questions: list[str] = field(default_factory=list)
    unsupported_flag: str | None = None     # set when both attempts failed validation


class AnalystValidationError(ValueError):
    pass


# ── Validation ─────────────────────────────────────────────────────────────────

def _validate(parsed: dict[str, Any],
              retrieved_ids: set[str],
              *, allow_empty_citations: bool = False) -> AnalystOutput:
    """Strict structural + citation validation. Raises AnalystValidationError."""
    try:
        summary = str(parsed["summary"]).strip()
        confidence = float(parsed.get("confidence", 0.0))
        likely_cause = parsed.get("likely_cause")
        if likely_cause is not None:
            likely_cause = str(likely_cause).strip() or None
        raw_actions = parsed["recommended_actions"]
        raw_claims = parsed.get("claims") or []
        open_questions = [str(q) for q in (parsed.get("open_questions") or [])]
    except (KeyError, TypeError, ValueError) as e:
        raise AnalystValidationError(f"missing/bad field: {e}") from e

    if not (0.0 <= confidence <= 1.0):
        raise AnalystValidationError(f"confidence out of range: {confidence}")
    if not isinstance(raw_actions, list) or not raw_actions:
        raise AnalystValidationError("recommended_actions must be a non-empty list")

    actions: list[AnalystAction] = []
    for i, a in enumerate(raw_actions):
        if not isinstance(a, dict):
            raise AnalystValidationError(f"action[{i}] not an object")
        try:
            step_i = int(a["step"])
            detail = str(a["detail"]).strip()
            citations = list(a.get("citations") or [])
        except (KeyError, TypeError, ValueError) as e:
            raise AnalystValidationError(f"action[{i}] bad shape: {e}") from e
        if not detail:
            raise AnalystValidationError(f"action[{i}] empty detail")
        if not allow_empty_citations and not citations:
            raise AnalystValidationError(f"action[{i}] has no citations")
        for cid in citations:
            if cid not in retrieved_ids:
                raise AnalystValidationError(
                    f"action[{i}] cites unknown chunk_id {cid!r}")
        actions.append(AnalystAction(step=step_i, detail=detail,
                                     citations=[str(c) for c in citations]))

    claims: list[AnalystClaim] = []
    if not isinstance(raw_claims, list):
        raise AnalystValidationError("claims must be a list")
    for i, c in enumerate(raw_claims):
        if not isinstance(c, dict):
            raise AnalystValidationError(f"claim[{i}] not an object")
        try:
            cid = str(c["id"])
            text = str(c["text"]).strip()
            supports = [int(s) for s in (c.get("supports_step") or [])]
            citations = list(c.get("citations") or [])
        except (KeyError, TypeError, ValueError) as e:
            raise AnalystValidationError(f"claim[{i}] bad shape: {e}") from e
        if not text:
            raise AnalystValidationError(f"claim[{i}] empty text")
        if not citations:
            raise AnalystValidationError(f"claim[{i}] has no citations")
        for ref in citations:
            if ref not in retrieved_ids:
                raise AnalystValidationError(
                    f"claim[{i}] cites unknown chunk_id {ref!r}")
        claims.append(AnalystClaim(id=cid, text=text, supports_step=supports,
                                   citations=[str(c) for c in citations]))

    return AnalystOutput(
        summary=summary,
        likely_cause=likely_cause,
        recommended_actions=actions,
        claims=claims,
        confidence=confidence,
        open_questions=open_questions,
    )


# ── Public entrypoint ──────────────────────────────────────────────────────────

def analyze(
    rfs: RFS,
    classifier_output: ClassifierOutput,
    chunks: Sequence[RetrievedChunk],
    *,
    model: str = MODEL_DEFAULT,
    extra_system_suffix: str | None = None,
) -> tuple[AnalystOutput, AgentStep]:
    """Run the Analyst. Returns (AnalystOutput, AgentStep)."""
    retrieved_ids = {c.chunk_id for c in chunks}
    empty_retrieval = len(chunks) == 0

    payload = {
        "rfs": {
            "lodge_id": rfs.lodge_id,
            "notes": rfs.notes,
            "relatedarea": rfs.relatedarea,
            "priority": rfs.priority,
        },
        "classifier_output": {
            "category": classifier_output.category,
            "confidence": classifier_output.confidence,
            "tags": classifier_output.tags,
        },
        "retrieved_context": [c.to_dict() for c in chunks],
    }

    system = SYSTEM_PROMPT
    if extra_system_suffix:
        system = SYSTEM_PROMPT + "\n\n" + extra_system_suffix

    # First call.
    try:
        res = llm.call_agent_json(
            model=model, system_prompt=system,
            user_payload=payload, max_tokens=MAX_TOKENS,
        )
    except llm.LLMParseError as e:
        return _unsupported_fallback(rfs, classifier_output,
                                     reason=f"unparseable: {e}",
                                     usage=None, duration_ms=0, model=model)

    parsed = res.parsed or {}
    try:
        out = _validate(parsed, retrieved_ids,
                        allow_empty_citations=empty_retrieval)
        step = _step(AgentStepStatus.ok, res.duration_ms, res.usage, model)
        if out.confidence < 0.20:
            step.note = "low_confidence"
        return out, step
    except AnalystValidationError as e:
        first_err = str(e)
        log.warning('"analyst.validation_failed attempt=1 err=%s"', first_err)

    # Retry once with the validator complaint appended.
    suffix = (
        "Your previous response failed validation: " + first_err +
        ". Fix the issue. Every claim and every recommended_action must "
        "cite at least one chunk_id present in retrieved_context. "
        "Return one valid JSON object only."
    )
    try:
        res2 = llm.call_agent_json(
            model=model, system_prompt=system + "\n\n" + suffix,
            user_payload=payload, max_tokens=MAX_TOKENS,
        )
    except llm.LLMParseError as e2:
        usage = res.usage
        return _unsupported_fallback(
            rfs, classifier_output,
            reason=f"retry_unparseable: {e2}",
            usage=usage, duration_ms=res.duration_ms, model=model,
            first_attempt_parsed=parsed,
        )

    combined_usage = _sum_usage(res.usage, res2.usage)
    combined_ms = res.duration_ms + res2.duration_ms
    parsed2 = res2.parsed or {}
    try:
        out = _validate(parsed2, retrieved_ids,
                        allow_empty_citations=empty_retrieval)
        step = _step(AgentStepStatus.retried, combined_ms, combined_usage,
                     model, note="retried after validation_failed")
        if out.confidence < 0.20:
            step.note = (step.note or "") + "; low_confidence"
        return out, step
    except AnalystValidationError as e2:
        return _unsupported_fallback(
            rfs, classifier_output,
            reason=f"validation_failed_twice: {e2}",
            usage=combined_usage, duration_ms=combined_ms,
            model=model, first_attempt_parsed=parsed2,
        )


def retry_with_opus(
    rfs: RFS,
    classifier_output: ClassifierOutput,
    chunks: Sequence[RetrievedChunk],
    verifier_reason: str,
) -> tuple[AnalystOutput, AgentStep]:
    """Re-run the Analyst on claude-opus-4-7 after Verifier returned must_retry."""
    suffix = (
        "An earlier draft was rejected by the Verifier. Reason: "
        + verifier_reason
        + ". Produce a new analysis that addresses the issue."
    )
    return analyze(rfs, classifier_output, chunks,
                   model=MODEL_RETRY, extra_system_suffix=suffix)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _step(status: AgentStepStatus, duration_ms: int,
          usage: "llm.LLMUsage | None", model: str,
          note: str | None = None) -> AgentStep:
    u = usage or llm.LLMUsage()
    return AgentStep(
        agent=AgentName.analyst, model=model, status=status,
        duration_ms=duration_ms,
        input_tokens=u.input_tokens,
        input_cache_read_tokens=u.input_cache_read_tokens,
        output_tokens=u.output_tokens,
        note=note,
    )


def _sum_usage(a: "llm.LLMUsage", b: "llm.LLMUsage") -> "llm.LLMUsage":
    return llm.LLMUsage(
        input_tokens=a.input_tokens + b.input_tokens,
        input_cache_read_tokens=a.input_cache_read_tokens + b.input_cache_read_tokens,
        input_cache_write_tokens=a.input_cache_write_tokens + b.input_cache_write_tokens,
        output_tokens=a.output_tokens + b.output_tokens,
    )


def _unsupported_fallback(
    rfs: RFS,
    classifier_output: ClassifierOutput,
    *,
    reason: str,
    usage: "llm.LLMUsage | None",
    duration_ms: int,
    model: str,
    first_attempt_parsed: dict[str, Any] | None = None,
) -> tuple[AnalystOutput, AgentStep]:
    """When the model can't produce a citation-valid analysis, return a minimal
    Analyst output that still satisfies downstream Formatter validation but
    carries `unsupported_flag` so the Verifier/Formatter can surface it as
    `verifier_flags.unsupported_claim` in the public Analysis."""
    log.warning('"analyst.unsupported_fallback reason=%s"', reason)
    out = AnalystOutput(
        summary=(
            f"Analyst could not produce a citation-valid analysis for "
            f"RFS {rfs.lodge_id} ({classifier_output.category}). "
            "Manual triage recommended."
        )[:600],
        likely_cause=None,
        recommended_actions=[
            AnalystAction(
                step=1,
                detail="Triage manually — Analyst output failed citation validation.",
                citations=[],
            )
        ],
        claims=[],
        confidence=0.0,
        open_questions=[f"Analyst error: {reason}"],
        unsupported_flag=reason,
    )
    step = _step(AgentStepStatus.failed, duration_ms, usage, model,
                 note=f"unsupported: {reason}")
    return out, step
