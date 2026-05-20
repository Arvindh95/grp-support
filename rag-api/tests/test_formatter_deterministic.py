"""Deterministic formatter — Analyst internal → public Analysis."""
from __future__ import annotations

import pytest

from app.agents.analyst import (
    AnalystAction,
    AnalystClaim,
    AnalystOutput,
)
from app.agents.classifier import ClassifierOutput
from app.agents.formatter_deterministic import (
    format_analysis,
)
from app.models import (
    Analysis,
    CitationSource,
    VerifierFlag,
    VerifierFlagKind,
)
from app.retrieval import RetrievedChunk


def _classifier(category="license-error", conf=0.85):
    return ClassifierOutput(category=category, confidence=conf,
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
            text="Resolved by service restart.", score=0.7,
        ),
        RetrievedChunk(
            chunk_id="rfs-tickets-mar-2025::t2",
            index="rfs-tickets-mar-2025", kind="rfs_ticket",
            locator={"lodge_id": "LDG-77001"},
            text="Same symptom, different cause.", score=0.5,
        ),
    ]


def _analyst_output():
    return AnalystOutput(
        summary="Stale license cache.",
        likely_cause="Renewal didn't trigger cache invalidation.",
        recommended_actions=[
            AnalystAction(step=1, detail="Call POST /license/refresh.",
                          citations=["grp-manuals::m1"]),
            AnalystAction(step=2, detail="If step 1 fails, restart service.",
                          citations=["grp-manuals::m1",
                                     "rfs-tickets-mar-2025::t1"]),
        ],
        claims=[
            AnalystClaim(id="claim-1", text="manual documents the endpoint",
                         supports_step=[1],
                         citations=["grp-manuals::m1"]),
        ],
        confidence=0.78,
        open_questions=[],
    )


# ── happy path ────────────────────────────────────────────────────────────────

def test_format_produces_valid_analysis():
    a = format_analysis(_classifier(), _analyst_output(), _chunks())
    assert isinstance(a, Analysis)
    # Pydantic validation ran on construction — schema is fine.
    assert a.category == "license-error"
    # confidence is min(classifier, analyst)
    assert a.confidence == 0.78


def test_format_citations_stable_cit_ids():
    a = format_analysis(_classifier(), _analyst_output(), _chunks())
    # First chunk referenced was m1 → cit-1. Then t1 → cit-2.
    ids = [c.id for c in a.citations]
    assert ids == ["cit-1", "cit-2"]
    assert a.citations[0].source == CitationSource.manual
    assert a.citations[1].source == CitationSource.rfs_ticket
    # source_refs use those same cit ids.
    assert a.recommended_actions[0].source_refs == ["cit-1"]
    assert a.recommended_actions[1].source_refs == ["cit-1", "cit-2"]


def test_format_includes_related_rfs():
    a = format_analysis(_classifier(), _analyst_output(), _chunks())
    lodges = {r.lodge_id for r in a.related_rfs}
    assert "LDG-90211" in lodges
    assert "LDG-77001" in lodges
    # Sorted by score descending.
    assert a.related_rfs[0].score >= a.related_rfs[1].score


def test_format_truncates_long_fields():
    ao = _analyst_output()
    ao.summary = "x" * 1000
    ao.likely_cause = "y" * 2000
    ao.recommended_actions[0].detail = "z" * 3000
    a = format_analysis(_classifier(), ao, _chunks())
    assert len(a.summary) <= 600
    assert len(a.likely_cause) <= 1000
    assert len(a.recommended_actions[0].detail) <= 2000


def test_format_drops_unresolvable_citations():
    ao = _analyst_output()
    ao.recommended_actions[0].citations = ["never-existed", "grp-manuals::m1"]
    a = format_analysis(_classifier(), ao, _chunks())
    # Only the resolvable one survives. Citation pool keeps the one chunk.
    assert a.recommended_actions[0].source_refs == ["cit-1"]
    assert len(a.citations) == 2  # m1 + t1 (still referenced by action 2)


def test_format_low_confidence_adds_flag():
    ao = _analyst_output()
    ao.confidence = 0.1
    a = format_analysis(_classifier(), ao, _chunks())
    kinds = {f.kind for f in a.verifier_flags}
    assert VerifierFlagKind.low_confidence in kinds


def test_format_unsupported_flag_carried():
    ao = _analyst_output()
    ao.unsupported_flag = "validation_failed_twice"
    a = format_analysis(_classifier(), ao, _chunks())
    kinds = {f.kind for f in a.verifier_flags}
    assert VerifierFlagKind.unsupported_claim in kinds


def test_format_extra_flags_preserved():
    extra = [VerifierFlag(kind=VerifierFlagKind.weak_citation,
                          detail="claim-1 only cites one manual chunk")]
    a = format_analysis(_classifier(), _analyst_output(), _chunks(),
                        extra_flags=extra)
    assert any(f.kind == VerifierFlagKind.weak_citation
               for f in a.verifier_flags)


def test_format_falls_back_to_default_action_when_empty():
    ao = AnalystOutput(
        summary="x", likely_cause=None, recommended_actions=[],
        claims=[], confidence=0.0, open_questions=[],
    )
    # Pydantic on Analysis requires min_length=1 actions, so the formatter
    # must inject a triage placeholder.
    a = format_analysis(_classifier(), ao, _chunks())
    assert len(a.recommended_actions) == 1
    assert "Manual triage" in a.recommended_actions[0].detail


