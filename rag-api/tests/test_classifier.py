"""Classifier agent — schema validation and error tolerance."""
from __future__ import annotations

import json

import pytest

from app import llm
from app.agents import classifier
from app.agents.classifier import (
    CATEGORIES,
    ClassifierOutput,
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
        "tags": ["billing"], "language": "en",
    })
    assert out.category == "license-error"
    assert out.confidence == 0.8


def test_validate_rejects_unknown_category():
    with pytest.raises(ValueError):
        _validate({"category": "purple", "confidence": 0.5})


def test_validate_rejects_bad_confidence():
    with pytest.raises(ValueError):
        _validate({"category": "other", "confidence": 1.5})


# ── classify() with mocked LLM ────────────────────────────────────────────────

def test_classify_happy_path(monkeypatch):
    monkeypatch.setattr(
        classifier.llm, "call_agent_json",
        lambda **kw: _llm_returning({
            "category": "license-error", "confidence": 0.82,
            "tags": ["billing", "renewal"], "language": "en",
        }),
    )
    out, step = classifier.classify(_rfs())
    assert isinstance(out, ClassifierOutput)
    assert out.category == "license-error"
    assert step.status == AgentStepStatus.ok
    assert step.input_tokens == 100


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
        lambda **kw: _llm_returning({"category": "made-up-cat", "confidence": 0.5}),
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
