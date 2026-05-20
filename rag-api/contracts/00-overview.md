# Pipeline overview

Five agents in a fixed order. Each agent reads JSON from the prior step plus retrieval context where relevant, writes JSON to the next.

```
RFS  →  Classifier  →  Retrieval Planner  →  Analyst  →  Verifier  →  Formatter  →  Analysis
        (haiku)        (haiku)              (sonnet)   (haiku)     (haiku)
        labels RFS     builds ES queries    reasons,   checks      shapes
                      + rerank knobs       cites      claims      Analysis JSON
                                                       + flags
```

## Models

| Agent | Model | Why |
|---|---|---|
| Classifier        | `claude-haiku-4-5` | Cheap labelling. Confidence routing. |
| Retrieval Planner | `claude-haiku-4-5` | Light planning, no reasoning load. |
| Analyst           | `claude-sonnet-4-6` | Heaviest reasoning over retrieved context. Cached system prompt. |
| Verifier          | `claude-haiku-4-5` | Structural check + citation lookup. |
| Formatter         | `claude-haiku-4-5` | Schema-shaping. JSON-only output. |

`claude-opus-4-7` reserved for one-shot retry on Analyst when Verifier flags `low_confidence` AND `confidence < 0.4`. Capped at 2% of traffic — alarm if exceeded.

## Shared types

All agents speak the same RFS shape (see `RFS` in `openapi.yaml`) and the same `Citation` shape. Internal agent IO uses these field names verbatim — no renaming between steps.

```
RFS               ⇢ defined in openapi.yaml#/components/schemas/RFS
Citation          ⇢ defined in openapi.yaml#/components/schemas/Citation
Analysis          ⇢ defined in openapi.yaml#/components/schemas/Analysis
```

## Pipeline contract (orchestrator-side)

1. Orchestrator receives RFS.
2. Calls Classifier.
3. Calls Retrieval Planner. Gets list of ES query plans (kNN seeds + lexical queries + filters).
4. Orchestrator executes the plans against ES, deduplicates results, packs top-K chunks into `retrieved_context`.
5. Calls Analyst with `retrieved_context`. Gets draft analysis + claim→citation map.
6. Calls Verifier. If `must_retry`, re-run Analyst once (with verifier's feedback) using `claude-opus-4-7`. If still failing, set `verifier_flags` and proceed.
7. Calls Formatter. Produces the final `Analysis` JSON.
8. Orchestrator writes job result. If `callback_url`, POSTs signed webhook.

## Cache boundaries

Anthropic prompt caching: split each agent prompt into `cached_prefix` (stable across requests) and `dynamic_suffix` (the RFS-specific bit). Mark the cached_prefix with `cache_control: ephemeral`.

- **Classifier** — cached prefix = system prompt + category enum + examples. Dynamic = RFS notes + relatedarea.
- **Retrieval Planner** — cached prefix = system prompt + ES schema description + index list. Dynamic = classifier output + RFS metadata.
- **Analyst** — cached prefix = system prompt + JSON output schema + 3 canonical exemplars. Dynamic = RFS + retrieved_context (rebuilt every call, **not** cached).
- **Verifier** — cached prefix = system prompt + verification rubric. Dynamic = analyst output + retrieved_context refs.
- **Formatter** — cached prefix = system prompt + Analysis JSON schema. Dynamic = upstream agents' outputs.

Target cache-hit rate (read tokens / total input tokens) ≥ 70% steady state. Measure per agent.

## Error handling

Each agent returns either a success envelope or an error envelope:

```jsonc
// Success
{ "ok": true, "data": { /* agent-specific */ }, "meta": { "duration_ms": 123, "tokens": {...} } }

// Error
{ "ok": false, "error": { "code": "...", "message": "...", "retryable": true|false } }
```

Orchestrator policy:
- `retryable=true` → exponential backoff up to 2 retries.
- `retryable=false` → fail the job, surface error in `Job.error`.
- Hitting `MONTHLY_TOKEN_BUDGET` → fail with `budget_exhausted`.

## Token budget (per-RFS, target)

| Agent | Input uncached | Input cache-read | Output |
|---|---|---|---|
| Classifier        |   400 |  1,100 |   200 |
| Retrieval Planner |   700 |  1,300 |   500 |
| Analyst           | 4,500 |  5,500 | 2,000 |
| Verifier          |   800 |  2,200 |   300 |
| Formatter         |   600 |  2,400 | 1,000 |

These are the budget assumptions in `plan/build_combined_excel.py` (`AGENTS`). Effective per-RFS cost ≈ RM 0.27 cached, RM 0.45 uncached. If real load shows ≥20% over budget, revisit prompts before scaling traffic.

## Observability

Every agent step writes one structured log line + one ES doc in `rag-api-trace-*`:

```jsonc
{
  "job_id": "...", "agent": "analyst", "model": "claude-sonnet-4-6",
  "status": "ok", "duration_ms": 1840,
  "tokens": { "input": 4501, "input_cache_read": 5498, "output": 1923 },
  "cache_hit_pct": 0.55,
  "ts": "2026-05-19T07:00:01Z"
}
```

Aggregated into the same Grafana dashboard as grp-api.
