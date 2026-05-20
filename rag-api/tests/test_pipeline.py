"""End-to-end pipeline tests — submit → run worker → poll.

W8 wiring: Classifier + Planner + retrieval + Analyst (Sonnet) + REAL
Verifier (Haiku rubric) + Opus retry on must_retry + deterministic
Formatter. All external services mocked at the LLM and ES boundaries.
"""
from __future__ import annotations

import json

import pytest

from app import _submit_meta, llm, pipeline, queue, retrieval, worker
from app.agents import analyst as analyst_agent
from app.agents import classifier as classifier_agent
from app.agents import planner as planner_agent
from app.agents import verifier as verifier_agent
from app.llm import LLMResult, LLMUsage
from app.models import JobStatus


# ── Canned agent outputs ──────────────────────────────────────────────────────

def _llm_result(parsed, *, input_tokens=200, output_tokens=80, cache_read=0):
    return LLMResult(
        text=json.dumps(parsed),
        parsed=parsed,
        usage=LLMUsage(input_tokens=input_tokens, output_tokens=output_tokens,
                       input_cache_read_tokens=cache_read),
        duration_ms=12,
        stop_reason="end_turn",
    )


CLASSIFIER_OUT = {
    "category": "license-error", "confidence": 0.82,
    "short_circuit": False, "short_circuit_reason": None,
    "short_circuit_payload": None,
    "tags": ["billing"], "language": "en",
}

PLANNER_OUT = {
    "queries": [
        {"index": "grp-manuals", "mode": "hybrid",
         "knn": {"field": "embedding", "k": 4, "num_candidates": 60},
         "lexical": {"must": [{"match": {"content": "license"}}]},
         "rerank": {"strategy": "mmr", "lambda": 0.6}, "max_chunks": 4},
        {"index": "rfs-tickets-*", "mode": "knn",
         "knn": {"field": "embedding", "k": 4, "num_candidates": 60},
         "rerank": {"strategy": "score_only"}, "max_chunks": 4},
    ],
    "rationale": "Manual + similar tickets.",
}

# Analyst output references the chunk_ids that the mocked ES returns below.
ANALYST_OUT = {
    "summary": "License cache stale after renewal.",
    "likely_cause": "Renewal flow does not invalidate the cached license state.",
    "recommended_actions": [
        {"step": 1, "detail": "Call POST /license/refresh to invalidate the cache.",
         "citations": ["grp-manuals::m1"]},
        {"step": 2, "detail": "If step 1 fails, restart the application service.",
         "citations": ["grp-manuals::m1", "rfs-tickets-mar-2025::t1"]},
    ],
    "claims": [
        {"id": "claim-1", "text": "The manual documents POST /license/refresh.",
         "supports_step": [1], "citations": ["grp-manuals::m1"]},
        {"id": "claim-2", "text": "LDG-90211 was resolved by restart.",
         "supports_step": [2], "citations": ["rfs-tickets-mar-2025::t1"]},
    ],
    "confidence": 0.78,
    "open_questions": [],
}


VERIFIER_PASS = {
    "verdict": "pass",
    "rubric_scores": {
        "every_claim_cited": True, "citations_resolve": True,
        "no_external_facts": True, "actions_actionable": True,
        "confidence_calibrated": True,
    },
    "flags": [], "must_retry_reason": None,
}

