# Agent 4 — Verifier

## Purpose

Check the Analyst's draft against the retrieved evidence. Catch hallucinations, unsupported claims, weak citations, and structural issues. Decide whether to accept, flag, or force a one-shot retry on Opus.

## Model

`claude-haiku-4-5`

## Input

```jsonc
{
  "analyst_output": { /* full Analyst JSON */ },
  "retrieved_context": [ /* same array Analyst received */ ],
  "category": "license-error"
}
```

## Output

```jsonc
{
  "verdict": "pass" | "flag" | "must_retry",
  "flags": [
    { "kind": "weak_citation", "claim_id": "claim-2", "detail": "..." }
  ],
  "must_retry_reason": null,
  "rubric_scores": {
    "every_claim_cited": true,
    "citations_resolve": true,
    "no_external_facts": true,
    "actions_actionable": true,
    "confidence_calibrated": true
  }
}
```

## Verdicts

- **pass** — All rubric checks green. Forward Analyst output unchanged to Formatter.
- **flag** — Some rubric checks amber but not fatal. Forward to Formatter, attach `flags` to final `Analysis.verifier_flags`.
- **must_retry** — Fatal issue (e.g., `every_claim_cited=false`, `no_external_facts=false`). Orchestrator re-runs Analyst once with Verifier's feedback included, using `claude-opus-4-7`. If the retry also fails, downgrade to `flag` and proceed with `low_confidence`.

## Rubric (all binary)

| Check | Detail | Fatal? |
|---|---|---|
| `every_claim_cited` | Every Analyst claim has ≥1 chunk_id in `citations`. | Yes |
| `citations_resolve` | Every cited chunk_id appears in `retrieved_context`. | Yes |
| `no_external_facts` | Sample 3 claims; each is verifiable from its cited chunk text. | Yes |
| `actions_actionable` | Each `recommended_actions[].detail` is concrete (no "investigate" with no next step). | No |
| `confidence_calibrated` | `confidence` aligns with citation strength (heuristic — see prompt). | No |

## System prompt (cached)

```
You are a verifier. Read an Analyst's draft and the retrieved chunks.
Run the rubric below. Output JSON only.

Rubric:
  1. every_claim_cited: true iff every claim has citations.length >= 1.
  2. citations_resolve: true iff every cited chunk_id exists in retrieved_context.
  3. no_external_facts: pick 3 claims at random. For each, find a sentence in
     the cited chunk(s) that supports it. If you can't, false.
  4. actions_actionable: each recommended action describes a concrete next
     step (a command, a UI path, a config change). If any is vague ("look
     into the database"), false.
  5. confidence_calibrated: if confidence >= 0.7, every claim must have a
     citation from a manual or a high-score ticket (score >= 0.7). Else false.

Verdict:
  - must_retry if any of (1), (2), (3) are false.
  - flag if (4) or (5) are false.
  - pass otherwise.
```

## Error modes

| Code | Retryable | Cause |
|---|---|---|
| `output_unparseable` | yes (once) | Non-JSON. |
| `model_overloaded` | yes | 529. |

The Verifier itself never blocks the pipeline. Worst case `must_retry` triggers one Opus retry; if that also fails, Verifier verdict degrades to `flag` and the job succeeds with `verifier_flags` populated. No infinite loops.

## Caching boundary

Cached prefix (~2,200 tokens): system prompt + rubric + 4 verdict exemplars (one per outcome).
Dynamic suffix (~800 tokens): analyst output (summarized to claims+citations only, full prose stripped) + chunk_id list with first-line snippets.

## Cost note

Cheap by design (Haiku, ~3K input, 300 output). ≈ 4% of per-RFS Claude bill. Plan considers replacing this agent with deterministic code-based checks (regex + ES `exists` lookups) to save another ~3% — see `plan/build_combined_excel.py` "Move Verifier to deterministic checks". Decision: keep LLM Verifier for v1 (catches subtle hallucination), revisit at W12 after eval.
