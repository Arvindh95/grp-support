"""Agent 1 — Classifier.

Contract: rag-api/contracts/01-classifier.md

Inputs:  RFS + optional duplicate_candidates
Outputs: ClassifierOutput { category, confidence, short_circuit, ... }
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from .. import llm
from ..models import AgentName, AgentStep, AgentStepStatus, RFS

log = logging.getLogger("rag-api.agent.classifier")

MODEL = "claude-haiku-4-5"
MAX_TOKENS = 400


CATEGORIES = (
    "license-error", "data-import", "report-generation", "permissions",
    "integration-failure", "performance", "ui-bug", "data-corruption",
    "feature-request", "how-to", "duplicate", "other",
)


SYSTEM_PROMPT = """You are a support-ticket classifier for the GRP / Acumatica ERP product.

Output ONE JSON object only. No prose, no markdown, no code fences.

Schema:
{
  "category": "<one of: license-error, data-import, report-generation, permissions, integration-failure, performance, ui-bug, data-corruption, feature-request, how-to, duplicate, other>",
  "confidence": 0.0-1.0,
  "short_circuit": true|false,
  "short_circuit_reason": "<short string>" | null,
  "short_circuit_payload": { /* optional, see rules */ } | null,
  "tags": ["<freeform>", "..."],
  "language": "<en | ms | zh | other>"
}

Short-circuit rules — set short_circuit=true ONLY if one matches:

1. DUPLICATE: duplicate_candidates[0].score >= 0.88 AND the candidate's
   snippet is semantically near-identical to the new RFS notes.
   - category = "duplicate"
   - short_circuit_reason = "near_duplicate_of_<lodge_id>"
   - short_circuit_payload = {
       "duplicate_of": "<lodge_id>",
       "suggested_response": "This is a duplicate of <lodge_id>. ..."
     }

2. TRIVIAL HOW-TO: notes length < 200 chars AND the question is a basic
   look-up answerable in one sentence (e.g. "where do I find X?").
   - category = "how-to"
   - short_circuit_reason = "trivial_how_to"
   - short_circuit_payload = {
       "suggested_response": "<one-sentence answer>"
     }

3. SPAM / EMPTY: notes contain no actionable question.
   - category = "other"
   - short_circuit_reason = "no_actionable_content"
   - short_circuit_payload = { "reason": "empty or spam" }

Otherwise short_circuit=false and the payload/reason are null.

Calibrate confidence:
  >=0.85 = strong signal (the notes literally name the symptom area).
  0.5-0.85 = best guess.
  <0.5 = unsure — still pick the most likely category, do not abstain.

Do not invent facts. Do not include explanations outside the JSON.
"""


# ── Output type ────────────────────────────────────────────────────────────────

@dataclass
class ClassifierOutput:
    category: str
    confidence: float
    short_circuit: bool
    short_circuit_reason: str | None
    short_circuit_payload: dict[str, Any] | None
    tags: list[str] = field(default_factory=list)
    language: str = "en"


@dataclass
class DuplicateCandidate:
    lodge_id: str
    score: float
    snippet: str


# ── Validation ─────────────────────────────────────────────────────────────────

def _validate(parsed: dict[str, Any]) -> ClassifierOutput:
    cat = str(parsed.get("category", "")).strip()
    if cat not in CATEGORIES:
        raise ValueError(f"category not in enum: {cat!r}")
    conf = float(parsed.get("confidence", 0.0))
    if not (0.0 <= conf <= 1.0):
        raise ValueError(f"confidence out of range: {conf}")
    sc = bool(parsed.get("short_circuit", False))
    sc_reason = parsed.get("short_circuit_reason")
    sc_payload = parsed.get("short_circuit_payload")
    if sc and not sc_reason:
        raise ValueError("short_circuit=true requires short_circuit_reason")
    if not sc and (sc_reason or sc_payload):
        raise ValueError("short_circuit=false but reason/payload populated")
    return ClassifierOutput(
        category=cat,
        confidence=conf,
        short_circuit=sc,
        short_circuit_reason=sc_reason,
        short_circuit_payload=sc_payload,
        tags=list(parsed.get("tags") or []),
        language=str(parsed.get("language") or "en"),
    )


# ── Public entrypoint ──────────────────────────────────────────────────────────

def classify(
    rfs: RFS,
    duplicate_candidates: list[DuplicateCandidate] | None = None,
) -> tuple[ClassifierOutput, AgentStep]:
    payload = {
        "rfs": {
            "lodge_id": rfs.lodge_id,
            "relatedarea": rfs.relatedarea,
            "notes": rfs.notes,
            "priority": rfs.priority,
        },
        "duplicate_candidates": [
            {"lodge_id": c.lodge_id, "score": c.score, "snippet": c.snippet}
            for c in (duplicate_candidates or [])
        ],
    }

    try:
        res = llm.call_agent_json(
            model=MODEL,
            system_prompt=SYSTEM_PROMPT,
            user_payload=payload,
            max_tokens=MAX_TOKENS,
        )
    except llm.LLMParseError as e:
        log.warning('"classifier.unparseable err=%s"', e)
        # Treat as a soft failure — return "other" with low confidence so
        # the pipeline can still proceed. The trace will record `failed`.
        step = AgentStep(
            agent=AgentName.classifier, model=MODEL,
            status=AgentStepStatus.failed, duration_ms=0,
            note=f"unparseable: {e}",
        )
        return ClassifierOutput(category="other", confidence=0.0,
                                short_circuit=False,
                                short_circuit_reason=None,
                                short_circuit_payload=None,
                                language="en"), step

    parsed = res.parsed or {}
    try:
        out = _validate(parsed)
    except ValueError as e:
        log.warning('"classifier.schema_violation err=%s parsed=%s"', e,
                    json.dumps(parsed)[:200])
        step = AgentStep(
            agent=AgentName.classifier, model=MODEL,
            status=AgentStepStatus.failed,
            duration_ms=res.duration_ms,
            input_tokens=res.usage.input_tokens,
            input_cache_read_tokens=res.usage.input_cache_read_tokens,
            output_tokens=res.usage.output_tokens,
            note=f"schema_violation: {e}",
        )
        return ClassifierOutput(category="other", confidence=0.0,
                                short_circuit=False,
                                short_circuit_reason=None,
                                short_circuit_payload=None,
                                language="en"), step

    status = (AgentStepStatus.short_circuit if out.short_circuit
              else AgentStepStatus.ok)
    step = AgentStep(
        agent=AgentName.classifier, model=MODEL, status=status,
        duration_ms=res.duration_ms,
        input_tokens=res.usage.input_tokens,
        input_cache_read_tokens=res.usage.input_cache_read_tokens,
        output_tokens=res.usage.output_tokens,
        note=out.short_circuit_reason if out.short_circuit else None,
    )
    return out, step
