"""Agent 5 — LLM Formatter (Haiku).

Contract: rag-api/contracts/05-formatter.md

Converts internal agent outputs into the public `Analysis` shape. Adds
nicer phrasing and tighter snippet selection over the deterministic
formatter. If the LLM Formatter fails twice (parse, schema, or
source_refs/citations cross-check), the orchestrator falls back to
`formatter_deterministic.format_analysis()` — see pipeline.py.

This module never produces an invalid Analysis on a successful return:
- Returns (Analysis, AgentStep) on success.
- Raises `FormatterError` on persistent failure — orchestrator catches.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Sequence

from pydantic import ValidationError

from .. import llm
from ..models import (
    AgentName,
    AgentStep,
    AgentStepStatus,
    Analysis,
    VerifierFlag,
)
from ..retrieval import RetrievedChunk
from .analyst import AnalystOutput
from .classifier import ClassifierOutput
from .verifier import VerifierOutput

log = logging.getLogger("rag-api.agent.formatter")

MODEL = "claude-haiku-4-5"
# 12 retrieved chunks → up to ~10 citations + 10 actions of JSON. 1500 was
# too tight and truncated the response mid-object → unparseable.
MAX_TOKENS = 4000


class FormatterError(RuntimeError):
    """Raised when the LLM Formatter cannot produce a valid Analysis after
    one retry. Orchestrator should fall back to the deterministic formatter.
    """


SYSTEM_PROMPT = """You are a formatter for a GRP / Acumatica ERP support pipeline.

You will receive the outputs of prior agents and the retrieved evidence
chunks. Your ONLY job is to assemble a public Analysis JSON object that
matches the schema below. Do not invent content. Do not rewrite or
paraphrase the Analyst's claims — copy them. You may trim and you may
choose which sentence of a chunk becomes the citation snippet.

Schema (must match exactly):
{
  "category":    "<copy from classifier_output.category>",
  "confidence":  <float in [0,1]; copy min(classifier.confidence, analyst.confidence)>,
  "summary":     "<<=600 chars, copy or trim analyst.summary>",
  "likely_cause":"<<=1000 chars, copy or trim analyst.likely_cause, or null>",
  "recommended_actions": [
    {
      "step": <int>,
      "detail": "<<=2000 chars, copy the analyst action.detail in full — do NOT truncate mid-sentence>",
      "source_refs": ["cit-1", "cit-2", ...]   // must reference citations[].id
    },
    ...    // 1 to 10 items
  ],
  "citations": [
    {
      "id": "cit-1",
      "source": "manual" | "rfs_ticket" | "code_script" | "attachment",
      "locator": <copy locator from the matching retrieved_context chunk>,
      "snippet": "<<=400 chars, one sentence from the chunk text>",
      "score": <float>
    },
    ...
  ],
  "related_rfs": [
    { "lodge_id": "<from a chunk where source=rfs_ticket>",
      "score": <float>, "snippet": "<<=240 chars>" },
    ...    // up to 5
  ],
  "verifier_flags": [
    { "kind": "unsupported_claim" | "weak_citation" | "low_confidence" | "retrieval_gap",
      "detail": "<copy from verifier_output.flags[].detail>" },
    ...
  ]
}

HARD RULES (violating any of these will cause a retry):
  1. Every recommended_action.source_refs[] entry MUST be the `id` of an
     entry in citations[] (cit-1, cit-2, ...). Never invent an id.
  2. Every citations[].locator MUST be copied verbatim from a retrieved
     context chunk whose chunk_id you are converting to cit-N.
  3. Number citations in order of first reference (the citation referenced
     by action 1 first → cit-1; next new one → cit-2; ...). No gaps.
  4. Snippets are <=400 chars. Take a single representative sentence
     from the chunk text. Do not summarize across sentences. Do not
     fabricate sentences not present in the chunk.
  5. verifier_flags are copied from verifier_output.flags VERBATIM (kind +
     detail). Do not add new flag kinds.
  6. Output VALID JSON only. No prose, no markdown.

Field-by-field mapping cheat-sheet:
  - category, classifier confidence  → from classifier_output
  - summary, likely_cause, actions   → from analyst_output  (TRIM ONLY)
  - chunks → citations + related_rfs → from retrieved_context
  - verifier_flags                   → from verifier_output.flags

