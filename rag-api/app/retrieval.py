"""Retrieval executor.

Takes the Retrieval Planner's `queries` list, runs each plan against
Elasticsearch, dedupes the results, normalizes scores, and packs them into
`RetrievedChunk` objects ready for the Analyst.

The Planner does NOT call ES. This module does.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable

import requests

from . import embed
from .deps import get_config

log = logging.getLogger("rag-api.retrieval")


# Hard cap from contracts/02-retrieval-planner.md
MAX_TOTAL_CHUNKS = 12

# Maps logical kinds → (real ES index, source-field allowlist, snippet fields)
# Reflects the actual grp-api ES schemas.
INDEX_REGISTRY: dict[str, dict[str, Any]] = {
    "grp-manuals": {
        "kind": "manual",
        "source_fields": ["module", "section", "content", "screen_codes",
                          "prev_section"],
        "text_fields": ["content"],
        "locator_fields": ["module", "section"],
    },
    "rfs-tickets-*": {
        "kind": "rfs_ticket",
        "source_fields": ["lodge_id", "referno", "notes", "relatedarea",
                          "priority", "laststatus", "action_summary"],
        "text_fields": ["notes", "action_summary"],
        "locator_fields": ["lodge_id", "referno"],
    },
    "grp-code": {
        "kind": "code_script",
        "source_fields": ["filename", "purpose", "content", "language"],
        "text_fields": ["content", "purpose"],
        "locator_fields": ["filename", "language"],
    },
}


# Description of available indices, passed to the Planner agent.
def available_indices_payload() -> list[dict[str, Any]]:
    return [
        {"name": name, "kind": meta["kind"], "fields": meta["source_fields"]}
        for name, meta in INDEX_REGISTRY.items()
    ]


# ── Data shapes ────────────────────────────────────────────────────────────────

@dataclass
class RetrievedChunk:
    chunk_id: str            # stable id used by Analyst's claim citations
    index: str               # e.g. "grp-manuals" or "rfs-tickets-jan-2025"
    kind: str                # "manual" | "rfs_ticket" | "code_script"
    locator: dict[str, Any]
    text: str
    score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "source": self.kind,
            "locator": self.locator,
            "text": self.text,
            "score": self.score,
        }


@dataclass
class RetrievalDebug:
    queries_run: int = 0
    raw_hits: int = 0
    after_dedupe: int = 0
    after_cap: int = 0
    errors: list[str] = field(default_factory=list)


# ── ES client (single boundary for tests) ──────────────────────────────────────

def _es_search(index: str, body: dict[str, Any]) -> dict[str, Any]:
    cfg = get_config()
    r = requests.post(
        f"{cfg.es_url}/{index}/_search",
        auth=(cfg.es_user, cfg.es_password),
        verify=cfg.es_verify_tls,
        json=body,
        timeout=10,
    )
    if r.status_code != 200:
        raise RuntimeError(f"ES {r.status_code}: {r.text[:200]}")
    return r.json()


# ── Plan validation ────────────────────────────────────────────────────────────

def _validate_plan(queries: list[dict[str, Any]]) -> tuple[bool, str | None]:
    """Returns (ok, error_message). Mirrors contracts/02 hard constraints."""
    if not queries:
        return False, "no queries"
    total = sum(int(q.get("max_chunks", 0)) for q in queries)
    if total > MAX_TOTAL_CHUNKS:
        return False, f"sum(max_chunks)={total} > {MAX_TOTAL_CHUNKS}"
    has_manual = any(q.get("index") == "grp-manuals" for q in queries)
    has_ticket = any((q.get("index") or "").startswith("rfs-tickets") for q in queries)
    if not has_manual:
        return False, "no manual query"
    if not has_ticket:
        return False, "no ticket query"
    for q in queries:
        if q.get("mode") not in ("hybrid", "knn", "lexical"):
            return False, f"bad mode={q.get('mode')!r}"
        if q.get("index") not in INDEX_REGISTRY and not (
            q.get("index", "").startswith("rfs-tickets")
        ):
            return False, f"unknown index={q.get('index')!r}"
    return True, None


def validate_plan(queries: list[dict[str, Any]]) -> None:
    ok, err = _validate_plan(queries)
    if not ok:
        raise ValueError(err)


# ── Build ES body from a plan ──────────────────────────────────────────────────

def _registry_for_index(index: str) -> dict[str, Any]:
    if index in INDEX_REGISTRY:
        return INDEX_REGISTRY[index]
    if index.startswith("rfs-tickets"):
        return INDEX_REGISTRY["rfs-tickets-*"]
    raise KeyError(f"no registry entry for index {index!r}")


def _build_es_body(plan: dict[str, Any], embedding: list[float] | None) -> dict[str, Any]:
    reg = _registry_for_index(plan["index"])
    body: dict[str, Any] = {
        "size": int(plan.get("max_chunks", 5)),
        "_source": reg["source_fields"],
    }

    mode = plan.get("mode", "hybrid")

    if mode in ("knn", "hybrid"):
        knn_spec = plan.get("knn") or {}
        k = int(knn_spec.get("k", body["size"]))
        num_candidates = int(knn_spec.get("num_candidates", max(k * 10, 100)))
        body["knn"] = {
            "field": knn_spec.get("field", "embedding"),
            "query_vector": embedding,
            "k": k,
            "num_candidates": num_candidates,
        }

    lexical = plan.get("lexical")
    if mode in ("lexical", "hybrid") and lexical:
        # The Planner emits a body like {"must":[...], "filter":[...]} —
        # wrap it in a bool query.
        bool_q: dict[str, Any] = {}
        for key in ("must", "should", "filter", "must_not"):
            if lexical.get(key):
                bool_q[key] = lexical[key]
        if bool_q:
            body["query"] = {"bool": bool_q}

    return body


# ── Scoring + dedupe ───────────────────────────────────────────────────────────

def _build_chunk(hit: dict[str, Any], index: str) -> RetrievedChunk:
    reg = _registry_for_index(index)
    src = hit.get("_source") or {}
    locator = {f: src[f] for f in reg["locator_fields"] if f in src}
    # Always include the concrete index — Analyst citations need it to point
    # at the right month for tickets.
    locator["_index"] = hit.get("_index", index)

    text_parts: list[str] = []
    for f in reg["text_fields"]:
        v = src.get(f)
        if isinstance(v, str) and v:
            text_parts.append(v)
    text = "\n\n".join(text_parts) or ""

    return RetrievedChunk(
        chunk_id=f"{hit.get('_index', index)}::{hit.get('_id', '?')}",
        index=hit.get("_index", index),
        kind=reg["kind"],
        locator=locator,
        text=text,
        score=float(hit.get("_score") or 0.0),
    )


def _normalize_scores_per_query(chunks: list[RetrievedChunk]) -> None:
    """Min-max normalise within each (index) so kNN and lexical scores can
    be mixed when interleaving across queries."""
    by_index: dict[str, list[RetrievedChunk]] = {}
    for c in chunks:
        by_index.setdefault(c.index, []).append(c)
    for items in by_index.values():
        if not items:
            continue
        lo = min(c.score for c in items)
        hi = max(c.score for c in items)
        if hi - lo < 1e-9:
            continue
        for c in items:
            c.score = (c.score - lo) / (hi - lo)


def _dedupe(chunks: Iterable[RetrievedChunk]) -> list[RetrievedChunk]:
    seen: dict[str, RetrievedChunk] = {}
    for c in chunks:
        prev = seen.get(c.chunk_id)
        if prev is None or c.score > prev.score:
            seen[c.chunk_id] = c
    return sorted(seen.values(), key=lambda c: c.score, reverse=True)


# ── Public entrypoint ──────────────────────────────────────────────────────────

def execute_plan(
    queries: list[dict[str, Any]],
    *,
    embedding: list[float] | None = None,
    embed_query: str | None = None,
) -> tuple[list[RetrievedChunk], RetrievalDebug]:
    """Run a Planner-output `queries` list. Returns (chunks, debug).

    `embedding` may be supplied directly; otherwise `embed_query` is used to
    compute one on the fly. If neither is provided, kNN/hybrid queries skip
    the vector component (lexical-only fallback).
    """
    debug = RetrievalDebug()

    validate_plan(queries)

    needs_embedding = any(q.get("mode") in ("knn", "hybrid") for q in queries)
    if needs_embedding and embedding is None and embed_query:
        try:
            embedding = embed.embed_text(embed_query)
        except embed.EmbedError as e:
            debug.errors.append(f"embed: {e}")
            embedding = None

    all_chunks: list[RetrievedChunk] = []
    for plan in queries:
        debug.queries_run += 1
        try:
            body = _build_es_body(plan, embedding)
            result = _es_search(plan["index"], body)
        except Exception as e:
            log.warning('"retrieval.query_failed index=%s err=%s"',
                        plan.get("index"), e)
            debug.errors.append(f"{plan.get('index')}: {e}")
            continue
        hits = result.get("hits", {}).get("hits", [])
        debug.raw_hits += len(hits)
        for h in hits:
            all_chunks.append(_build_chunk(h, plan["index"]))

    _normalize_scores_per_query(all_chunks)
    deduped = _dedupe(all_chunks)
    debug.after_dedupe = len(deduped)

    capped = deduped[:MAX_TOTAL_CHUNKS]
    debug.after_cap = len(capped)
    return capped, debug
