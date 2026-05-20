"""Agent 4 — Verifier.

Contract: rag-api/contracts/04-verifier.md

Reads the Analyst's draft and the retrieved chunks. Runs a 5-check rubric.
Returns one of:
  - pass      → forward Analyst output unchanged
  - flag      → forward, attach non-fatal flags
  - must_retry → orchestrator retries Analyst on Opus once

The Verifier itself NEVER blocks the pipeline. Output errors degrade to
verdict=flag with a low_confidence flag.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

from .. import llm
from ..models import (
    AgentName,
    AgentStep,
    AgentStepStatus,
    Analysis,
    VerifierFlag,
    VerifierFlagKind,
)
from ..retrieval import RetrievedChunk
from .analyst import AnalystOutput

log = logging.getLogger("rag-api.agent.verifier")

MODEL = "claude-haiku-4-5"
MAX_TOKENS = 600

VERDICT_PASS = "pass"
VERDICT_FLAG = "flag"
VERDICT_MUST_RETRY = "must_retry"

_VALID_VERDICTS = {VERDICT_PASS, VERDICT_FLAG, VERDICT_MUST_RETRY}

# Rubric keys — fatal ones drive must_retry.
RUBRIC_FATAL = ("every_claim_cited", "citations_resolve", "no_external_facts")
RUBRIC_NONFATAL = ("actions_actionable", "confidence_calibrated")
RUBRIC_ALL = RUBRIC_FATAL + RUBRIC_NONFATAL


SYSTEM_PROMPT = """You are a verifier for a GRP / Acumatica ERP support Analyst.

You will receive:
  - analyst_output: the Analyst's draft (summary, likely_cause, claims,
    recommended_actions, confidence, ...)
  - retrieved_context: the same evidence chunks the Analyst saw
  - category: from the prior Classifier step

Run the 5-check rubric below. Output ONE JSON object only. No prose, no
markdown fences.

Rubric (booleans):
  1. every_claim_cited
       true iff every claim has citations.length >= 1 AND every action has
       citations.length >= 1.
  2. citations_resolve
       true iff every cited chunk_id appears as a chunk_id in
       retrieved_context. False if even one citation is invented.
  3. no_external_facts
       Sample up to 3 claims at random. For each, find a sentence in the
       cited chunk(s) that supports it. If you cannot, false. State of a
       single unsupported claim is enough to fail this rubric.
  4. actions_actionable
       true iff each recommended_action.detail describes a CONCRETE next
       step (a command, a UI path like "Settings > License", a config flag).
       Vague verbs ("investigate", "look into", "check things") fail this.
  5. confidence_calibrated
       If analyst_output.confidence >= 0.70, every claim's citations must
       include at least one manual chunk OR a ticket chunk with score >= 0.70.
       Otherwise true.

Verdict rules:
  - "must_retry" if ANY of (1, 2, 3) is false.
  - "flag" if (4) or (5) is false BUT (1, 2, 3) are all true.
  - "pass" otherwise.

Schema:
{
  "verdict": "pass" | "flag" | "must_retry",
  "rubric_scores": {
    "every_claim_cited": <bool>,
    "citations_resolve": <bool>,
    "no_external_facts": <bool>,
    "actions_actionable": <bool>,
    "confidence_calibrated": <bool>
  },
  "flags": [
    {
      "kind": "unsupported_claim" | "weak_citation" | "low_confidence" | "retrieval_gap",
      "detail": "<short explanation>"
    },
    ...
  ],
  "must_retry_reason": "<one sentence>" | null
}

Notes:
  - When verdict is "must_retry", must_retry_reason must be a non-empty string
    that names which rubric failed and which claim/action triggered it.
  - flags is optional in pass/must_retry; in flag, include at least one entry.
