"""Agent 1 — Classifier.

Contract: rag-api/contracts/01-classifier.md

Inputs:  RFS
Outputs: ClassifierOutput { category, confidence, tags, language }
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

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
  "tags": ["<freeform>", "..."],
  "language": "<en | ms | zh | other>"
}

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
    tags: list[str] = field(default_factory=list)
    language: str = "en"


# ── Validation ─────────────────────────────────────────────────────────────────

def _validate(parsed: dict[str, Any]) -> ClassifierOutput:
    cat = str(parsed.get("category", "")).strip()
    if cat not in CATEGORIES:
        raise ValueError(f"category not in enum: {cat!r}")
    conf = float(parsed.get("confidence", 0.0))
    if not (0.0 <= conf <= 1.0):
        raise ValueError(f"confidence out of range: {conf}")
    return ClassifierOutput(
        category=cat,
        confidence=conf,
        tags=list(parsed.get("tags") or []),
        language=str(parsed.get("language") or "en"),
    )


# ── Public entrypoint ──────────────────────────────────────────────────────────

def classify(rfs: RFS) -> tuple[ClassifierOutput, AgentStep]:
    payload = {
        "rfs": {
            "lodge_id": rfs.lodge_id,
            "relatedarea": rfs.relatedarea,
            "notes": rfs.notes,
            "priority": rfs.priority,
        },
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
                                language="en"), step

    step = AgentStep(
        agent=AgentName.classifier, model=MODEL, status=AgentStepStatus.ok,
        duration_ms=res.duration_ms,
        input_tokens=res.usage.input_tokens,
        input_cache_read_tokens=res.usage.input_cache_read_tokens,
        output_tokens=res.usage.output_tokens,
    )
    return out, step
