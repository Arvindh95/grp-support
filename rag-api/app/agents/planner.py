"""Agent 2 — Retrieval Planner.

Contract: rag-api/contracts/02-retrieval-planner.md

Inputs:  RFS + ClassifierOutput + available indices.
Output:  PlannerOutput { queries: [...], rationale: "..." }

If the LLM returns an invalid plan (sum max_chunks > 12, missing manual or
ticket coverage, bad mode/index), we retry ONCE with the validator error
appended. After that, we fall back to a deterministic baseline plan so the
pipeline can still serve traffic — flagged on the AgentStep.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from .. import llm, retrieval
from ..models import AgentName, AgentStep, AgentStepStatus, RFS
from .classifier import ClassifierOutput

log = logging.getLogger("rag-api.agent.planner")

MODEL = "claude-haiku-4-5"
MAX_TOKENS = 800


SYSTEM_PROMPT = """You are a retrieval-planner. Build an Elasticsearch query plan that
surfaces the most useful evidence for an Analyst agent to answer the user's RFS.

Output ONE JSON object only. No prose, no markdown fences.

Schema:
{
  "queries": [
    {
      "index": "<one of the available_indices[].name>",
      "mode": "hybrid" | "knn" | "lexical",
      "knn": {
        "field": "embedding",
        "k": <integer>,
        "num_candidates": <integer>
      } | null,
      "lexical": {
        "must":      [<ES clause>, ...],
        "filter":    [<ES clause>, ...],
        "should":    [<ES clause>, ...],
        "must_not":  [<ES clause>, ...]
      } | null,
      "rerank": { "strategy": "mmr" | "score_only", "lambda": <0..1, optional> },
      "max_chunks": <integer>
    },
    ...
  ],
  "rationale": "<one-paragraph justification>"
}

HARD RULES:
  - sum(queries[].max_chunks) <= 12.
  - At least one query against "grp-manuals".
  - At least one query against an "rfs-tickets-*" pattern (e.g. "rfs-tickets-mar-2025"
    or the wildcard "rfs-tickets-*").
  - mode must be one of: hybrid | knn | lexical.
  - For hybrid mode supply BOTH knn and lexical. For knn supply only knn.
    For lexical supply only lexical.
  - Default rerank.strategy is "mmr" for manuals, "score_only" for tickets.
  - The Planner does NOT execute queries. It only emits the plan.

Use the Classifier output to bias the plan:
  - category="how-to" → prefer manuals, set max_chunks of tickets lower.
  - category="duplicate" → include similar tickets so the Analyst can compare.
  - category="performance" or "integration-failure" → include grp-code if available.