# Canned LLM-Formatter output that mirrors what the deterministic formatter
# would have produced for the default Analyst+chunks fixture.
FORMATTER_OUT = {
    "category": "license-error",
    "confidence": 0.78,
    "summary": "License cache stale after renewal.",
    "likely_cause": "Renewal flow does not invalidate the cached license state.",
    "recommended_actions": [
        {"step": 1, "detail": "Call POST /license/refresh to invalidate the cache.",
         "source_refs": ["cit-1"]},
        {"step": 2, "detail": "If step 1 fails, restart the application service.",
         "source_refs": ["cit-1", "cit-2"]},
    ],
    "citations": [
        {"id": "cit-1", "source": "manual",
         "locator": {"module": "Admin", "section": "License > Renewal",
                     "_index": "grp-manuals"},
         "snippet": "After renewal, call POST /license/refresh.",
         "score": 1.0},
        {"id": "cit-2", "source": "rfs_ticket",
         "locator": {"lodge_id": "LDG-90211",
                     "_index": "rfs-tickets-mar-2025"},
         "snippet": "License stuck after renewal. Resolved by restart.",
         "score": 1.0},
    ],
    "related_rfs": [
        {"lodge_id": "LDG-90211", "score": 1.0,
         "snippet": "License stuck after renewal. Resolved by restart."},
    ],
    "verifier_flags": [],
}


def _setup_mocks(monkeypatch, *,
                 classifier_out=None, planner_out=None,
                 analyst_out=None, verifier_out=None,
                 formatter_out=None):
    """Single dispatcher routes all 5 agent calls by prompt prefix.

    All agents go through `llm.call_agent_json`, which is the SAME module
    reference across modules — one monkeypatch suffices.
    """
    co = classifier_out if classifier_out is not None else CLASSIFIER_OUT
    po = planner_out if planner_out is not None else PLANNER_OUT
    ao = analyst_out if analyst_out is not None else ANALYST_OUT
    vo = verifier_out if verifier_out is not None else VERIFIER_PASS
    fo = formatter_out if formatter_out is not None else FORMATTER_OUT

    def dispatch(**kw):
        sp = kw.get("system_prompt", "")
        if sp.startswith("You are a support-ticket classifier"):
            return _llm_result(co, input_tokens=400, output_tokens=200)
        if sp.startswith("You are a retrieval-planner"):
            return _llm_result(po, input_tokens=700, output_tokens=500)
        if sp.startswith("You are a GRP / Acumatica ERP support analyst"):
            return _llm_result(ao, input_tokens=8000,
                               cache_read=5500, output_tokens=1500)
        if sp.startswith("You are a verifier"):
            return _llm_result(vo, input_tokens=800,
                               cache_read=2200, output_tokens=200)
        if sp.startswith("You are a formatter"):
            return _llm_result(fo, input_tokens=600,
                               cache_read=2400, output_tokens=900)
        raise AssertionError(f"unexpected system prompt: {sp[:60]!r}")
    monkeypatch.setattr(classifier_agent.llm, "call_agent_json", dispatch)

    def fake_es(index, body):
        if index == "grp-manuals":
            return {"hits": {"hits": [
                {"_index": "grp-manuals", "_id": "m1", "_score": 2.0,
                 "_source": {"module": "Admin", "section": "License > Renewal",
                             "content": "After renewal, call POST /license/refresh."}},
            ]}}
        return {"hits": {"hits": [
            {"_index": "rfs-tickets-mar-2025", "_id": "t1", "_score": 1.4,
             "_source": {"lodge_id": "LDG-90211",
                         "notes": "License stuck after renewal.",
                         "action_summary": "Resolved by restart"}},
        ]}}
    monkeypatch.setattr(retrieval, "_es_search", fake_es)
    monkeypatch.setattr(retrieval.embed, "embed_text", lambda t: [0.1] * 4)


