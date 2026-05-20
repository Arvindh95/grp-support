"""Verifier agent — rubric, verdict derivation, flag parsing, soft-fail."""
from __future__ import annotations

import json

import pytest

from app import llm
from app.agents import verifier
from app.agents.analyst import AnalystAction, AnalystClaim, AnalystOutput
from app.agents.verifier import (
    RUBRIC_ALL,
    VERDICT_FLAG,
    VERDICT_MUST_RETRY,
    VERDICT_PASS,
    VerifierOutput,
    VerifierValidationError,
    _validate,
)
from app.llm import LLMResult, LLMUsage
from app.models import AgentStepStatus, VerifierFlagKind
from app.retrieval import RetrievedChunk


def _analyst():
    return AnalystOutput(
        summary="License cache stale.", likely_cause="cache not invalidated",
        recommended_actions=[
            AnalystAction(step=1, detail="Call POST /license/refresh.",
                          citations=["grp-manuals::m1"]),
        ],
        claims=[
            AnalystClaim(id="claim-1", text="manual documents endpoint",
                         supports_step=[1], citations=["grp-manuals::m1"]),
        ],
        confidence=0.78, open_questions=[],
    )


def _chunks():
    return [RetrievedChunk(
        chunk_id="grp-manuals::m1", index="grp-manuals", kind="manual",
        locator={"section": "License"},
        text="After renewal call POST /license/refresh.", score=0.9,
    )]


def _llm_result(parsed, *, input_tokens=600, output_tokens=120):
    return LLMResult(
        text=json.dumps(parsed), parsed=parsed,
        usage=LLMUsage(input_tokens=input_tokens, output_tokens=output_tokens),
        duration_ms=200, stop_reason="end_turn",
    )


def _all_true_rubric():
    return {k: True for k in RUBRIC_ALL}


# ── _validate unit tests ──────────────────────────────────────────────────────

def test_validate_pass_verdict():
    out = _validate({
        "verdict": "pass",
        "rubric_scores": _all_true_rubric(),
        "flags": [],
        "must_retry_reason": None,
    })
    assert out.verdict == VERDICT_PASS
    assert out.rubric_scores["every_claim_cited"] is True
    assert out.flags == []


def test_validate_flag_with_nonfatal_failures():
    scores = _all_true_rubric()
    scores["actions_actionable"] = False
    out = _validate({
        "verdict": "flag",
        "rubric_scores": scores,
        "flags": [{"kind": "low_confidence", "detail": "vague verb in step 1"}],
        "must_retry_reason": None,
    })
    assert out.verdict == VERDICT_FLAG
    assert out.flags[0].kind == VerifierFlagKind.low_confidence


def test_validate_must_retry_on_fatal_failure():
    scores = _all_true_rubric()
    scores["no_external_facts"] = False
    out = _validate({
        "verdict": "must_retry",
        "rubric_scores": scores,
        "flags": [],
        "must_retry_reason": "claim-2 cites a chunk that doesn't support it",
    })
    assert out.verdict == VERDICT_MUST_RETRY
    assert out.must_retry_reason


def test_validate_derives_must_retry_when_llm_picked_pass():
    """LLM picks 'pass' but rubric_scores has a fatal=false → trust the rubric."""
    scores = _all_true_rubric()
    scores["every_claim_cited"] = False
    out = _validate({
        "verdict": "pass",   # contradicts rubric
        "rubric_scores": scores,
        "flags": [],
        "must_retry_reason": None,
    })
    assert out.verdict == VERDICT_MUST_RETRY
    # Auto-derived reason names the failing key.
    assert "every_claim_cited" in (out.must_retry_reason or "")


def test_validate_rejects_bad_verdict():
    with pytest.raises(VerifierValidationError, match="verdict"):
        _validate({"verdict": "perfect",
                   "rubric_scores": _all_true_rubric()})


def test_validate_rejects_missing_rubric_key():
    scores = _all_true_rubric()
    del scores["confidence_calibrated"]
    with pytest.raises(VerifierValidationError, match="confidence_calibrated"):
        _validate({"verdict": "pass", "rubric_scores": scores})