Tie-breakers:
  - Analyst action references chunk_id "X" not present in retrieved_context
    → drop that source_ref (it won't pass validation either way).
  - Multiple chunks with same lodge_id in related_rfs → pick the one with
    highest score, drop duplicates.
  - More than 5 related_rfs → keep top 5 by score.
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _compact_payload(
    classifier_output: ClassifierOutput,
    analyst_output: AnalystOutput,
    verifier_output: VerifierOutput,
    chunks: Sequence[RetrievedChunk],
) -> dict[str, Any]:
    return {
        "classifier_output": {
            "category": classifier_output.category,
            "confidence": classifier_output.confidence,
            "tags": classifier_output.tags,
        },
        "analyst_output": {
            "summary": analyst_output.summary[:600],
            "likely_cause": (analyst_output.likely_cause or "")[:1000] or None,
            "confidence": analyst_output.confidence,
            "recommended_actions": [
                {"step": a.step, "detail": a.detail[:2000],
                 "citations": list(a.citations)}
                for a in analyst_output.recommended_actions
            ],
            "claims": [
                {"id": c.id, "text": c.text[:400],
                 "citations": list(c.citations)}
                for c in analyst_output.claims
            ],
        },
        "verifier_output": {
            "verdict": verifier_output.verdict,
            "flags": [{"kind": f.kind.value, "detail": f.detail}
                      for f in verifier_output.flags],
        },
        "retrieved_context": [
            {
                "chunk_id": c.chunk_id,
                "source": c.kind,
                "locator": c.locator,
                "text": (c.text or "")[:500],
                "score": c.score,
            } for c in chunks
        ],
    }


def _cross_check(analysis: Analysis) -> None:
    """Source_refs must resolve to citations[].id. Raise FormatterError if not."""
    cit_ids = {c.id for c in analysis.citations}
    for i, a in enumerate(analysis.recommended_actions):
        for ref in a.source_refs:
            if ref not in cit_ids:
                raise FormatterError(
                    f"action[{i}].source_refs[{ref!r}] not in citations")
    if len(analysis.verifier_flags) > 50:
        # Sanity guard against pathological output.
        raise FormatterError("too many verifier_flags")


def _validate(parsed: dict[str, Any]) -> Analysis:
    """Pydantic-validate then cross-check source_refs ↔ citations."""
    try:
        analysis = Analysis.model_validate(parsed)
    except ValidationError as e:
        raise FormatterError(f"schema: {e.errors()[:3]}") from e
    _cross_check(analysis)
    return analysis


# ── Public entrypoint ─────────────────────────────────────────────────────────

def format_via_llm(
    classifier_output: ClassifierOutput,
    analyst_output: AnalystOutput,
    verifier_output: VerifierOutput,
    chunks: Sequence[RetrievedChunk],
) -> tuple[Analysis, AgentStep]:
    """Run the LLM Formatter. Raises FormatterError on persistent failure."""
    payload = _compact_payload(classifier_output, analyst_output,
                               verifier_output, chunks)

    # First attempt.
    try:
        res = llm.call_agent_json(
            model=MODEL, system_prompt=SYSTEM_PROMPT,
            user_payload=payload, max_tokens=MAX_TOKENS,
        )
    except llm.LLMParseError as e:
        raise FormatterError(f"unparseable_first: {e}") from e

    parsed = res.parsed or {}
    first_err: FormatterError | None = None
    try:
        analysis = _validate(parsed)
        step = AgentStep(
            agent=AgentName.formatter, model=MODEL,
            status=AgentStepStatus.ok,
            duration_ms=res.duration_ms,
            input_tokens=res.usage.input_tokens,
            input_cache_read_tokens=res.usage.input_cache_read_tokens,
            output_tokens=res.usage.output_tokens,
        )
        return analysis, step
    except FormatterError as e:
        first_err = e
        log.warning('"formatter.invalid attempt=1 err=%s"', e)

    # Retry once with the validator's complaint.
    suffix = (
        "Your previous response failed validation: " + str(first_err) +
        ". Fix it and return ONE valid JSON object matching the schema. "
        "Recall: source_refs must reference an id in citations; every "
        "citations[].locator must come from retrieved_context; snippets <=400 chars."
    )
    try:
        res2 = llm.call_agent_json(
            model=MODEL, system_prompt=SYSTEM_PROMPT + "\n\n" + suffix,
            user_payload=payload, max_tokens=MAX_TOKENS,
        )
    except llm.LLMParseError as e2:
        raise FormatterError(f"unparseable_retry: {e2}") from e2

    parsed2 = res2.parsed or {}
    combined_usage = llm.LLMUsage(
        input_tokens=res.usage.input_tokens + res2.usage.input_tokens,
        input_cache_read_tokens=res.usage.input_cache_read_tokens
            + res2.usage.input_cache_read_tokens,
        input_cache_write_tokens=res.usage.input_cache_write_tokens
            + res2.usage.input_cache_write_tokens,
        output_tokens=res.usage.output_tokens + res2.usage.output_tokens,
    )
    combined_ms = res.duration_ms + res2.duration_ms

    try:
        analysis = _validate(parsed2)
        step = AgentStep(
            agent=AgentName.formatter, model=MODEL,
            status=AgentStepStatus.retried,
            duration_ms=combined_ms,
            input_tokens=combined_usage.input_tokens,
            input_cache_read_tokens=combined_usage.input_cache_read_tokens,
            output_tokens=combined_usage.output_tokens,
            note="retried after validation_failed",
        )
        return analysis, step
    except FormatterError as e2:
        raise FormatterError(f"validation_failed_twice: {e2}") from e2