# ── Tests ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_full_pipeline_produces_real_analysis(monkeypatch, client,
                                                   good_headers, sample_rfs):
    _setup_mocks(monkeypatch)

    job_id = client.post("/rfs/analyze", json=sample_rfs,
                         headers=good_headers).json()["job_id"]
    job = queue.dequeue(timeout_seconds=1)
    await worker._process_one(job)
    final = queue.get_job(job_id)

    assert final.status == JobStatus.succeeded
    a = final.result
    assert a is not None

    # Real Analyst content.
    assert "license cache" in a.summary.lower()
    # min(classifier=0.82, analyst=0.78) = 0.78
    assert a.confidence == 0.78

    # Two real recommended actions with resolved source_refs.
    assert len(a.recommended_actions) == 2
    assert a.recommended_actions[0].source_refs == ["cit-1"]
    assert set(a.recommended_actions[1].source_refs) == {"cit-1", "cit-2"}

    # Stable cit-N ordering — manual first (referenced first), ticket second.
    assert [c.id for c in a.citations] == ["cit-1", "cit-2"]
    assert a.citations[0].source.value == "manual"
    assert a.citations[1].source.value == "rfs_ticket"

    # related_rfs derived from retrieval, not from Analyst output.
    assert any(r.lodge_id == "LDG-90211" for r in a.related_rfs)

    # Trace: 5 agents in order + a trailing post-Formatter verifier review.
    agents = [s.agent.value for s in final.agent_trace]
    assert agents == ["classifier", "retrieval_planner",
                      "analyst", "verifier", "formatter", "verifier"]
    analyst_step = final.agent_trace[2]
    assert analyst_step.status.value == "ok"
    assert analyst_step.input_tokens == 8000
    assert analyst_step.input_cache_read_tokens == 5500


@pytest.mark.asyncio
async def test_analyst_retry_on_citation_violation(monkeypatch, client,
                                                  good_headers, sample_rfs):
    """Analyst returns invalid output first → retries → succeeds."""
    bad = dict(ANALYST_OUT)
    bad = json.loads(json.dumps(ANALYST_OUT))  # deep copy
    bad["recommended_actions"][0]["citations"] = []   # invalid

    counter = {"analyst_calls": 0}

    def dispatch(**kw):
        sp = kw.get("system_prompt", "")
        if sp.startswith("You are a support-ticket classifier"):
            return _llm_result(CLASSIFIER_OUT)
        if sp.startswith("You are a retrieval-planner"):
            return _llm_result(PLANNER_OUT)
        if sp.startswith("You are a GRP / Acumatica ERP support analyst"):
            counter["analyst_calls"] += 1
            return _llm_result(bad if counter["analyst_calls"] == 1
                               else ANALYST_OUT,
                               input_tokens=8000, output_tokens=1500)
        if sp.startswith("You are a verifier"):
            return _llm_result(VERIFIER_PASS)
        if sp.startswith("You are a formatter"):
            raise llm.LLMParseError("force det. fallback in this test")
        raise AssertionError(sp[:80])
    monkeypatch.setattr(classifier_agent.llm, "call_agent_json", dispatch)

    def fake_es(index, body):
        if index == "grp-manuals":
            return {"hits": {"hits": [
                {"_index": "grp-manuals", "_id": "m1", "_score": 2.0,
                 "_source": {"module": "A", "section": "L",
                             "content": "POST /license/refresh"}}]}}
        return {"hits": {"hits": [
            {"_index": "rfs-tickets-mar-2025", "_id": "t1", "_score": 1.0,
             "_source": {"lodge_id": "LDG-90211", "notes": "x"}}]}}
    monkeypatch.setattr(retrieval, "_es_search", fake_es)
    monkeypatch.setattr(retrieval.embed, "embed_text", lambda t: [0.1] * 4)

    job_id = client.post("/rfs/analyze", json=sample_rfs,
                         headers=good_headers).json()["job_id"]
    job = queue.dequeue(timeout_seconds=1)
    await worker._process_one(job)
    final = queue.get_job(job_id)

    assert final.status == JobStatus.succeeded
    analyst_step = next(s for s in final.agent_trace
                        if s.agent.value == "analyst")
    assert analyst_step.status.value == "retried"
    assert counter["analyst_calls"] == 2


