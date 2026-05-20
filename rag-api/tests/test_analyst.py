"""Analyst agent — validation, retry, citation-resolution, Opus retry."""
from __future__ import annotations

import json

import pytest

from app import llm
from app.agents import analyst
from app.agents.analyst import (
    AnalystOutput,
    AnalystValidationError,
    MODEL_DEFAULT,
    MODEL_RETRY,
    _validate,
)
from app.agents.classifier import ClassifierOutput
from app.llm import LLMResult, LLMUsage
from app.models import RFS, AgentStepStatus
from app.retrieval import RetrievedChunk


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _rfs():
    return RFS(lodge_id="LDG-1",
               notes="Cannot generate report after license renewal",
               relatedarea="Licensing")


def _classifier():
    return ClassifierOutput(category="license-error", confidence=0.8,
                            tags=[], language="en")


def _chunks():
    return [
        RetrievedChunk(
            chunk_id="grp-manuals::m1", index="grp-manuals", kind="manual",
            locator={"module": "Admin", "section": "License"},
            text="After renewal call POST /license/refresh.", score=0.9,
        ),
        RetrievedChunk(
            chunk_id="rfs-tickets-mar-2025::t1",
            index="rfs-tickets-mar-2025", kind="rfs_ticket",
            locator={"lodge_id": "LDG-90211"},
            text="License stuck after renewal — fixed by restart.", score=0.7,
        ),
    ]


def _good_parsed():
    return {
        "summary": "User cannot generate reports because license cache is stale.",
        "likely_cause": "License cache not invalidated after renewal.",
        "recommended_actions": [
            {"step": 1, "detail": "Call POST /license/refresh.",
             "citations": ["grp-manuals::m1"]},
            {"step": 2, "detail": "If step 1 fails, restart service.",
             "citations": ["grp-manuals::m1", "rfs-tickets-mar-2025::t1"]},
        ],
        "claims": [
            {"id": "claim-1",
             "text": "License refresh endpoint exists in the manual.",
             "supports_step": [1], "citations": ["grp-manuals::m1"]},
            {"id": "claim-2",
             "text": "LDG-90211 was resolved by service restart.",
             "supports_step": [2], "citations": ["rfs-tickets-mar-2025::t1"]},
        ],
        "confidence": 0.78,
        "open_questions": [],
    }


def _llm_result(parsed, *, input_tokens=8000, cache_read=4500, output_tokens=1500):
    return LLMResult(
        text=json.dumps(parsed),
        parsed=parsed,
        usage=LLMUsage(input_tokens=input_tokens,
                       input_cache_read_tokens=cache_read,
                       output_tokens=output_tokens),
        duration_ms=1800,
        stop_reason="end_turn",
    )


# ── _validate unit tests ──────────────────────────────────────────────────────

RETRIEVED_IDS = {"grp-manuals::m1", "rfs-tickets-mar-2025::t1"}


def test_validate_happy():
    out = _validate(_good_parsed(), RETRIEVED_IDS)
    assert isinstance(out, AnalystOutput)
    assert len(out.recommended_actions) == 2
    assert len(out.claims) == 2
    assert out.confidence == 0.78


def test_validate_rejects_action_without_citation():
    bad = _good_parsed()
    bad["recommended_actions"][0]["citations"] = []
    with pytest.raises(AnalystValidationError, match="no citations"):
        _validate(bad, RETRIEVED_IDS)


def test_validate_rejects_claim_without_citation():
    bad = _good_parsed()
    bad["claims"][0]["citations"] = []
    with pytest.raises(AnalystValidationError, match="no citations"):
        _validate(bad, RETRIEVED_IDS)


def test_validate_rejects_unknown_chunk_id():
    bad = _good_parsed()
    bad["recommended_actions"][0]["citations"] = ["fake::1"]
    with pytest.raises(AnalystValidationError, match="unknown chunk_id"):
        _validate(bad, RETRIEVED_IDS)


def test_validate_rejects_unknown_chunk_in_claim():
    bad = _good_parsed()
    bad["claims"][0]["citations"] = ["never-saw-this"]
    with pytest.raises(AnalystValidationError, match="unknown chunk_id"):
        _validate(bad, RETRIEVED_IDS)


def test_validate_rejects_bad_confidence():
    bad = _good_parsed()
    bad["confidence"] = 1.5
    with pytest.raises(AnalystValidationError, match="confidence"):
        _validate(bad, RETRIEVED_IDS)


def test_validate_rejects_empty_actions():
    bad = _good_parsed()
    bad["recommended_actions"] = []
    with pytest.raises(AnalystValidationError, match="non-empty"):
        _validate(bad, RETRIEVED_IDS)