Keep `lexical.must` query strings concise (a few keywords). Avoid copy-pasting
the user's full RFS notes.
"""


# ── Output types ───────────────────────────────────────────────────────────────

@dataclass
class PlannerOutput:
    queries: list[dict[str, Any]]
    rationale: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {"queries": self.queries, "rationale": self.rationale}


# ── Baseline fallback ──────────────────────────────────────────────────────────

def baseline_plan(rfs: RFS) -> PlannerOutput:
    """Deterministic safe plan when the LLM repeatedly produces invalid output.

    Manual+ticket hybrid kNN with no lexical filter — always retrieves something.
    """
    query_text = (rfs.relatedarea or rfs.notes)[:200]
    return PlannerOutput(
        queries=[
            {
                "index": "grp-manuals",
                "mode": "hybrid",
                "knn": {"field": "embedding", "k": 6, "num_candidates": 100},
                "lexical": {
                    "must": [{"multi_match": {
                        "query": query_text,
                        "fields": ["section^3", "module^2", "content"],
                    }}],
                },
                "rerank": {"strategy": "mmr", "lambda": 0.6},
                "max_chunks": 5,
            },
            {
                "index": "rfs-tickets-*",
                "mode": "knn",
                "knn": {"field": "embedding", "k": 6, "num_candidates": 100},
                "rerank": {"strategy": "score_only"},
                "max_chunks": 4,
            },
        ],
        rationale="baseline plan (Planner LLM failed validation twice)",
    )


# ── Public entrypoint ──────────────────────────────────────────────────────────

def plan(rfs: RFS, classifier_output: ClassifierOutput,
         ) -> tuple[PlannerOutput, AgentStep]:
    payload = {
        "rfs": {
            "lodge_id": rfs.lodge_id,
            "notes": rfs.notes[:1200],   # planner doesn't need the full notes
            "relatedarea": rfs.relatedarea,
            "probareaid": rfs.probareaid,
            "probtypeid": rfs.probtypeid,
        },
        "classifier_output": {
            "category": classifier_output.category,
            "confidence": classifier_output.confidence,
            "tags": classifier_output.tags,
        },
        "available_indices": retrieval.available_indices_payload(),
    }

    def _attempt(extra_suffix: str | None = None) -> llm.LLMResult:
        prompt = SYSTEM_PROMPT
        if extra_suffix:
            prompt = SYSTEM_PROMPT + "\n\n" + extra_suffix
        return llm.call_agent_json(
            model=MODEL,
            system_prompt=prompt,
            user_payload=payload,
            max_tokens=MAX_TOKENS,
        )

    # First attempt
    try:
        res = _attempt()
    except llm.LLMParseError as e:
        return _fail_to_baseline(rfs, reason=f"unparseable: {e}",
                                 usage=None, duration_ms=0)

    parsed = res.parsed or {}
    queries = parsed.get("queries") or []
    rationale = parsed.get("rationale", "")

    first_err: Exception | None = None
    try:
        retrieval.validate_plan(queries)
        out = PlannerOutput(queries=queries, rationale=rationale)
        return out, _step(AgentStepStatus.ok, res.duration_ms, res.usage)
    except (ValueError, TypeError) as e:
        first_err = e
        log.warning('"planner.invalid attempt=1 err=%s plan=%s"', e,
                    json.dumps(queries)[:200])

    # Retry with the validator's complaint included.
    suffix = (
        "Your previous plan failed validation: " + str(first_err) +
        ". Fix it and return a single valid JSON object."
    )
    try:
        res2 = _attempt(suffix)
    except llm.LLMParseError as e2:
        return _fail_to_baseline(rfs, reason=f"retry unparseable: {e2}",
                                 usage=res.usage, duration_ms=res.duration_ms)
    parsed2 = res2.parsed or {}
    queries2 = parsed2.get("queries") or []
    rationale2 = parsed2.get("rationale", rationale)
    combined_usage = llm.LLMUsage(
        input_tokens=res.usage.input_tokens + res2.usage.input_tokens,
        input_cache_read_tokens=res.usage.input_cache_read_tokens
            + res2.usage.input_cache_read_tokens,
        input_cache_write_tokens=res.usage.input_cache_write_tokens
            + res2.usage.input_cache_write_tokens,
        output_tokens=res.usage.output_tokens + res2.usage.output_tokens,
    )
    total_ms = res.duration_ms + res2.duration_ms

    try:
        retrieval.validate_plan(queries2)
        out = PlannerOutput(queries=queries2, rationale=rationale2)
        step = _step(AgentStepStatus.retried, total_ms, combined_usage,
                     note="retried after plan_invalid")
        return out, step
    except (ValueError, TypeError) as e2:
        log.warning('"planner.invalid attempt=2 err=%s"', e2)
        return _fail_to_baseline(rfs, reason=f"validation_failed: {e2}",
                                 usage=combined_usage, duration_ms=total_ms)


def _step(status: AgentStepStatus, duration_ms: int,
          usage: llm.LLMUsage | None, note: str | None = None) -> AgentStep:
    u = usage or llm.LLMUsage()
    return AgentStep(
        agent=AgentName.retrieval_planner, model=MODEL, status=status,
        duration_ms=duration_ms,
        input_tokens=u.input_tokens,
        input_cache_read_tokens=u.input_cache_read_tokens,
        output_tokens=u.output_tokens,
        note=note,
    )


def _fail_to_baseline(rfs: RFS, *, reason: str,
                      usage: llm.LLMUsage | None,
                      duration_ms: int) -> tuple[PlannerOutput, AgentStep]:
    log.warning('"planner.fallback_baseline reason=%s"', reason)
    return baseline_plan(rfs), _step(AgentStepStatus.failed, duration_ms,
                                     usage, note=f"baseline: {reason}")
