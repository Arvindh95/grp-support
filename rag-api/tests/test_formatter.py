"""LLM Formatter — schema validation, cross-check, retry, failure."""
from __future__ import annotations

import json

import pytest

from app import llm
from app.agents import formatter
from app.agents.analyst import AnalystAction, AnalystClaim, AnalystOutput
from app.agents.classifier import ClassifierOutput
from app.agents.formatter import FormatterError, _validate
from app.agents.verifier import VERDICT_PASS, VerifierOutput
from app.llm import LLMResult, LLMUsage
from app.models import AgentStepStatus, VerifierFlag, VerifierFlagKind
from app.retrieval import RetrievedChunk


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _classifier():
    return ClassifierOutput(category="license-error", confidence=0.85,
                            short_circuit=False,
                            short_circuit_reason=None,
                            short_circuit_payload=None,
                            tags=["billing"], language="en")


def _analyst():
    return AnalystOutput(
        summary="License cache stale.", likely_cause="cache not invalidated",
        recommended_actions=[
            AnalystAction(step=1, detail="Call POST /license/refresh.",
                          citations=["grp-manuals::m1"]),
        ],
        claims=[
            AnalystClaim(id="claim-1", text="manual documents endpoint",
                         supports_step=[1], citations=["grp-manuals::m1"])
        ],
        confidence=0.78, open_questions=[],
    )


def _verifier(flags=None):
    return VerifierOutput(verdict=VERDICT_PASS,
                          rubric_scores={"every_claim_cited": True,
                                         "citations_resolve": True,
                                         "no_external_facts": True,
                                         "actions_actionable": True,
                                         "confidence_calibrated": True},
                          flags=flags or [], must_retry_reason=None)


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
            text="Resolved by service restart.", score=0.7,
        ),
    ]


def _good_analysis_json():
    return {
        "category": "license-error",
        "confidence": 0.78,
        "summary": "License cache stale.",
        "likely_cause": "cache not invalidated",
        "recommended_actions": [
            {"step": 1, "detail": "Call POST /license/refresh.",
             "source_refs": ["cit-1"]},
        ],
        "citations": [
            {"id": "cit-1", "source": "manual",
             "locator": {"module": "Admin", "section": "License"},
             "snippet": "After renewal call POST /license/refresh.",
             "score": 0.9},
        ],
        "related_rfs": [
            {"lodge_id": "LDG-90211", "score": 0.7,
             "snippet": "Resolved by service restart."},
        ],
        "verifier_flags": [],
    }


def _llm_result(parsed, *, input_tokens=600, output_tokens=900):
    return LLMResult(
        text=json.dumps(parsed), parsed=parsed,
        usage=LLMUsage(input_tokens=input_tokens, output_tokens=output_tokens),
        duration_ms=180, stop_reason="end_turn",
    )


# ── _validate unit tests ──────────────────────────────────────────────────────

def test_validate_accepts_good_json():
    a = _validate(_good_analysis_json())
    assert a.category == "license-error"
    assert a.recommended_actions[0].source_refs == ["cit-1"]


def test_validate_rejects_orphan_source_ref():
    bad = _good_analysis_json()
    bad["recommended_actions"][0]["source_refs"] = ["cit-99"]
    with pytest.raises(FormatterError, match="not in citations"):
        _validate(bad)


def test_validate_rejects_schema_violation():
    bad = _good_analysis_json()
    bad["citations"][0]["snippet"] = "x" * 500  # >400 char snippet
    with pytest.raises(FormatterError, match="schema"):
        _validate(bad)


def test_validate_rejects_missing_action():
    bad = _good_analysis_json()
    bad["recommended_actions"] = []
    with pytest.raises(FormatterError, match="schema"):
        _validate(bad)


def test_validate_rejects_bad_confidence():
    bad = _good_analysis_json()
    bad["confidence"] = 1.5
    with pytest.raises(FormatterError, match="schema"):
        _validate(bad)


# ── format_via_llm integration ────────────────────────────────────────────────

def test_format_via_llm_happy(monkeypatch):
    monkeypatch.setattr(formatter.llm, "call_agent_json",
                        lambda **kw: _llm_result(_good_analysis_json()))
    a, step = formatter.format_via_llm(_classifier(), _analyst(),
                                       _verifier(), _chunks())
    assert a.category == "license-error"
    assert step.status == AgentStepStatus.ok
    assert step.input_tokens == 600