@pytest.mark.asyncio
async def test_analyst_unsupported_fallback_still_produces_analysis(
    monkeypatch, client, good_headers, sample_rfs,
):
    """Analyst fails both attempts → pipeline still produces a valid Analysis
    with an unsupported_claim flag."""
    bad = json.loads(json.dumps(ANALYST_OUT))
    bad["recommended_actions"][0]["citations"] = []

    def dispatch(**kw):
        sp = kw.get("system_prompt", "")
        if sp.startswith("You are a support-ticket classifier"):
            return _llm_result(CLASSIFIER_OUT)
        if sp.startswith("You are a retrieval-planner"):
            return _llm_result(PLANNER_OUT)
        if sp.startswith("You are a GRP / Acumatica ERP support analyst"):
            return _llm_result(bad)
        if sp.startswith("You are a verifier"):
            return _llm_result(VERIFIER_PASS)
        if sp.startswith("You are a formatter"):
            raise llm.LLMParseError("force det. fallback in this test")
        raise AssertionError(sp[:80])
    monkeypatch.setattr(classifier_agent.llm, "call_agent_json", dispatch)

    def fake_es(index, body):
        if index == "grp-manuals":
            return {"hits": {"hits": [
                {"_index": "grp-manuals", "_id": "m1", "_score": 2.0,
                 "_source": {"module": "A", "section": "L",
                             "content": "POST /license/refresh"}}]}}
        return {"hits": {"hits": []}}
    monkeypatch.setattr(retrieval, "_es_search", fake_es)
    monkeypatch.setattr(retrieval.embed, "embed_text", lambda t: [0.1] * 4)

    job_id = client.post("/rfs/analyze", json=sample_rfs,
                         headers=good_headers).json()["job_id"]
    job = queue.dequeue(timeout_seconds=1)
    await worker._process_one(job)
    final = queue.get_job(job_id)

    assert final.status == JobStatus.succeeded
    a = final.result
    # unsupported_claim flag carried through
    flags = {f.kind.value for f in a.verifier_flags}
    assert "unsupported_claim" in flags
    # Still has at least one action (deterministic Formatter floor)
    assert len(a.recommended_actions) >= 1


@pytest.mark.asyncio
async def test_short_circuit_path_no_analyst(monkeypatch, client, good_headers,
                                             sample_rfs):
    short_circuit_out = {
        "category": "duplicate", "confidence": 0.95,
        "short_circuit": True,
        "short_circuit_reason": "near_duplicate_of_LDG-77",
        "short_circuit_payload": {
            "duplicate_of": "LDG-77",
            "suggested_response": "Same as LDG-77.",
        },
        "tags": ["duplicate"], "language": "en",
    }
    def dispatch(**kw):
        sp = kw.get("system_prompt", "")
        if sp.startswith("You are a support-ticket classifier"):
            return _llm_result(short_circuit_out)
        if sp.startswith("You are a GRP / Acumatica ERP support analyst"):
            raise AssertionError("Analyst should be skipped")
        if sp.startswith("You are a retrieval-planner"):
            raise AssertionError("Planner should be skipped")
        raise AssertionError(sp[:80])
    monkeypatch.setattr(classifier_agent.llm, "call_agent_json", dispatch)
    def es_called(index, body):
        raise AssertionError("ES should be skipped on short_circuit")
    monkeypatch.setattr(retrieval, "_es_search", es_called)

    job_id = client.post("/rfs/analyze", json=sample_rfs,
                         headers=good_headers).json()["job_id"]
    job = queue.dequeue(timeout_seconds=1)
    await worker._process_one(job)
    final = queue.get_job(job_id)

    assert final.status == JobStatus.succeeded
    assert final.result.category == "duplicate"
    assert any(c.locator.get("lodge_id") == "LDG-77"
               for c in final.result.citations)


