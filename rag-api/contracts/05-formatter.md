# Agent 5 — Formatter

## Purpose

Shape the Analyst + Verifier outputs into the final `Analysis` JSON exposed in `openapi.yaml`. Resolves internal `chunk_id` references into proper `Citation` objects. Trims overlong fields. Carries Verifier flags through. Single source of truth for what the caller sees.

## Model

`claude-haiku-4-5`

## Input

```jsonc
{
  "rfs_lodge_id": "LDG-44021",
  "classifier_output": { /* full output */ },
  "analyst_output": { /* full output */ },
  "verifier_output": { /* full output */ },
  "retrieved_context": [ /* same as Analyst */ ],
  "short_circuit_payload": null
}
```

When the Classifier short-circuited, the Formatter receives:

```jsonc
{
  "rfs_lodge_id": "LDG-44021",
  "classifier_output": { /* with short_circuit=true */ },
  "analyst_output": null,
  "verifier_output": null,
  "retrieved_context": [],
  "short_circuit_payload": { "duplicate_of": "LDG-12345", "suggested_response": "..." }
}
```

## Output

Final `Analysis` JSON exactly as defined in `openapi.yaml#/components/schemas/Analysis`.

```jsonc
{
  "category": "license-error",
  "confidence": 0.78,
  "summary": "User cannot generate reports after renewing license; system still reads expired-license state.",
  "likely_cause": "License cache not invalidated after renewal. Manual confirms this requires a service restart or a /license/refresh API call.",
  "recommended_actions": [
    {
      "step": 1,
      "detail": "Call POST /license/refresh as admin to invalidate the cached license state.",
      "source_refs": ["cit-1"]
    },
    {
      "step": 2,
      "detail": "If step 1 fails, restart the application service: systemctl restart grp-app.",
      "source_refs": ["cit-1", "cit-2"]
    }
  ],
  "citations": [
    {
      "id": "cit-1",
      "source": "manual",
      "locator": { "file": "Admin.docx", "section": "Licensing > Renewal" },
      "snippet": "After a renewal the license cache must be refreshed via POST /license/refresh...",
      "score": 0.83
    },
    {
      "id": "cit-2",
      "source": "rfs_ticket",
      "locator": { "lodge_id": "LDG-90211" },
      "snippet": "Resolved by restarting grp-app service after license renewal stuck.",
      "score": 0.78
    }
  ],
  "related_rfs": [
    { "lodge_id": "LDG-90211", "score": 0.78, "snippet": "Resolved by service restart..." }
  ],
  "verifier_flags": []
}
```

For short-circuit input, Formatter produces a much shorter Analysis with `category="duplicate"` (or `"how-to"`), one-step recommended_actions, and citations pointing at the duplicate ticket.

## Rules

1. **No new content.** Formatter does not invent claims or actions. It only restructures.
2. **Trim, don't summarize.** If a field exceeds its `maxLength`, hard-truncate with `...` rather than rewriting.
3. **Stable citation IDs.** Map `chunk_id` → `cit-N` in order of first appearance. Both `recommended_actions[].source_refs` and any claim references update consistently.
4. **Snippets are ≤ 400 chars.** Take the first matching sentence from the chunk text or the chunk's natural opening.
5. **`verifier_flags` carry through verbatim** from Verifier output (kind + detail).
6. **JSON only.** No prose, no markdown.

## System prompt (cached)

```
You are a formatter. Convert internal agent outputs into the public Analysis
JSON. You do not invent or remove content; you only restructure and resolve
chunk_id references to Citation objects.

Output schema: { ... full Analysis schema inlined ... }

Rules:
  - Every source_refs[] entry must reference a citations[].id.
  - Snippets are at most 400 characters.
  - If short_circuit_payload is non-null, ignore analyst/verifier and produce
    a one-step Analysis based on the payload.
  - JSON only.
```

## Error modes

| Code | Retryable | Cause |
|---|---|---|
| `output_unparseable` | yes (once) | Non-JSON. |
| `schema_violation` | yes (once) | Output doesn't validate against Analysis schema. Retry with the validation error appended. |
| `model_overloaded` | yes | 529. |

Fallback: if the Formatter fails twice, the orchestrator falls back to a deterministic Python formatter (`format_analysis.py`, written W3-W4) that maps agent outputs to Analysis directly. The LLM Formatter is preferred because it can clean up citation snippets and reorder for readability; the deterministic fallback is correctness-only.

## Caching boundary

Cached prefix (~2,400 tokens): system prompt + Analysis schema + 2 exemplars (one full, one short-circuit).
Dynamic suffix (~600 tokens): analyst output (already JSON, slim) + verifier output + chunk locators.
