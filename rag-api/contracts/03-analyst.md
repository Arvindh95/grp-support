# Agent 3 — Analyst

## Purpose

Read the RFS plus retrieved evidence. Produce a draft analysis: likely cause, recommended actions, and a citation map linking every claim to a chunk. This is the only Sonnet step. Heaviest token + dollar contribution.

## Model

`claude-sonnet-4-6`

(Retry on Verifier `must_retry` uses `claude-opus-4-7` — capped at 2% of traffic.)

## Input

```jsonc
{
  "rfs": { /* RFS shape */ },
  "classifier_output": { /* category, confidence, tags */ },
  "retrieved_context": [
    {
      "chunk_id": "c-001",
      "source": "manual",
      "locator": { "file": "Admin.docx", "section": "Licensing > Renewal" },
      "text": "...up to ~400 tokens...",
      "score": 0.83
    },
    {
      "chunk_id": "c-002",
      "source": "rfs_ticket",
      "locator": { "lodge_id": "LDG-90211", "action_idx": 3 },
      "text": "...",
      "score": 0.78
    }
    // ... up to 12
  ]
}
```

## Output

```jsonc
{
  "summary": "User cannot generate reports after renewing license; system still reads expired-license state.",
  "likely_cause": "License cache not invalidated after renewal. Manual confirms this requires a service restart or a /license/refresh API call.",
  "recommended_actions": [
    {
      "step": 1,
      "detail": "Call POST /license/refresh as admin to invalidate the cached license state.",
      "claim_refs": ["c-001"]
    },
    {
      "step": 2,
      "detail": "If step 1 fails, restart the application service: `systemctl restart grp-app`.",
      "claim_refs": ["c-001", "c-002"]
    }
  ],
  "claims": [
    {
      "id": "claim-1",
      "text": "The license refresh endpoint exists and is the documented fix for stale license state.",
      "supports_step": [1],
      "citations": ["c-001"]
    },
    {
      "id": "claim-2",
      "text": "LDG-90211 had the same symptom and was resolved by service restart.",
      "supports_step": [2],
      "citations": ["c-002"]
    }
  ],
  "confidence": 0.78,
  "open_questions": [
    "Did the user complete payment via the new portal or legacy form? Resolution differs."
  ]
}
```

## Rules the Analyst MUST follow

1. **Every claim has ≥1 citation.** No claim with empty `citations`. The Verifier hard-fails on this.
2. **No external knowledge.** Only use facts present in `retrieved_context`. If retrieval is insufficient, lower `confidence` and add an `open_questions` entry. Do not fabricate URLs, version numbers, or product names.
3. **JSON only.** No markdown, no preamble.
4. **Cite by `chunk_id`.** Not by section name, not by lodge_id. Orchestrator resolves chunk_id → human citation later.
5. **Confidence ∈ [0, 1].** Calibrated: 0.8 = "this is the documented fix"; 0.5 = "best guess from past tickets"; <0.3 = "I don't have enough evidence".

## System prompt (cached, ~5,500 tokens)

```
You are an Acumatica/GRP support analyst. Read the user's RFS and the
retrieved evidence (manual chunks + past tickets). Produce a structured
analysis as JSON.

Hard rules:
  - Every claim cites at least one chunk_id from retrieved_context.
  - You may only state facts supported by retrieved_context.
  - If evidence is missing, lower confidence and list open_questions.
  - Output is JSON only — no prose, no markdown, no chain-of-thought.

Schema: { ... full schema inlined ... }

Three exemplars: { ... } { ... } { ... }
```

## Cache boundary

Cached prefix (~5,500 tokens): system prompt + schema + 3 exemplars.
Dynamic suffix (~4,500 tokens): RFS + retrieved_context. Rebuilt every call — **not** cached, because chunks differ per RFS.

Cache-hit on input = `cached_prefix_tokens / total_input_tokens` ≈ 5500 / 10000 ≈ 55%. The 70% steady-state target across the pipeline accounts for the other agents having higher cache shares.

## Error modes

| Code | Retryable | Cause |
|---|---|---|
| `output_unparseable` | yes (once) | Non-JSON. Retry once with strict suffix. |
| `claim_without_citation` | yes (once) | A claim has empty citations. Retry with explicit reminder. |
| `confidence_too_low` | no | confidence < 0.2 — surface as `verifier_flags.low_confidence`. Don't retry; let Formatter pass it through. |
| `model_overloaded` | yes | 529. |
| `budget_exhausted` | no | Monthly cap hit. |

## Cost note

At ~12K input + 2K output, Sonnet contributes ≈ 78% of per-RFS Claude cost (per `plan/build_combined_excel.py`). Levers to pull before scaling:

1. Shrink `retrieved_context` from 12 → 8 chunks (Planner-side rerank). −20% input cost. Validate eval stays ≥ 80%.
2. Force `output ≤ 1500 tokens` via instruction + max_tokens. Output is 5× input price.
3. Tighten claim-citation format to reduce repetition.
