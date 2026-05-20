# Agent 2 — Retrieval Planner

## Purpose

Convert the Classifier output + RFS into a concrete retrieval plan: which ES indices to hit, which lexical queries to run, which fields to filter, how many chunks to return. The orchestrator executes the plan — the agent does NOT call ES.

## Model

`claude-haiku-4-5`

## Input

```jsonc
{
  "rfs": { /* RFS shape */ },
  "classifier_output": { /* output of Agent 1 */ },
  "available_indices": [
    {
      "name": "grp-manuals",
      "kind": "manual",
      "fields": ["title", "section", "text", "embedding"],
      "size_docs": 12500
    },
    {
      "name": "rfs-tickets-*",
      "kind": "rfs_ticket",
      "fields": ["lodge_id", "notes", "actions.note", "embedding"],
      "size_docs": 84000
    },
    {
      "name": "grp-code-scripts",
      "kind": "code_script",
      "fields": ["filename", "language", "text", "embedding"],
      "size_docs": 3200
    }
  ]
}
```

## Output

```jsonc
{
  "queries": [
    {
      "index": "grp-manuals",
      "mode": "hybrid",
      "knn": {
        "field": "embedding",
        "query_text": "license renewal expired error",
        "k": 6,
        "num_candidates": 100
      },
      "lexical": {
        "must": [
          { "multi_match": { "query": "license renewal", "fields": ["title^3", "section^2", "text"] } }
        ],
        "filter": [
          { "term": { "section": "licensing" } }
        ]
      },
      "rerank": { "strategy": "mmr", "lambda": 0.6 },
      "max_chunks": 5
    },
    {
      "index": "rfs-tickets-*",
      "mode": "knn",
      "knn": {
        "field": "embedding",
        "query_text": "license renewal expired error",
        "k": 8,
        "num_candidates": 200
      },
      "lexical": null,
      "rerank": { "strategy": "score_only" },
      "max_chunks": 3
    }
  ],
  "rationale": "User reports license error after renewal payment. Need manuals on licensing flow + past tickets with same symptom."
}
```

## Constraints

- Total `max_chunks` across queries ≤ **12**. Hard cap. Exceeding it makes Analyst input balloon.
- Always include at least one `grp-manuals` query if available — manuals beat past tickets for authority.
- Always include at least one `rfs-tickets-*` query — past tickets show what actually worked.
- `query_text` may differ from raw RFS notes. The Planner is expected to canonicalize ("can't login since this morning" → "login failure authentication issue").
- `rerank.strategy` ∈ {`mmr`, `score_only`}. `mmr` is the default for manuals (reduces redundancy).

## System prompt (cached)

```
You are a retrieval-planner. Build an Elasticsearch query plan that surfaces
the most useful evidence for an Analyst agent to answer the user's RFS.

Output a JSON object matching the schema. Do NOT call any tool. Do NOT
execute queries. Just plan.

Rules:
  - Always cover both manuals and past tickets.
  - Total max_chunks across queries must be <= 12.
  - For manual queries, use mode="hybrid" and rerank="mmr" by default.
  - For ticket queries, use mode="knn" unless the Classifier set category
    to "how-to" (then use mode="hybrid" and prefer manuals).
```

## Error modes

| Code | Retryable | Cause |
|---|---|---|
| `output_unparseable` | yes (once) | Non-JSON or schema mismatch. |
| `plan_invalid` | yes (once) | sum(max_chunks) > 12, no manual query, etc. Orchestrator validates and retries with a stricter "fix this plan" suffix. |
| `model_overloaded` | yes | 529. |

## Caching boundary

Cached prefix: system prompt + index list + ES schema description + 3 plan exemplars (~1,300 tokens).
Dynamic suffix: classifier output + RFS notes summary (~700 tokens).