@pytest.mark.asyncio
async def test_pipeline_uses_real_submitted_rfs(monkeypatch, client, good_headers):
    """Worker rehydrates the real RFS body submitted by the caller, and the
    Analyst sees the real notes (not a fabricated stub)."""
    seen_payloads = []
    def dispatch(**kw):
        sp = kw.get("system_prompt", "")
        seen_payloads.append((sp, kw.get("user_payload")))
        if sp.startswith("You are a support-ticket classifier"):
            return _llm_result(CLASSIFIER_OUT)
        if sp.startswith("You are a retrieval-planner"):
            return _llm_result(PLANNER_OUT)
        if sp.startswith("You are a GRP / Acumatica ERP support analyst"):
            return _llm_result(ANALYST_OUT)
        if sp.startswith("You are a verifier"):
            return _llm_result(VERIFIER_PASS)
        if sp.startswith("You are a formatter"):
            raise llm.LLMParseError("force det. fallback in this test")
        raise AssertionError(sp[:80])
    monkeypatch.setattr(classifier_agent.llm, "call_agent_json", dispatch)
    monkeypatch.setattr(retrieval, "_es_search",
                        lambda i, b: {"hits": {"hits": [
                            {"_index": "grp-manuals" if i == "grp-manuals"
                                       else "rfs-tickets-mar-2025",
                             "_id": "m1" if i == "grp-manuals" else "t1",
                             "_score": 1.0,
                             "_source": {"module": "A", "section": "L",
                                         "content": "x",
                                         "lodge_id": "LDG-90211",
                                         "notes": "y"}}]}})
    monkeypatch.setattr(retrieval.embed, "embed_text", lambda t: [0.1] * 4)

    payload = {"rfs": {"lodge_id": "LDG-REHYDRATE",
                       "notes": "This is the real notes the caller sent.",
                       "relatedarea": "Licensing"}}
    job_id = client.post("/rfs/analyze", json=payload,
                         headers=good_headers).json()["job_id"]

    meta = _submit_meta.load_submit_meta(job_id)
    assert meta["rfs"]["notes"].startswith("This is the real notes")

    job = queue.dequeue(timeout_seconds=1)
    await worker._process_one(job)
    final = queue.get_job(job_id)
    assert final.status == JobStatus.succeeded

    # The Analyst saw the rehydrated lodge_id, not a fabricated one.
    analyst_payloads = [
        p for sp, p in seen_payloads
        if sp.startswith("You are a GRP / Acumatica ERP support analyst")
    ]
    assert analyst_payloads, "Analyst was never called"
    analyst_payload = analyst_payloads[0]
    assert analyst_payload["rfs"]["lodge_id"] == "LDG-REHYDRATE"
    assert analyst_payload["rfs"]["notes"].startswith("This is the real notes")


# ── W8: Verifier flag / must_retry paths ──────────────────────────────────────

VERIFIER_FLAG = {
    "verdict": "flag",
    "rubric_scores": {
        "every_claim_cited": True, "citations_resolve": True,
        "no_external_facts": True, "actions_actionable": False,
        "confidence_calibrated": True,
    },
    "flags": [{"kind": "weak_citation",
               "detail": "action 1 cites only one chunk"}],
    "must_retry_reason": None,
}


def _verifier_must_retry(reason="claim-1 unsupported by cited chunk"):
    return {
        "verdict": "must_retry",
        "rubric_scores": {
            "every_claim_cited": True, "citations_resolve": True,
            "no_external_facts": False, "actions_actionable": True,
            "confidence_calibrated": True,
        },
        "flags": [],
        "must_retry_reason": reason,
    }


@pytest.mark.asyncio
async def test_verifier_flag_passes_through_to_analysis(
    monkeypatch, client, good_headers, sample_rfs,
):
    """Verifier=flag → Formatter receives the flags, public Analysis carries them."""
    _setup_mocks(monkeypatch, verifier_out=VERIFIER_FLAG)

    job_id = client.post("/rfs/analyze", json=sample_rfs,
                         headers=good_headers).json()["job_id"]
    job = queue.dequeue(timeout_seconds=1)
    await worker._process_one(job)
    final = queue.get_job(job_id)

    assert final.status == JobStatus.succeeded
    kinds = {f.kind.value for f in final.result.verifier_flags}
    assert "weak_citation" in kinds
    verifier_step = next(s for s in final.agent_trace
                         if s.agent.value == "verifier")
    assert verifier_step.status.value == "ok"
    assert "verdict=flag" in (verifier_step.note or "")


