"""Retrieval Planner agent — happy path, retry-on-invalid, baseline fallback."""
from __future__ import annotations

import json

import pytest

from app import llm
from app.agents import classifier, planner
from app.agents.classifier import ClassifierOutput
from app.agents.planner import PlannerOutput
from app.llm import LLMResult, LLMUsage
from app.models import RFS, AgentStepStatus


def _rfs():
    return RFS(lodge_id="LDG-1",
               notes="Cannot generate report after license renewal",
               relatedarea="Licensing")


def _classifier_output(category="license-error"):
    return ClassifierOutput(category=category, confidence=0.8,
                            short_circuit=False,
                            short_circuit_reason=None,
                            short_circuit_payload=None,
                            tags=[], language="en")


def _llm_returning(obj: dict, *, input_tokens=200, output_tokens=120):
    return LLMResult(
        text=json.dumps(obj),
        parsed=obj,
        usage=LLMUsage(input_tokens=input_tokens, output_tokens=output_tokens),
        duration_ms=15,
        stop_reason="end_turn",
    )


VALID_LLM_PLAN = {
    "queries": [
        {"index": "grp-manuals", "mode": "hybrid",
         "knn": {"field": "embedding", "k": 4, "num_candidates": 60},
         "lexical": {"must": [{"match": {"content": "license renewal"}}]},
         "rerank": {"strategy": "mmr", "lambda": 0.6}, "max_chunks": 4},
        {"index": "rfs-tickets-*", "mode": "knn",
         "knn": {"field": "embedding", "k": 4, "num_candidates": 60},
         "rerank": {"strategy": "score_only"}, "max_chunks": 4},
    ],
    "rationale": "Look up renewal procedure + past similar tickets.",
}


def test_planner_happy_path(monkeypatch):
    monkeypatch.setattr(planner.llm, "call_agent_json",
                        lambda **kw: _llm_returning(VALID_LLM_PLAN))
    out, step = planner.plan(_rfs(), _classifier_output())
    assert isinstance(out, PlannerOutput)
    assert len(out.queries) == 2
    assert step.status == AgentStepStatus.ok
    assert step.input_tokens == 200


def test_planner_retries_on_invalid_then_succeeds(monkeypatch):
    calls = {"n": 0}
    invalid = {"queries": [VALID_LLM_PLAN["queries"][0]],  # missing ticket query
               "rationale": "bad"}
    def fake(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return _llm_returning(invalid)
        return _llm_returning(VALID_LLM_PLAN)
    monkeypatch.setattr(planner.llm, "call_agent_json", fake)
    out, step = planner.plan(_rfs(), _classifier_output())
    assert step.status == AgentStepStatus.retried
    assert "retried" in (step.note or "")
    assert calls["n"] == 2
    # Tokens aggregated.
    assert step.input_tokens == 400


def test_planner_falls_back_to_baseline_when_both_invalid(monkeypatch):
    invalid = {"queries": [], "rationale": "no good"}
    monkeypatch.setattr(planner.llm, "call_agent_json",
                        lambda **kw: _llm_returning(invalid))
    out, step = planner.plan(_rfs(), _classifier_output())
    assert step.status == AgentStepStatus.failed
    assert "baseline" in (step.note or "")
    # Baseline must itself be valid.
    from app import retrieval
    retrieval.validate_plan(out.queries)
    # Baseline includes both required coverages.
    assert any(q["index"] == "grp-manuals" for q in out.queries)
    assert any(q["index"].startswith("rfs-tickets") for q in out.queries)


def test_planner_falls_back_when_unparseable(monkeypatch):
    def boom(**kw):
        raise llm.LLMParseError("not json")
    monkeypatch.setattr(planner.llm, "call_agent_json", boom)
    out, step = planner.plan(_rfs(), _classifier_output())
    assert step.status == AgentStepStatus.failed
    assert "unparseable" in (step.note or "")
    # Still produces a usable plan.
    from app import retrieval
    retrieval.validate_plan(out.queries)


def test_planner_passes_classifier_into_payload(monkeypatch):
    captured = {}
    def fake(**kw):
        captured["payload"] = kw["user_payload"]
        return _llm_returning(VALID_LLM_PLAN)
    monkeypatch.setattr(planner.llm, "call_agent_json", fake)
    planner.plan(_rfs(), _classifier_output(category="how-to"))
    assert captured["payload"]["classifier_output"]["category"] == "how-to"
    # Notes truncated to keep the prompt cheap.
    assert len(captured["payload"]["rfs"]["notes"]) <= 1200


def test_baseline_plan_is_valid():
    from app import retrieval
    base = planner.baseline_plan(_rfs())
    retrieval.validate_plan(base.queries)
