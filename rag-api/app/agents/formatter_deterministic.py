"""Deterministic Formatter — pure Python, no LLM call.

Purpose
-------
Convert the Analyst's internal output (claims+actions referencing chunk_ids)
into the public `Analysis` shape exposed in `openapi.yaml`. Used until the
LLM Formatter lands in W9. Also serves as the documented fallback when the
LLM Formatter fails twice (see contracts/05).

Determinism matters here: this function is also the correctness floor of
the whole pipeline. Worst case, the LLM Formatter is disabled and this
runs in its place — outputs must still validate against `Analysis`.
"""
from __future__ import annotations

import logging
from typing import Sequence

from ..models import (
    Analysis,
    Citation,
    CitationSource,
    RecommendedAction,
    RelatedRFS,
    VerifierFlag,
    VerifierFlagKind,
)
from ..retrieval import RetrievedChunk
from .analyst import AnalystOutput
from .classifier import ClassifierOutput

log = logging.getLogger("rag-api.formatter.det")


_MAX_SUMMARY = 600
_MAX_LIKELY_CAUSE = 1000
_MAX_ACTION_DETAIL = 2000
_MAX_SNIPPET = 400
_MAX_RELATED_SNIPPET = 240
_MAX_RELATED = 5
_MAX_ACTIONS = 10


def _kind_to_source(kind: str) -> CitationSource:
    if kind == "manual":
        return CitationSource.manual
    if kind == "rfs_ticket":
        return CitationSource.rfs_ticket
    if kind == "attachment":
        return CitationSource.attachment
    return CitationSource.code_script


def _snippet(text: str, max_chars: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _truncate(text: str | None, max_chars: int) -> str:
    if not text:
        return ""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def format_short_circuit(classifier_output: ClassifierOutput) -> Analysis:
    """Build an Analysis directly from a short-circuited Classifier output."""
    payload = classifier_output.short_circuit_payload or {}
    suggested = payload.get("suggested_response") or (
        f"Classifier short-circuited: {classifier_output.short_circuit_reason}."
    )
    citations: list[Citation] = []
    related: list[RelatedRFS] = []
    if classifier_output.category == "duplicate" and payload.get("duplicate_of"):
        dup_id = payload["duplicate_of"]
        citations.append(Citation(
            id="cit-1",
            source=CitationSource.rfs_ticket,
            locator={"lodge_id": dup_id},
            snippet=_snippet(f"Duplicate of {dup_id}", _MAX_SNIPPET),
            score=classifier_output.confidence,
        ))
        related.append(RelatedRFS(lodge_id=dup_id,
                                  score=classifier_output.confidence,
                                  snippet=_snippet(suggested, _MAX_RELATED_SNIPPET)))
    return Analysis(
        category=classifier_output.category,
        confidence=classifier_output.confidence,
        summary=_truncate(suggested, _MAX_SUMMARY) or "Short-circuited.",
        likely_cause=None,
        recommended_actions=[RecommendedAction(
            step=1,
            detail=_truncate(suggested, _MAX_ACTION_DETAIL)
                or "See original ticket.",
            source_refs=[c.id for c in citations],
        )],
        citations=citations,
        related_rfs=related,
        verifier_flags=[],
    )


def format_analysis(
    classifier_output: ClassifierOutput,
    analyst_output: AnalystOutput,
    chunks: Sequence[RetrievedChunk],
    *,
    extra_flags: Sequence[VerifierFlag] = (),
) -> Analysis:
    """Map Analyst internal output → public Analysis."""

    # Build the citation pool: every chunk_id referenced by an action or
    # claim, in order of first appearance, skipping ids we cannot resolve.
    # cit-N numbering follows the resolvable order so the public output
    # never has gaps.
    chunk_by_id = {c.chunk_id: c for c in chunks}
    cit_order: list[str] = []
    cit_seen: set[str] = set()

    def _take(cid: str) -> None:
        if cid in cit_seen:
            return
        if cid not in chunk_by_id:
            log.warning('"formatter.unknown_chunk id=%s"', cid)
            return
        cit_seen.add(cid)
        cit_order.append(cid)

    for a in analyst_output.recommended_actions:
        for cid in a.citations:
            _take(cid)
    for c in analyst_output.claims:
        for cid in c.citations:
            _take(cid)

    chunk_id_to_cit_id: dict[str, str] = {}
    citations: list[Citation] = []
    for i, chunk_id in enumerate(cit_order, start=1):
        chunk = chunk_by_id[chunk_id]
        cit_id = f"cit-{i}"
        chunk_id_to_cit_id[chunk_id] = cit_id
        citations.append(Citation(
            id=cit_id,
            source=_kind_to_source(chunk.kind),
            locator=chunk.locator,
            snippet=_snippet(chunk.text, _MAX_SNIPPET),
            score=chunk.score,
        ))

    # Actions — map citation chunk_ids → cit-N. Drop ids that didn't resolve.
    actions: list[RecommendedAction] = []
    for a in analyst_output.recommended_actions[:_MAX_ACTIONS]:
        refs = [chunk_id_to_cit_id[c] for c in a.citations
                if c in chunk_id_to_cit_id]
        actions.append(RecommendedAction(
            step=a.step,
            detail=_truncate(a.detail, _MAX_ACTION_DETAIL),
            source_refs=refs,
        ))
    if not actions:
        # Always emit at least one action so the Analysis schema validates.
        actions.append(RecommendedAction(
            step=1,
            detail="Manual triage recommended — Analyst produced no actions.",
            source_refs=[],
        ))

    # Related RFS — every rfs_ticket chunk that appears in retrieval, sorted
    # by score, deduped by lodge_id.
    related: list[RelatedRFS] = []
    seen_lodges: set[str] = set()
    for c in sorted(chunks, key=lambda x: x.score, reverse=True):
        if c.kind != "rfs_ticket":
            continue
        lodge_id = str(c.locator.get("lodge_id") or "")
        if not lodge_id or lodge_id in seen_lodges:
            continue
        seen_lodges.add(lodge_id)
        related.append(RelatedRFS(
            lodge_id=lodge_id,
            score=c.score,
            snippet=_snippet(c.text, _MAX_RELATED_SNIPPET),
        ))
        if len(related) >= _MAX_RELATED:
            break

    # Verifier flags — carry through caller flags + Analyst self-flag.
    flags: list[VerifierFlag] = list(extra_flags)
    if analyst_output.unsupported_flag:
        flags.append(VerifierFlag(
            kind=VerifierFlagKind.unsupported_claim,
            detail=f"Analyst self-flagged: {analyst_output.unsupported_flag}",
        ))
    if analyst_output.confidence < 0.20 and not any(
        f.kind == VerifierFlagKind.low_confidence for f in flags
    ):
        flags.append(VerifierFlag(
            kind=VerifierFlagKind.low_confidence,
            detail=f"Analyst confidence={analyst_output.confidence:.2f}",
        ))

    # Final confidence — take the lower of Classifier and Analyst, since both
    # contribute uncertainty.
    confidence = min(classifier_output.confidence, analyst_output.confidence)

    return Analysis(
        category=classifier_output.category,
        confidence=confidence,
        summary=_truncate(analyst_output.summary, _MAX_SUMMARY) or "(no summary)",
        likely_cause=_truncate(analyst_output.likely_cause, _MAX_LIKELY_CAUSE) or None,
        recommended_actions=actions,
        citations=citations,
        related_rfs=related,
        verifier_flags=flags,
    )