@pytest.mark.asyncio
async def test_must_retry_triggers_opus_then_succeeds(
    monkeypatch, client, good_headers, sample_rfs,
):
    """First Verifier=must_retry, Opus Analyst runs, second Verifier=pass."""
    analyst_models_seen: list[str] = []
    verifier_calls = {"n": 0}

    def dispatch(**kw):
        sp = kw.get("system_prompt", "")
        if sp.startswith("You are a support-ticket classifier"):
            return _llm_result(CLASSIFIER_OUT)
        if sp.startswith("You are a retrieval-planner"):
            return _llm_result(PLANNER_OUT)
        if sp.startswith("You are a GRP / Acumatica ERP support analyst"):
            analyst_models_seen.append(kw["model"])
            return _llm_result(ANALYST_OUT, input_tokens=8000,
                               output_tokens=1500)
        if sp.startswith("You are a verifier performing"):
            return _llm_result({"flags": []})   # post-Formatter final review
        if sp.startswith("You are a verifier"):
            verifier_calls["n"] += 1
            if verifier_calls["n"] == 1:
                return _llm_result(_verifier_must_retry())
            return _llm_result(VERIFIER_PASS)
        if sp.startswith("You are a formatter"):
            raise llm.LLMParseError("force det. fallback in this test")
        raise AssertionError(sp[:80])
    monkeypatch.setattr(classifier_agent.llm, "call_agent_json", dispatch)

    def fake_es(index, body):
        if index == "grp-manuals":
            return {"hits": {"hits": [
                {"_index": "grp-manuals", "_id": "m1", "_score": 2.0,
                 "_source": {"module": "A", "section": "L",
                             "content": "POST /license/refresh"}}]}}
        return {"hits": {"hits": [
            {"_index": "rfs-tickets-mar-2025", "_id": "t1", "_score": 1.0,
             "_source": {"lodge_id": "LDG-90211", "notes": "x"}}]}}
    monkeypatch.setattr(retrieval, "_es_search", fake_es)
    monkeypatch.setattr(retrieval.embed, "embed_text", lambda t: [0.1] * 4)

    job_id = client.post("/rfs/analyze", json=sample_rfs,
                         headers=good_headers).json()["job_id"]
    job = queue.dequeue(timeout_seconds=1)
    await worker._process_one(job)
    final = queue.get_job(job_id)

    assert final.status == JobStatus.succeeded
    # Two Analyst calls: Sonnet then Opus.
    assert analyst_models_seen == [analyst_agent.MODEL_DEFAULT,
                                   analyst_agent.MODEL_RETRY]
    # Two Verifier calls: must_retry then pass.
    assert verifier_calls["n"] == 2

    # Trace shape: classifier, planner, analyst (sonnet), verifier(must_retry),
    # analyst (opus), verifier(pass), formatter, verifier(final review).
    agents = [s.agent.value for s in final.agent_trace]
    assert agents == ["classifier", "retrieval_planner",
                      "analyst", "verifier",
                      "analyst", "verifier",
                      "formatter", "verifier"]
    assert final.agent_trace[2].model == analyst_agent.MODEL_DEFAULT
    assert final.agent_trace[3].status.value == "retried"
    assert final.agent_trace[4].model == analyst_agent.MODEL_RETRY


@pytest.mark.asyncio
async def test_must_retry_twice_degrades_to_flag(
    monkeypatch, client, good_headers, sample_rfs,
):
    """Both Verifier calls return must_retry → pipeline degrades to flag with
    low_confidence and proceeds (never blocks)."""
    _setup_mocks(monkeypatch, verifier_out=_verifier_must_retry())

    job_id = client.post("/rfs/analyze", json=sample_rfs,
                         headers=good_headers).json()["job_id"]
    job = queue.dequeue(timeout_seconds=1)
    await worker._process_one(job)
    final = queue.get_job(job_id)

    assert final.status == JobStatus.succeeded
    kinds = {f.kind.value for f in final.result.verifier_flags}
    assert "low_confidence" in kinds
    detail_joined = " ".join(f.detail for f in final.result.verifier_flags)
    assert "Opus retry could not satisfy rubric" in detail_joined


