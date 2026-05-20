# Agent 1 — Classifier

## Purpose

Assign the RFS to a category and decide whether the pipeline can short-circuit (return a canned/cached answer without running Analyst).

## Model

`claude-haiku-4-5`

## Input

```jsonc
{
  "rfs": { /* RFS shape from openapi.yaml */ },
  "duplicate_candidates": [
    // Optional: top-3 lexical-match RFS_ticket lodge_ids the orchestrator
    // pre-fetched in <50ms. Empty array if none.
    { "lodge_id": "LDG-12345", "score": 0.91, "snippet": "..." }
  ]
}
```

## Output

```jsonc
{
  "category": "license-error",
  "confidence": 0.86,
  "short_circuit": false,
  "short_circuit_reason": null,
  "short_circuit_payload": null,
  "tags": ["billing", "renewal"],
  "language": "en"
}
```

When `short_circuit = true`:

```jsonc
{
  "category": "duplicate",
  "confidence": 0.97,
  "short_circuit": true,
  "short_circuit_reason": "near_duplicate_of_LDG-12345",
  "short_circuit_payload": {
    "duplicate_of": "LDG-12345",
    "suggested_response": "This is a duplicate of LDG-12345. See that ticket's resolution."
  },
  "tags": ["duplicate"],
  "language": "en"
}
```

## Category enum (v1)

`license-error`, `data-import`, `report-generation`, `permissions`, `integration-failure`, `performance`, `ui-bug`, `data-corruption`, `feature-request`, `how-to`, `duplicate`, `other`.

Adding a category requires updating this contract + the cached system prompt.

## Short-circuit rules

1. **Duplicate** — `duplicate_candidates[0].score ≥ 0.88` AND notes overlap (judged by the agent). Set `category="duplicate"`.
2. **Trivial how-to** — RFS notes < 200 chars AND notes match a known how-to pattern (e.g., "where do I find X"). Set `category="how-to"`, payload includes a direct doc link if Analyst would have cited it anyway.
3. **Spam/empty** — notes have no question-like content. Set `category="other"`, `short_circuit=true`, payload = `{"reason": "no actionable content"}`.

Target short-circuit rate: 5% of traffic (per plan cost model). Alarm if > 20% (likely over-aggressive).

## System prompt (cached)

```
You are a support-ticket classifier. Output JSON only, matching the schema
in this prompt. Categories: [license-error, data-import, ...].

Short-circuit rules:
  - If duplicate_candidates[0].score >= 0.88 and the new notes are semantically
    near-identical to its snippet, return category=duplicate, short_circuit=true,
    short_circuit_payload.duplicate_of=<that lodge_id>.
  - If notes < 200 chars AND match one of the canonical how-to patterns,
    return category=how-to with short_circuit=true and a direct answer.
  - Otherwise short_circuit=false.

Do not invent facts. Do not include explanations outside the JSON.
```

(Examples + full enum included in actual prompt. Kept cached.)

## Error modes

| Code | Retryable | Cause |
|---|---|---|
| `bad_input` | no | RFS missing required fields (orchestrator should catch first). |
| `model_overloaded` | yes | 529 from Anthropic. |
| `output_unparseable` | yes (once) | Agent returned non-JSON. Retry with stricter system prompt suffix. |

## Caching boundary

Cached prefix: system prompt + category enum + 5 short-circuit exemplars (~1,100 tokens).
Dynamic suffix: RFS notes, relatedarea, duplicate_candidates (~400 tokens).