def test_validate_drops_unknown_flag_kind():
    out = _validate({
        "verdict": "flag",
        "rubric_scores": {**_all_true_rubric(), "actions_actionable": False},
        "flags": [
            {"kind": "made_up_kind", "detail": "x"},
            {"kind": "weak_citation", "detail": "claim-1 only cites one chunk"},
        ],
    })
    # Unknown kind discarded; valid one kept.
    assert len(out.flags) == 1
    assert out.flags[0].kind == VerifierFlagKind.weak_citation


def test_validate_drops_empty_detail_flag():
    out = _validate({
        "verdict": "flag",
        "rubric_scores": {**_all_true_rubric(), "actions_actionable": False},
        "flags": [{"kind": "weak_citation", "detail": ""}],
    })
    assert out.flags == []


# ── verify() integration ──────────────────────────────────────────────────────

def test_verify_happy_path(monkeypatch):
    monkeypatch.setattr(verifier.llm, "call_agent_json",
                        lambda **kw: _llm_result({
                            "verdict": "pass",
                            "rubric_scores": _all_true_rubric(),
                            "flags": [], "must_retry_reason": None,
                        }))
    out, step = verifier.verify(_analyst(), _chunks(), "license-error")
    assert out.verdict == VERDICT_PASS
    assert step.status == AgentStepStatus.ok
    assert step.note == "verdict=pass"


def test_verify_must_retry_status_marked_retried(monkeypatch):
    monkeypatch.setattr(verifier.llm, "call_agent_json",
                        lambda **kw: _llm_result({
                            "verdict": "must_retry",
                            "rubric_scores": {**_all_true_rubric(),
                                              "no_external_facts": False},
                            "flags": [], "must_retry_reason": "claim-1 unsupported",
                        }))
    out, step = verifier.verify(_analyst(), _chunks(), "license-error")
    assert out.verdict == VERDICT_MUST_RETRY
    assert step.status == AgentStepStatus.retried
    assert "reason=claim-1 unsupported" in (step.note or "")


def test_verify_soft_fails_on_unparseable(monkeypatch):
    def boom(**kw):
        raise llm.LLMParseError("garbage")
    monkeypatch.setattr(verifier.llm, "call_agent_json", boom)
    out, step = verifier.verify(_analyst(), _chunks(), "license-error")
    assert out.verdict == VERDICT_FLAG   # never blocks
    assert step.status == AgentStepStatus.failed
    assert out.flags[0].kind == VerifierFlagKind.low_confidence


def test_verify_soft_fails_on_schema_violation(monkeypatch):
    monkeypatch.setattr(verifier.llm, "call_agent_json",
                        lambda **kw: _llm_result({"verdict": "bogus"}))
    out, step = verifier.verify(_analyst(), _chunks(), "license-error")
    assert out.verdict == VERDICT_FLAG
    assert step.status == AgentStepStatus.failed


def test_verify_input_compaction_keeps_token_budget(monkeypatch):
    """Big analyst output + long chunks shouldn't balloon the payload."""
    captured = {}
    def fake(**kw):
        captured["payload"] = kw["user_payload"]
        return _llm_result({"verdict": "pass",
                            "rubric_scores": _all_true_rubric()})
    monkeypatch.setattr(verifier.llm, "call_agent_json", fake)

    big_analyst = AnalystOutput(
        summary="x" * 5000, likely_cause="y" * 5000,
        recommended_actions=[
            AnalystAction(step=1, detail="z" * 5000,
                          citations=["grp-manuals::m1"]),
        ],
        claims=[AnalystClaim(id="claim-1", text="q" * 5000, supports_step=[1],
                             citations=["grp-manuals::m1"])],
        confidence=0.5, open_questions=["a"] * 50,
    )
    big_chunks = [RetrievedChunk(
        chunk_id="grp-manuals::m1", index="grp-manuals", kind="manual",
        locator={"section": "x"}, text="w" * 5000, score=0.9,
    )]
    verifier.verify(big_analyst, big_chunks, "license-error")

    p = captured["payload"]
    assert len(p["analyst_output"]["summary"]) <= 400
    assert len(p["analyst_output"]["recommended_actions"][0]["detail"]) <= 2000
    assert len(p["analyst_output"]["claims"][0]["text"]) <= 400
    assert len(p["analyst_output"]["open_questions"]) <= 3
    assert len(p["retrieved_context"][0]["text"]) <= 600