@pytest.mark.asyncio
async def test_verifier_soft_fail_does_not_block(
    monkeypatch, client, good_headers, sample_rfs,
):
    """Verifier itself returns garbage → soft-fail to flag+low_confidence,
    pipeline still succeeds. No Opus retry should fire."""
    opus_called = {"n": 0}

    def dispatch(**kw):
        sp = kw.get("system_prompt", "")
        if sp.startswith("You are a support-ticket classifier"):
            return _llm_result(CLASSIFIER_OUT)
        if sp.startswith("You are a retrieval-planner"):
            return _llm_result(PLANNER_OUT)
        if sp.startswith("You are a GRP / Acumatica ERP support analyst"):
            if kw["model"] == analyst_agent.MODEL_RETRY:
                opus_called["n"] += 1
            return _llm_result(ANALYST_OUT, input_tokens=8000,
                               output_tokens=1500)
        if sp.startswith("You are a verifier"):
            raise llm.LLMParseError("verifier nonsense")
        if sp.startswith("You are a formatter"):
            raise llm.LLMParseError("force det. fallback in this test")
        raise AssertionError(sp[:80])
    monkeypatch.setattr(classifier_agent.llm, "call_agent_json", dispatch)

    def fake_es(index, body):
        if index == "grp-manuals":
            return {"hits": {"hits": [
                {"_index": "grp-manuals", "_id": "m1", "_score": 2.0,
                 "_source": {"module": "A", "section": "L",
                             "content": "x"}}]}}
        return {"hits": {"hits": [
            {"_index": "rfs-tickets-mar-2025", "_id": "t1", "_score": 1.0,
             "_source": {"lodge_id": "LDG-90211", "notes": "y"}}]}}
    monkeypatch.setattr(retrieval, "_es_search", fake_es)
    monkeypatch.setattr(retrieval.embed, "embed_text", lambda t: [0.1] * 4)

    job_id = client.post("/rfs/analyze", json=sample_rfs,
                         headers=good_headers).json()["job_id"]
    job = queue.dequeue(timeout_seconds=1)
    await worker._process_one(job)
    final = queue.get_job(job_id)

    assert final.status == JobStatus.succeeded
    # Verifier soft-fail → its verdict was 'flag' (degraded), not must_retry,
    # so Opus must NOT have been called.
    assert opus_called["n"] == 0
    kinds = {f.kind.value for f in final.result.verifier_flags}
    assert "low_confidence" in kinds


# ── W9: LLM Formatter happy path + deterministic fallback ─────────────────────

@pytest.mark.asyncio
async def test_llm_formatter_runs_and_marked_ok(monkeypatch, client,
                                                good_headers, sample_rfs):
    """When the LLM Formatter returns a valid Analysis, it's used directly
    and the trace step is `ok` with non-zero token usage."""
    _setup_mocks(monkeypatch)

    job_id = client.post("/rfs/analyze", json=sample_rfs,
                         headers=good_headers).json()["job_id"]
    job = queue.dequeue(timeout_seconds=1)
    await worker._process_one(job)
    final = queue.get_job(job_id)

    assert final.status == JobStatus.succeeded
    fmt = next(s for s in final.agent_trace if s.agent.value == "formatter")
    assert fmt.status.value == "ok"
    assert fmt.model.startswith("claude-haiku")
    assert fmt.input_tokens == 600
    # FORMATTER_OUT specifies category, summary etc.
    assert final.result.summary.startswith("License cache stale after renewal")