def test_validate_allows_empty_citations_only_when_no_retrieval():
    """If retrieval was empty, the Analyst is allowed to admit it can't
    cite anything in actions (still must have actions)."""
    bad = _good_parsed()
    bad["recommended_actions"][0]["citations"] = []
    bad["recommended_actions"][1]["citations"] = []
    bad["claims"] = []  # claims always require citations, so drop them
    out = _validate(bad, set(), allow_empty_citations=True)
    assert out.recommended_actions[0].citations == []


# ── analyze() integration tests ───────────────────────────────────────────────

def test_analyze_happy_path(monkeypatch):
    monkeypatch.setattr(analyst.llm, "call_agent_json",
                        lambda **kw: _llm_result(_good_parsed()))
    out, step = analyst.analyze(_rfs(), _classifier(), _chunks())
    assert step.status == AgentStepStatus.ok
    assert step.model == MODEL_DEFAULT
    assert step.input_tokens == 8000
    assert out.confidence == 0.78
    assert len(out.recommended_actions) == 2


def test_analyze_retries_on_citation_violation(monkeypatch):
    bad = _good_parsed()
    bad["recommended_actions"][0]["citations"] = []
    good = _good_parsed()
    calls = {"n": 0}
    def fake(**kw):
        calls["n"] += 1
        return _llm_result(bad if calls["n"] == 1 else good)
    monkeypatch.setattr(analyst.llm, "call_agent_json", fake)

    out, step = analyst.analyze(_rfs(), _classifier(), _chunks())
    assert step.status == AgentStepStatus.retried
    assert "retried after" in (step.note or "")
    assert calls["n"] == 2
    # Usage aggregated across both attempts.
    assert step.input_tokens == 16000


def test_analyze_fallback_when_both_attempts_fail(monkeypatch):
    bad = _good_parsed()
    bad["recommended_actions"][0]["citations"] = []
    monkeypatch.setattr(analyst.llm, "call_agent_json",
                        lambda **kw: _llm_result(bad))
    out, step = analyst.analyze(_rfs(), _classifier(), _chunks())
    assert step.status == AgentStepStatus.failed
    assert "unsupported" in (step.note or "")
    assert out.unsupported_flag is not None
    # Still emits at least one action so downstream Formatter has something
    # to map to public recommended_actions.
    assert len(out.recommended_actions) >= 1
    assert out.confidence == 0.0


def test_analyze_fallback_on_unparseable(monkeypatch):
    monkeypatch.setattr(analyst.llm, "call_agent_json",
                        lambda **kw: (_ for _ in ()).throw(
                            llm.LLMParseError("garbage")))
    out, step = analyst.analyze(_rfs(), _classifier(), _chunks())
    assert step.status == AgentStepStatus.failed
    assert "unparseable" in (step.note or "")


def test_analyze_low_confidence_flagged_in_note(monkeypatch):
    parsed = _good_parsed()
    parsed["confidence"] = 0.1
    monkeypatch.setattr(analyst.llm, "call_agent_json",
                        lambda **kw: _llm_result(parsed))
    out, step = analyst.analyze(_rfs(), _classifier(), _chunks())
    assert step.status == AgentStepStatus.ok
    assert "low_confidence" in (step.note or "")


def test_analyze_empty_retrieval_allows_empty_citations(monkeypatch):
    parsed = {
        "summary": "No retrieval results — cannot ground analysis.",
        "likely_cause": None,
        "recommended_actions": [
            {"step": 1, "detail": "Gather more context: full error log + screenshot.",
             "citations": []},
        ],
        "claims": [],
        "confidence": 0.0,
        "open_questions": ["What's the exact error?"],
    }
    monkeypatch.setattr(analyst.llm, "call_agent_json",
                        lambda **kw: _llm_result(parsed))
    out, step = analyst.analyze(_rfs(), _classifier(), [])
    assert step.status == AgentStepStatus.ok
    assert out.recommended_actions[0].citations == []


def test_retry_with_opus_uses_opus_model(monkeypatch):
    seen = {}
    def fake(**kw):
        seen["model"] = kw["model"]
        seen["system"] = kw["system_prompt"]
        return _llm_result(_good_parsed())
    monkeypatch.setattr(analyst.llm, "call_agent_json", fake)
    out, step = analyst.retry_with_opus(
        _rfs(), _classifier(), _chunks(),
        verifier_reason="claim claim-1 lacks supporting evidence",
    )
    assert step.model == MODEL_RETRY
    assert seen["model"] == MODEL_RETRY
    assert "Verifier" in seen["system"]


def test_extra_system_suffix_appended(monkeypatch):
    seen = {}
    def fake(**kw):
        seen["system"] = kw["system_prompt"]
        return _llm_result(_good_parsed())
    monkeypatch.setattr(analyst.llm, "call_agent_json", fake)
    analyst.analyze(_rfs(), _classifier(), _chunks(),
                    extra_system_suffix="EXTRA INSTRUCTIONS HERE")
    assert "EXTRA INSTRUCTIONS HERE" in seen["system"]
