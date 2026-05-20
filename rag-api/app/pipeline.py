"""5-agent pipeline orchestrator.

Status by week:
  W3-W4: stub end-to-end.
  W5-W6: real Classifier + Retrieval Planner + ES retrieval.
  W7   : real Analyst (Sonnet) + deterministic Formatter.
         Verifier still stubbed (always pass) — real Verifier lands W8.
         LLM Formatter lands W9; the deterministic one becomes the fallback.

Short-circuit path: Classifier short_circuit=true bypasses Planner / retrieval
/ Analyst / Verifier; deterministic Formatter builds the Analysis directly
from the Classifier's payload.
"""
from __future__ import annotations

import asyncio
import logging

from . import retrieval
from .deps import get_config
from .agents import analyst as analyst_agent
from .agents import classifier as classifier_agent
from .agents import formatter as llm_formatter
from .agents import formatter_deterministic as det_formatter
from .agents import planner as planner_agent
from .agents import verifier as verifier_agent
from .models import (
    AgentName,
    AgentStep,
    AgentStepStatus,
    Analysis,
    Job,
    RFS,
    Usage,
    VerifierFlag,
    VerifierFlagKind,
)

log = logging.getLogger("rag-api.pipeline")


PIPELINE_VERSION = "0.5.0-formatter"


async def run_pipeline(job: Job, rfs: RFS) -> tuple[Analysis, list[AgentStep], Usage]:
    log.info('"pipeline.start job=%s lodge=%s"', job.job_id, rfs.lodge_id)
    trace: list[AgentStep] = []
    usage = Usage()

    # ── 1. Classifier ─────────────────────────────────────────────────────────
    cls_out, cls_step = await asyncio.to_thread(classifier_agent.classify, rfs)
    trace.append(cls_step)
    _add_usage(usage, cls_step)

    if cls_out.short_circuit and not get_config().short_circuit_enabled:
        log.info('"pipeline.short_circuit_overridden reason=%s — running full pipeline"',
                 cls_out.short_circuit_reason)

    if cls_out.short_circuit and get_config().short_circuit_enabled:
        log.info('"pipeline.short_circuit reason=%s"', cls_out.short_circuit_reason)
        analysis = det_formatter.format_short_circuit(cls_out)
        trace.extend(_skipped_steps_for_short_circuit())
        usage.estimated_cost_rm = _estimate_cost_rm(usage)
        return analysis, trace, usage

    # ── 2. Retrieval Planner ──────────────────────────────────────────────────
    plan_out, plan_step = await asyncio.to_thread(planner_agent.plan, rfs, cls_out)
    trace.append(plan_step)
    _add_usage(usage, plan_step)

    # ── 3. Execute retrieval ──────────────────────────────────────────────────
    embed_query = (rfs.relatedarea or rfs.notes)[:400]
    chunks, debug = await asyncio.to_thread(
        retrieval.execute_plan, plan_out.queries, embed_query=embed_query,
    )
    log.info('"retrieval.done queries=%d raw=%d kept=%d"',
             debug.queries_run, debug.raw_hits, debug.after_cap)

    # ── 4. Analyst (REAL — Sonnet) ───────────────────────────────────────────
    analyst_out, analyst_step = await asyncio.to_thread(
        analyst_agent.analyze, rfs, cls_out, chunks,
    )
    trace.append(analyst_step)
    _add_usage(usage, analyst_step)

    # ── 5. Verifier (REAL — Haiku rubric, may trigger Opus retry) ────────────
    verifier_out, verifier_step = await asyncio.to_thread(
        verifier_agent.verify, analyst_out, chunks, cls_out.category,
    )
    trace.append(verifier_step)
    _add_usage(usage, verifier_step)

    # If the Verifier demanded a retry, run the Analyst again on Opus and
    # re-verify ONCE. If the second pass still fails, degrade to flag with
    # low_confidence — never block the pipeline.
    if verifier_out.verdict == verifier_agent.VERDICT_MUST_RETRY:
        log.info('"pipeline.opus_retry reason=%s"', verifier_out.must_retry_reason)
        opus_out, opus_step = await asyncio.to_thread(
            analyst_agent.retry_with_opus, rfs, cls_out, chunks,
            verifier_out.must_retry_reason or "verifier requested retry",
        )
        trace.append(opus_step)
        _add_usage(usage, opus_step)

        reverify_out, reverify_step = await asyncio.to_thread(
            verifier_agent.verify, opus_out, chunks, cls_out.category,
        )
        trace.append(reverify_step)
        _add_usage(usage, reverify_step)

        if reverify_out.verdict != verifier_agent.VERDICT_MUST_RETRY:
            # Opus retry accepted — use its output downstream.
            analyst_out = opus_out
            verifier_out = reverify_out
        else:
            # Still failing — degrade to flag, keep the FIRST Analyst output
            # (Opus output may also be unsupported; first one was already
            # accepted by Analyst's own validator).
            log.warning('"pipeline.opus_retry_still_failed — degrading to flag"')
            verifier_out = _degrade_to_flag(reverify_out)

    # ── 6. Formatter (REAL — LLM Haiku, falls back to deterministic) ─────────
    try:
        analysis, formatter_step = await asyncio.to_thread(
            llm_formatter.format_via_llm,
            cls_out, analyst_out, verifier_out, chunks,
        )
    except llm_formatter.FormatterError as e:
        log.warning('"pipeline.formatter_fallback err=%s"', e)
        analysis = det_formatter.format_analysis(cls_out, analyst_out, chunks)
        formatter_step = AgentStep(
            agent=AgentName.formatter, model="deterministic (fallback)",
            status=AgentStepStatus.failed, duration_ms=0,
            input_tokens=0, input_cache_read_tokens=0, output_tokens=0,
            note=f"llm_formatter_failed: {e}",
        )
    trace.append(formatter_step)
    _add_usage(usage, formatter_step)

    # ── 7. Final review — re-verify the FINAL Analysis so verifier flags
    #     reference its own step numbers / cit-ids. The pre-format Verifier
    #     saw the Analyst draft; the Formatter may have renumbered actions,
    #     leaving the original flags pointing at steps that no longer exist.
    final_flags, reverify_step = await asyncio.to_thread(
        verifier_agent.verify_analysis, analysis, chunks, cls_out.category,
    )
    trace.append(reverify_step)
    _add_usage(usage, reverify_step)

    # Carry the flags the final review cannot derive from the Analysis alone:
    # pipeline-level low_confidence / retrieval_gap (Verifier soft-fail or
    # Opus-retry degrade) and the Analyst's own unsupported-claim self-flag.
    carried: list[VerifierFlag] = [
        f for f in verifier_out.flags
        if f.kind in (VerifierFlagKind.low_confidence,
                      VerifierFlagKind.retrieval_gap)
    ]
    if analyst_out.unsupported_flag:
        carried.append(VerifierFlag(
            kind=VerifierFlagKind.unsupported_claim,
            detail=f"Analyst self-flagged: {analyst_out.unsupported_flag}"[:400],
        ))
    if analyst_out.confidence < 0.20:
        carried.append(VerifierFlag(
            kind=VerifierFlagKind.low_confidence,
            detail=f"Analyst confidence={analyst_out.confidence:.2f}",
        ))
    analysis = analysis.model_copy(
        update={"verifier_flags": _merge_flags(final_flags, carried)})

    usage.estimated_cost_rm = _estimate_cost_rm(usage)
    log.info('"pipeline.done job=%s cost_rm=%.4f chunks=%d analyst_status=%s"',
             job.job_id, usage.estimated_cost_rm, len(chunks),
             analyst_step.status.value)
    return analysis, trace, usage