@pytest.mark.asyncio
async def test_llm_formatter_falls_back_to_deterministic(monkeypatch, client,
                                                       good_headers, sample_rfs):
    """LLM Formatter returns invalid output twice → pipeline falls back to
    deterministic Formatter and still produces a schema-valid Analysis."""
    # Make the LLM Formatter return an orphan source_ref both attempts.
    bad_fmt = {
        **FORMATTER_OUT,
        "recommended_actions": [
            {"step": 1, "detail": "Call POST /license/refresh.",
             "source_refs": ["cit-99"]},   # orphan, fails cross-check
        ],
    }
    _setup_mocks(monkeypatch, formatter_out=bad_fmt)

    job_id = client.post("/rfs/analyze", json=sample_rfs,
                         headers=good_headers).json()["job_id"]
    job = queue.dequeue(timeout_seconds=1)
    await worker._process_one(job)
    final = queue.get_job(job_id)

    assert final.status == JobStatus.succeeded
    fmt = next(s for s in final.agent_trace if s.agent.value == "formatter")
    assert fmt.status.value == "failed"
    assert "fallback" in fmt.model
    assert "llm_formatter_failed" in (fmt.note or "")
    # Deterministic fallback still produces a valid Analysis with cit-N ids.
    assert len(final.result.citations) >= 1
    assert all(r.startswith("cit-") for a in final.result.recommended_actions
               for r in a.source_refs)


@pytest.mark.asyncio
async def test_llm_formatter_backfills_dropped_verifier_flags(
    monkeypatch, client, good_headers, sample_rfs,
):
    """If the LLM Formatter forgets a verifier flag, the pipeline backfills it."""
    verifier_with_flag = {
        **VERIFIER_FLAG,
        "flags": [{"kind": "weak_citation",
                   "detail": "action 1 cites only one chunk"}],
    }
    # FORMATTER_OUT has empty verifier_flags — simulates the LLM dropping it.
    _setup_mocks(monkeypatch, verifier_out=verifier_with_flag,
                 formatter_out=FORMATTER_OUT)

    job_id = client.post("/rfs/analyze", json=sample_rfs,
                         headers=good_headers).json()["job_id"]
    job = queue.dequeue(timeout_seconds=1)
    await worker._process_one(job)
    final = queue.get_job(job_id)

    assert final.status == JobStatus.succeeded
    kinds = {f.kind.value for f in final.result.verifier_flags}
    # The flag was backfilled even though the LLM Formatter dropped it.
    assert "weak_citation" in kinds


@pytest.mark.asyncio
async def test_short_circuit_skips_llm_formatter(monkeypatch, client,
                                                good_headers, sample_rfs):
    """LLM Formatter must NOT be called on the short-circuit path —
    deterministic format_short_circuit handles it."""
    short_circuit_out = {
        "category": "duplicate", "confidence": 0.95, "short_circuit": True,
        "short_circuit_reason": "near_duplicate_of_LDG-77",
        "short_circuit_payload": {
            "duplicate_of": "LDG-77",
            "suggested_response": "Same as LDG-77.",
        },
        "tags": ["duplicate"], "language": "en",
    }
    def dispatch(**kw):
        sp = kw.get("system_prompt", "")
        if sp.startswith("You are a support-ticket classifier"):
            return _llm_result(short_circuit_out)
        if sp.startswith("You are a formatter"):
            raise AssertionError("LLM Formatter should be skipped on short-circuit")
        raise AssertionError(sp[:80])
    monkeypatch.setattr(classifier_agent.llm, "call_agent_json", dispatch)

    job_id = client.post("/rfs/analyze", json=sample_rfs,
                         headers=good_headers).json()["job_id"]
    job = queue.dequeue(timeout_seconds=1)
    await worker._process_one(job)
    final = queue.get_job(job_id)
    assert final.status == JobStatus.succeeded
    fmt = next(s for s in final.agent_trace if s.agent.value == "formatter")
    assert "short-circuit" in fmt.model