"""


# ── Output type ───────────────────────────────────────────────────────────────

@dataclass
class VerifierOutput:
    verdict: str
    rubric_scores: dict[str, bool] = field(default_factory=dict)
    flags: list[VerifierFlag] = field(default_factory=list)
    must_retry_reason: str | None = None


class VerifierValidationError(ValueError):
    pass


# ── Input compaction ──────────────────────────────────────────────────────────

def _compact_analyst(a: AnalystOutput) -> dict[str, Any]:
    return {
        "summary": a.summary[:400],
        "likely_cause": (a.likely_cause or "")[:500] or None,
        "confidence": a.confidence,
        "recommended_actions": [
            {"step": x.step,
             "detail": x.detail[:400],
             "citations": list(x.citations)}
            for x in a.recommended_actions
        ],
        "claims": [
            {"id": c.id, "text": c.text[:400],
             "supports_step": list(c.supports_step),
             "citations": list(c.citations)}
            for c in a.claims
        ],
        "open_questions": a.open_questions[:3],
    }


def _compact_chunks(chunks: Sequence[RetrievedChunk]) -> list[dict[str, Any]]:
    out = []
    for c in chunks:
        out.append({
            "chunk_id": c.chunk_id,
            "source": c.kind,
            "locator": c.locator,
            # Keep enough text to verify claims; cap to keep prompt tight.
            "text": (c.text or "")[:600],
            "score": c.score,
        })
    return out


# ── Output validation ─────────────────────────────────────────────────────────

def _parse_flag(raw: Any) -> VerifierFlag | None:
    if not isinstance(raw, dict):
        return None
    kind_str = str(raw.get("kind", "")).strip()
    try:
        kind = VerifierFlagKind(kind_str)
    except ValueError:
        return None
    detail = str(raw.get("detail") or "").strip()
    if not detail:
        return None
    return VerifierFlag(kind=kind, detail=detail[:400])


def _validate(parsed: dict[str, Any]) -> VerifierOutput:
    verdict = str(parsed.get("verdict", "")).strip()
    if verdict not in _VALID_VERDICTS:
        raise VerifierValidationError(f"bad verdict: {verdict!r}")

    raw_scores = parsed.get("rubric_scores") or {}
    if not isinstance(raw_scores, dict):
        raise VerifierValidationError("rubric_scores must be object")
    scores: dict[str, bool] = {}
    for k in RUBRIC_ALL:
        if k not in raw_scores:
            raise VerifierValidationError(f"missing rubric_scores.{k}")
        scores[k] = bool(raw_scores[k])

    fatal_fail = any(not scores[k] for k in RUBRIC_FATAL)
    nonfatal_fail = any(not scores[k] for k in RUBRIC_NONFATAL)

    # Cross-check verdict ↔ rubric consistency. If the LLM contradicts itself,
    # trust the rubric (more granular signal). Note the inconsistency but
    # don't fail outright.
    derived: str
    if fatal_fail:
        derived = VERDICT_MUST_RETRY
    elif nonfatal_fail:
        derived = VERDICT_FLAG
    else:
        derived = VERDICT_PASS
    if derived != verdict:
        log.warning('"verifier.verdict_rubric_mismatch claimed=%s derived=%s"',
                    verdict, derived)
        verdict = derived

    raw_flags = parsed.get("flags") or []
    if not isinstance(raw_flags, list):
        raw_flags = []
    flags: list[VerifierFlag] = []
    for f in raw_flags:
        parsed_flag = _parse_flag(f)
        if parsed_flag is not None:
            flags.append(parsed_flag)

    reason = parsed.get("must_retry_reason")
    if reason is not None:
        reason = str(reason).strip() or None
    if verdict == VERDICT_MUST_RETRY and not reason:
        # Derive a generic reason from the failing rubric checks.
        failing = [k for k in RUBRIC_FATAL if not scores[k]]
        reason = f"rubric failed: {', '.join(failing) or 'unknown'}"

    return VerifierOutput(verdict=verdict, rubric_scores=scores,
                          flags=flags, must_retry_reason=reason)


# ── Public entrypoint ─────────────────────────────────────────────────────────

def verify(
    analyst_output: AnalystOutput,
    chunks: Sequence[RetrievedChunk],
    category: str,
) -> tuple[VerifierOutput, AgentStep]:
    payload = {
        "category": category,
        "analyst_output": _compact_analyst(analyst_output),
        "retrieved_context": _compact_chunks(chunks),
    }

    try:
        res = llm.call_agent_json(
            model=MODEL, system_prompt=SYSTEM_PROMPT,
            user_payload=payload, max_tokens=MAX_TOKENS,
        )
    except llm.LLMParseError as e:
        log.warning('"verifier.unparseable err=%s"', e)
        return _soft_fail(reason=f"unparseable: {e}",
                          usage=None, duration_ms=0)

    parsed = res.parsed or {}
    try:
        out = _validate(parsed)
    except VerifierValidationError as e:
        log.warning('"verifier.schema_violation err=%s"', e)
        return _soft_fail(reason=f"schema_violation: {e}",
                          usage=res.usage, duration_ms=res.duration_ms)

    status_map = {
        VERDICT_PASS: AgentStepStatus.ok,
        VERDICT_FLAG: AgentStepStatus.ok,
        VERDICT_MUST_RETRY: AgentStepStatus.retried,
    }
    step = AgentStep(
        agent=AgentName.verifier, model=MODEL,
        status=status_map[out.verdict],
        duration_ms=res.duration_ms,
        input_tokens=res.usage.input_tokens,
        input_cache_read_tokens=res.usage.input_cache_read_tokens,
        output_tokens=res.usage.output_tokens,
        note=f"verdict={out.verdict}" + (
            f"; reason={out.must_retry_reason}"
            if out.verdict == VERDICT_MUST_RETRY else ""
        ),
    )
    return out, step


# ── Final review (post-Formatter) ─────────────────────────────────────────────

FINAL_REVIEW_PROMPT = """You are a verifier performing the FINAL review of a GRP / Acumatica ERP support Analysis.

