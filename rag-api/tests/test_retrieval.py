"""Retrieval executor — plan validation, ES query building, dedupe."""
from __future__ import annotations

import pytest

from app import retrieval


# ── Plan validation ───────────────────────────────────────────────────────────

VALID_PLAN = [
    {
        "index": "grp-manuals",
        "mode": "hybrid",
        "knn": {"field": "embedding", "k": 4, "num_candidates": 80},
        "lexical": {"must": [{"match": {"content": "license"}}]},
        "max_chunks": 4,
    },
    {
        "index": "rfs-tickets-mar-2025",
        "mode": "knn",
        "knn": {"field": "embedding", "k": 4, "num_candidates": 80},
        "max_chunks": 4,
    },
]


def test_valid_plan_passes():
    retrieval.validate_plan(VALID_PLAN)


def test_plan_too_many_chunks_rejected():
    plan = [dict(VALID_PLAN[0], max_chunks=8), dict(VALID_PLAN[1], max_chunks=8)]
    with pytest.raises(ValueError, match="max_chunks"):
        retrieval.validate_plan(plan)


def test_plan_without_manual_rejected():
    plan = [dict(VALID_PLAN[1])]
    with pytest.raises(ValueError, match="manual"):
        retrieval.validate_plan(plan)


def test_plan_without_ticket_rejected():
    plan = [dict(VALID_PLAN[0])]
    with pytest.raises(ValueError, match="ticket"):
        retrieval.validate_plan(plan)


def test_plan_bad_mode_rejected():
    plan = [dict(VALID_PLAN[0], mode="bogus"), VALID_PLAN[1]]
    with pytest.raises(ValueError, match="mode"):
        retrieval.validate_plan(plan)


def test_plan_unknown_index_rejected():
    plan = [
        VALID_PLAN[0],
        VALID_PLAN[1],
        {"index": "weird-index", "mode": "knn",
         "knn": {"k": 1, "num_candidates": 10}, "max_chunks": 1},
    ]
    with pytest.raises(ValueError, match="index"):
        retrieval.validate_plan(plan)


# ── ES body building ──────────────────────────────────────────────────────────

def test_build_body_hybrid_includes_knn_and_query():
    body = retrieval._build_es_body(VALID_PLAN[0], embedding=[0.1] * 4)
    assert body["knn"]["k"] == 4
    assert body["knn"]["query_vector"] == [0.1] * 4
    assert body["query"]["bool"]["must"]
    assert body["size"] == 4
    assert set(body["_source"]) >= {"module", "section", "content"}


def test_build_body_knn_omits_query():
    body = retrieval._build_es_body(VALID_PLAN[1], embedding=[0.2] * 4)
    assert "knn" in body
    assert "query" not in body


# ── End-to-end with mocked ES ─────────────────────────────────────────────────

def _hit(idx: str, doc_id: str, src: dict, score: float = 1.0):
    return {"_index": idx, "_id": doc_id, "_score": score, "_source": src}


def test_execute_plan_returns_chunks(monkeypatch):
    responses = {
        "grp-manuals": {"hits": {"hits": [
            _hit("grp-manuals", "m1",
                 {"module": "Admin", "section": "License",
                  "content": "License renewal procedure: call POST /license/refresh..."},
                 score=2.0),
            _hit("grp-manuals", "m2",
                 {"module": "Admin", "section": "Setup",
                  "content": "Install steps..."}, score=1.0),
        ]}},
        "rfs-tickets-mar-2025": {"hits": {"hits": [
            _hit("rfs-tickets-mar-2025", "t1",
                 {"lodge_id": "LDG-9", "notes": "License stuck after renewal",
                  "action_summary": "Resolved by restart"}, score=1.5),
        ]}},
    }
    monkeypatch.setattr(retrieval, "_es_search",
                        lambda index, body: responses[index])
    monkeypatch.setattr(retrieval.embed, "embed_text", lambda t: [0.1] * 4)

    chunks, dbg = retrieval.execute_plan(VALID_PLAN, embed_query="license renewal")
    assert dbg.queries_run == 2
    assert dbg.raw_hits == 3
    assert dbg.after_dedupe == 3
    assert dbg.after_cap == 3
    ids = [c.chunk_id for c in chunks]
    assert "grp-manuals::m1" in ids
    assert "rfs-tickets-mar-2025::t1" in ids


