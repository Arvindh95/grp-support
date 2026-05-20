# Agent 1 — Classifier

## Purpose

Assign the RFS to a category, confidence, tags, and detected language. The Classifier never terminates the pipeline; every accepted RFS continues through Retrieval Planner, Analyst, Verifier, and Formatter.

## Model

`claude-haiku-4-5`

## Input

```jsonc
{
  "rfs": { /* RFS shape from openapi.yaml */ }
}
```

## Output

```jsonc
{
  "category": "license-error",
  "confidence": 0.86,
  "tags": ["billing", "renewal"],
  "language": "en"
}
```

## Category enum (v1)

`license-error`, `data-import`, `report-generation`, `permissions`, `integration-failure`, `performance`, `ui-bug`, `data-corruption`, `feature-request`, `how-to`, `duplicate`, `other`.

Adding a category requires updating this contract + the cached system prompt.

## Classification rules

1. Pick the single best category even when confidence is low.
2. Use `duplicate` only when the RFS itself clearly states that it is a duplicate or refers to another lodge/ticket as the same issue.
3. Use `how-to` for basic usage questions, but do not answer the question here.
4. Use `other` for empty, spam-like, or insufficiently actionable notes.
5. Do not include fields outside the output schema.

## System prompt (cached)

```
You are a support-ticket classifier. Output JSON only, matching the schema
in this prompt. Categories: [license-error, data-import, ...].

Rules:
  - Pick the single best category.
  - Return confidence, tags, and language only.
  - Do not answer the user's question.

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

Cached prefix: system prompt + category enum + exemplars (~1,100 tokens).
Dynamic suffix: RFS notes and relatedarea (~400 tokens).
