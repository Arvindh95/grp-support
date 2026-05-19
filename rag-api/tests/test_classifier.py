"""Classifier agent — schema validation, short-circuit, error tolerance."""
from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from app import llm
from app.agents import classifier
from app.agents.classifier import (
    CATEGORIES,
    ClassifierOutput,
    DuplicateCandidate,
    _validate,
)
from app.llm import LLMResult, LLMUsage
from app.models import RFS, AgentStepStatus


def _rfs(notes="User cannot log in after license renewal — license expired error."):
    return RFS(lodge_id="LDG-1", notes=notes)


def _llm_returning(parsed: dict, *, input_tokens=100, output_tokens=50,
                   cache_read=0):
    return LLMResult(
        text=json.dumps(parsed),
        parsed=parsed,
        usage=LLMUsage(input_tokens=input_tokens,
                       input_cache_read_tokens=cache_read,
                       output_tokens=output_tokens),
        duration_ms=10,
        stop_reason="end_turn",
    )


# ── _validate ─────────────────────────────────────────────────────────────────

def test_validate_accepts_well_formed():
    out = _validate({
        "category": "license-error", "confidence": 0.8,
        "short_circuit": False, "short_circuit_reason": None,
        "short_circuit_payload": None, "tags": ["billing"], "language": "en",
    })
    assert out.category == "license-error"
    assert out.confidence == 0.8


def test_validate_rejects_unknown_category():
    with pytest.raises(ValueError):
        _validate({"category": "purple", "confidence": 0.5,
                   "short_circuit": False})


def test_validate_rejects_bad_confidence():
    with pytest.raises(ValueError):
        _validate({"category": "other", "confidence": 1.5,
                   "short_circuit": False})


def test_validate_requires_reason_when_short_circuit():
    with pytest.raises(ValueError):
        _validate({"category": "duplicate", "confidence": 0.9,
                   "short_circuit": True, "short_circuit_reason": None})


def test_validate_rejects_orphan_reason_when_not_sc():
    with pytest.raises(ValueError):
        _validate({"category": "other", "confidence": 0.5,
                   "short_circuit": False,
                   "short_circuit_reason": "shouldnt be here"})


# ── classify() with mocked LLM ────────────────────────────────────────────────

def test_classify_happy_path(monkeypatch):
    monkeypatch.setattr(
        classifier.llm, "call_agent_json",
        lambda **kw: _llm_returning({
            "category": "license-error", "confidence": 0.82,
            "short_circuit": False, "short_circuit_reason": None,
            "short_circuit_payload": None,
            "tags": ["billing", "renewal"], "language": "en",
        }),
    )
    out, step = classifier.classify(_rfs())
    assert isinstance(out, ClassifierOutput)
    assert out.category == "license-error"
    assert out.short_circuit is False
    assert step.status == AgentStepStatus.ok
    assert step.input_tokens == 100


def test_classify_short_circuit_duplicate(monkeypatch):
    captured = {}
    def fake(**kw):
        captured.update(kw)
        return _llm_returning({
            "category": "duplicate", "confidence": 0.95,
            "short_circuit": True,
            "short_circuit_reason": "near_duplicate_of_LDG-99",
            "short_circuit_payload": {
                "duplicate_of": "LDG-99",
                "suggested_response": "Same as LDG-99",
            },
            "tags": ["duplicate"], "language": "en",
        })
    monkeypatch.setattr(classifier.llm, "call_agent_json", fake)

    dupes = [DuplicateCandidate(lodge_id="LDG-99", score=0.93,
                                snippet="License renewal stuck")]
    out, step = classifier.classify(_rfs(), duplicate_candidates=dupes)
    assert out.short_circuit
    assert out.short_circuit_payload["duplicate_of"] == "LDG-99"
    assert step.status == AgentStepStatus.short_circuit
    # Make sure dupe candidates landed in the LLM input.
    assert "LDG-99" in captured["user_payload"]["duplicate_candidates"][0]["lodge_id"]


def test_classify_soft_fails_on_unparseable(monkeypatch):
    def boom(**kw):
        raise llm.LLMParseError("garbage")
    monkeypatch.setattr(classifier.llm, "call_agent_json", boom)
    out, step = classifier.classify(_rfs())
    assert out.category == "other"
    assert out.confidence == 0.0
    assert step.status == AgentStepStatus.failed


def test_classify_soft_fails_on_schema_violation(monkeypatch):
    monkeypatch.setattr(
        classifier.llm, "call_agent_json",
        lambda **kw: _llm_returning({"category": "made-up-cat", "confidence": 0.5,
                                     "short_circuit": False}),
    )
    out, step = classifier.classify(_rfs())
    assert out.category == "other"
    assert step.status == AgentStepStatus.failed
    assert "schema_violation" in (step.note or "")


def test_categories_match_contract():
    # Tripwire: keep the python enum in sync with the contract doc.
    assert "duplicate" in CATEGORIES
    assert "other" in CATEGORIES
    assert len(CATEGORIES) == 12