def test_format_via_llm_retries_on_invalid(monkeypatch):
    calls = {"n": 0}
    bad = _good_analysis_json()
    bad["recommended_actions"][0]["source_refs"] = ["cit-99"]   # orphan
    def fake(**kw):
        calls["n"] += 1
        return _llm_result(bad if calls["n"] == 1 else _good_analysis_json())
    monkeypatch.setattr(formatter.llm, "call_agent_json", fake)
    a, step = formatter.format_via_llm(_classifier(), _analyst(),
                                       _verifier(), _chunks())
    assert step.status == AgentStepStatus.retried
    assert calls["n"] == 2
    assert step.input_tokens == 1200


def test_format_via_llm_raises_after_two_failures(monkeypatch):
    bad = _good_analysis_json()
    bad["recommended_actions"][0]["source_refs"] = ["cit-99"]
    monkeypatch.setattr(formatter.llm, "call_agent_json",
                        lambda **kw: _llm_result(bad))
    with pytest.raises(FormatterError, match="validation_failed_twice"):
        formatter.format_via_llm(_classifier(), _analyst(),
                                 _verifier(), _chunks())


def test_format_via_llm_raises_on_unparseable_first(monkeypatch):
    def boom(**kw):
        raise llm.LLMParseError("garbage")
    monkeypatch.setattr(formatter.llm, "call_agent_json", boom)
    with pytest.raises(FormatterError, match="unparseable_first"):
        formatter.format_via_llm(_classifier(), _analyst(),
                                 _verifier(), _chunks())


def test_format_via_llm_raises_on_unparseable_retry(monkeypatch):
    calls = {"n": 0}
    bad = _good_analysis_json()
    bad["recommended_actions"][0]["source_refs"] = ["cit-99"]
    def fake(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return _llm_result(bad)
        raise llm.LLMParseError("garbage on retry")
    monkeypatch.setattr(formatter.llm, "call_agent_json", fake)
    with pytest.raises(FormatterError, match="unparseable_retry"):
        formatter.format_via_llm(_classifier(), _analyst(),
                                 _verifier(), _chunks())


def test_format_via_llm_propagates_verifier_flags(monkeypatch):
    """LLM should carry through verifier flags. We supply a parsed payload
    that already contains them and check it validates + survives."""
    payload = _good_analysis_json()
    payload["verifier_flags"] = [
        {"kind": "weak_citation", "detail": "single chunk for action 1"},
        {"kind": "low_confidence", "detail": "confidence 0.6"},
    ]
    monkeypatch.setattr(formatter.llm, "call_agent_json",
                        lambda **kw: _llm_result(payload))
    a, _ = formatter.format_via_llm(_classifier(), _analyst(),
                                    _verifier(), _chunks())
    kinds = {f.kind.value for f in a.verifier_flags}
    assert kinds == {"weak_citation", "low_confidence"}


def test_format_via_llm_payload_is_compact(monkeypatch):
    """Long Analyst fields get capped in the prompt payload to keep tokens low."""
    captured = {}
    def fake(**kw):
        captured["payload"] = kw["user_payload"]
        return _llm_result(_good_analysis_json())
    monkeypatch.setattr(formatter.llm, "call_agent_json", fake)

    big_analyst = AnalystOutput(
        summary="x" * 2000, likely_cause="y" * 2000,
        recommended_actions=[AnalystAction(step=1, detail="z" * 2000,
                                           citations=["grp-manuals::m1"])],
        claims=[AnalystClaim(id="claim-1", text="q" * 2000,
                             supports_step=[1],
                             citations=["grp-manuals::m1"])],
        confidence=0.5, open_questions=[],
    )
    big_chunks = [RetrievedChunk(
        chunk_id="grp-manuals::m1", index="grp-manuals", kind="manual",
        locator={"section": "x"}, text="w" * 2000, score=0.9,
    )]
    formatter.format_via_llm(_classifier(), big_analyst,
                             _verifier(), big_chunks)
    p = captured["payload"]
    assert len(p["analyst_output"]["summary"]) <= 600
    assert len(p["analyst_output"]["likely_cause"]) <= 1000
    assert len(p["analyst_output"]["recommended_actions"][0]["detail"]) <= 600
    assert len(p["retrieved_context"][0]["text"]) <= 500