You receive the FINAL Analysis object that will be returned to the user, plus
the retrieved evidence chunks. Output verifier flags that reference the
Analysis EXACTLY as given — its own step numbers and citation ids (cit-1, ...).

Output ONE JSON object only. No prose, no markdown fences.

Review every recommended_action and every citation:
  - weak_citation: an action's source_refs point at a citation whose score is
    low (below 0.5) OR whose snippet does not actually support the action.
  - unsupported_claim: an action has empty source_refs, or none of its cited
    snippets support what the step instructs.
  - retrieval_gap: the Analysis needs evidence on a topic that no chunk covers.
  - low_confidence: the stated confidence looks too high for the citation
    quality.

Rules:
  - Reference ONLY step numbers and cit-ids that appear in the given Analysis.
    Never mention a step or cit-id that is not present.
  - The Analysis has NO "claims" — it only has recommended_actions and
    citations. Never write "claim-N" / "Claim-N" or any other identifier.
    Refer to an action as "Step N" and a citation as "cit-N", nothing else.
  - Flag only real problems. A step well supported by a strong citation gets
    no flag. If everything checks out, return an empty flags array.

Schema:
{
  "flags": [
    { "kind": "unsupported_claim" | "weak_citation" | "low_confidence" | "retrieval_gap",
      "detail": "<short explanation citing step N and/or cit-N>" }
  ]
}
"""


def _compact_analysis(a: Analysis) -> dict[str, Any]:
    return {
        "category": a.category,
        "confidence": a.confidence,
        "summary": a.summary[:400],
        "recommended_actions": [
            {"step": x.step, "detail": x.detail[:400],
             "source_refs": list(x.source_refs)}
            for x in a.recommended_actions
        ],
        "citations": [
            {"id": c.id, "source": c.source.value,
             "snippet": c.snippet[:300], "score": c.score}
            for c in a.citations
        ],
    }


def verify_analysis(
    analysis: Analysis,
    chunks: Sequence[RetrievedChunk],
    category: str,
) -> tuple[list[VerifierFlag], AgentStep]:
    """Re-verify the FINAL Analysis (post-Formatter). Returns flags that
    reference the Analysis's own step numbers / cit-ids — unlike the
    pre-format Verifier, whose flags point at the Analyst draft's numbering.

    Flag-only: the pass/retry verdict was already settled before formatting.
    Soft-fails to an empty flag list — never blocks the pipeline.
    """
    payload = {
        "category": category,
        "analysis": _compact_analysis(analysis),
        "retrieved_context": _compact_chunks(chunks),
    }
    try:
        res = llm.call_agent_json(
            model=MODEL, system_prompt=FINAL_REVIEW_PROMPT,
            user_payload=payload, max_tokens=MAX_TOKENS,
        )
    except llm.LLMParseError as e:
        log.warning('"verifier.final_review_unparseable err=%s"', e)
        return [], AgentStep(
            agent=AgentName.verifier, model=MODEL,
            status=AgentStepStatus.failed, duration_ms=0,
            note=f"final_review soft_fail: {e}",
        )

    parsed = res.parsed or {}
    raw_flags = parsed.get("flags")
    flags: list[VerifierFlag] = []
    if isinstance(raw_flags, list):
        for f in raw_flags:
            pf = _parse_flag(f)
            if pf is not None:
                flags.append(pf)
    step = AgentStep(
        agent=AgentName.verifier, model=MODEL,
        status=AgentStepStatus.ok, duration_ms=res.duration_ms,
        input_tokens=res.usage.input_tokens,
        input_cache_read_tokens=res.usage.input_cache_read_tokens,
        output_tokens=res.usage.output_tokens,
        note=f"final_review flags={len(flags)}",
    )
    return flags, step


def _soft_fail(*, reason: str, usage: "llm.LLMUsage | None",
               duration_ms: int) -> tuple[VerifierOutput, AgentStep]:
    """Verifier failures → flag verdict with low_confidence, never block."""
    out = VerifierOutput(
        verdict=VERDICT_FLAG,
        rubric_scores={k: False for k in RUBRIC_ALL},
        flags=[VerifierFlag(
            kind=VerifierFlagKind.low_confidence,
            detail=f"Verifier error: {reason}",
        )],
        must_retry_reason=None,
    )
    u = usage or llm.LLMUsage()
    step = AgentStep(
        agent=AgentName.verifier, model=MODEL,
        status=AgentStepStatus.failed,
        duration_ms=duration_ms,
        input_tokens=u.input_tokens,
        input_cache_read_tokens=u.input_cache_read_tokens,
        output_tokens=u.output_tokens,
        note=f"soft_fail: {reason}",
    )
    return out, step