def test_execute_plan_dedupes_by_chunk_id(monkeypatch):
    # Same _index+_id from two queries → single chunk after dedupe.
    same_hit = _hit("grp-manuals", "m1",
                    {"module": "Admin", "section": "License",
                     "content": "x"}, score=2.0)
    distinct_hit = _hit("rfs-tickets-jan-2025", "t9",
                        {"lodge_id": "LDG-9", "notes": "y"}, score=1.0)
    def fake(index, body):
        if index == "grp-manuals":
            return {"hits": {"hits": [same_hit, same_hit]}}
        return {"hits": {"hits": [distinct_hit]}}
    monkeypatch.setattr(retrieval, "_es_search", fake)
    monkeypatch.setattr(retrieval.embed, "embed_text", lambda t: [0.1] * 4)
    plan = [
        {"index": "grp-manuals", "mode": "knn",
         "knn": {"k": 2, "num_candidates": 10}, "max_chunks": 2},
        {"index": "rfs-tickets-jan-2025", "mode": "knn",
         "knn": {"k": 2, "num_candidates": 10}, "max_chunks": 2},
    ]
    chunks, dbg = retrieval.execute_plan(plan, embed_query="hi")
    assert dbg.raw_hits == 3
    # m1 collapses to one entry → 1 manual + 1 ticket = 2 after dedupe.
    assert dbg.after_dedupe == 2


def test_execute_plan_caps_at_12(monkeypatch):
    def many_hits(index, body):
        return {"hits": {"hits": [
            _hit(index, f"{index}-{i}", {"content": "x", "notes": "x"}, score=i)
            for i in range(20)
        ]}}
    monkeypatch.setattr(retrieval, "_es_search", many_hits)
    monkeypatch.setattr(retrieval.embed, "embed_text", lambda t: [0.1] * 4)
    plan = [
        {"index": "grp-manuals", "mode": "knn",
         "knn": {"k": 6, "num_candidates": 60}, "max_chunks": 6},
        {"index": "rfs-tickets-jan-2025", "mode": "knn",
         "knn": {"k": 6, "num_candidates": 60}, "max_chunks": 6},
    ]
    chunks, dbg = retrieval.execute_plan(plan, embed_query="hi")
    assert len(chunks) == retrieval.MAX_TOTAL_CHUNKS


def test_execute_plan_continues_on_index_error(monkeypatch):
    def selective(index, body):
        if index == "grp-manuals":
            raise RuntimeError("ES 503")
        return {"hits": {"hits": [_hit(index, "t1",
                                        {"lodge_id": "L1", "notes": "n"},
                                        score=1.0)]}}
    monkeypatch.setattr(retrieval, "_es_search", selective)
    monkeypatch.setattr(retrieval.embed, "embed_text", lambda t: [0.1] * 4)
    chunks, dbg = retrieval.execute_plan(VALID_PLAN, embed_query="hi")
    assert any("grp-manuals" in e for e in dbg.errors)
    # Other query still produced results.
    assert any("rfs-tickets-mar-2025" in c.chunk_id for c in chunks)


def test_execute_plan_skips_embedding_when_supplied(monkeypatch):
    monkeypatch.setattr(retrieval, "_es_search",
                        lambda i, b: {"hits": {"hits": []}})
    calls = {"n": 0}
    def boom(t):
        calls["n"] += 1
        raise retrieval.embed.EmbedError("should not be called")
    monkeypatch.setattr(retrieval.embed, "embed_text", boom)
    retrieval.execute_plan(VALID_PLAN, embedding=[0.1] * 4)
    assert calls["n"] == 0


def test_available_indices_payload_shape():
    payload = retrieval.available_indices_payload()
    names = {x["name"] for x in payload}
    assert {"grp-manuals", "rfs-tickets-*", "grp-code"} <= names