# ── Helpers ───────────────────────────────────────────────────────────────────

def _merge_flags(primary: list[VerifierFlag],
                 extra: list[VerifierFlag]) -> list[VerifierFlag]:
    """Concatenate two flag lists, de-duping by (kind, detail)."""
    seen = {(f.kind, f.detail) for f in primary}
    out = list(primary)
    for f in extra:
        key = (f.kind, f.detail)
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


def _degrade_to_flag(v):
    """Convert a still-failing must_retry verdict into a flag with
    low_confidence. Preserves any flags the Verifier already attached."""
    flags = list(v.flags)
    if not any(f.kind == VerifierFlagKind.low_confidence for f in flags):
        flags.append(VerifierFlag(
            kind=VerifierFlagKind.low_confidence,
            detail=(f"Opus retry could not satisfy rubric: "
                    f"{v.must_retry_reason or 'unknown'}")[:400],
        ))
    return verifier_agent.VerifierOutput(
        verdict=verifier_agent.VERDICT_FLAG,
        rubric_scores=v.rubric_scores,
        flags=flags,
        must_retry_reason=None,
    )


def _skipped_steps_for_short_circuit() -> list[AgentStep]:
    return [
        AgentStep(agent=AgentName.retrieval_planner,
                  model="(skipped)", status=AgentStepStatus.ok,
                  duration_ms=0, note="skipped: short-circuit"),
        AgentStep(agent=AgentName.analyst,
                  model="(skipped)", status=AgentStepStatus.ok,
                  duration_ms=0, note="skipped: short-circuit"),
        AgentStep(agent=AgentName.verifier,
                  model="(skipped)", status=AgentStepStatus.ok,
                  duration_ms=0, note="skipped: short-circuit"),
        AgentStep(agent=AgentName.formatter,
                  model="deterministic (short-circuit)",
                  status=AgentStepStatus.ok,
                  duration_ms=0, note="short-circuit"),
    ]


def _add_usage(u: Usage, s: AgentStep) -> None:
    u.input_tokens += s.input_tokens or 0
    u.input_cache_read_tokens += s.input_cache_read_tokens or 0
    u.output_tokens += s.output_tokens or 0


def _estimate_cost_rm(u: Usage) -> float:
    USD_TO_RM = 4.7
    sonnet_in  = 3.0 * USD_TO_RM / 1_000_000
    sonnet_out = 15.0 * USD_TO_RM / 1_000_000
    haiku_in   = 1.0 * USD_TO_RM / 1_000_000
    haiku_out  = 5.0 * USD_TO_RM / 1_000_000
    cache_factor = 0.10

    in_total = u.input_tokens + u.input_cache_read_tokens * cache_factor
    cost = (in_total * 0.75 * sonnet_in
            + in_total * 0.25 * haiku_in
            + u.output_tokens * 0.75 * sonnet_out
            + u.output_tokens * 0.25 * haiku_out)
    return round(cost, 4)
