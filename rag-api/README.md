# rag-api

Multi-agent RAG service. Source apps POST an RFS (support ticket payload), service runs a 5-agent Claude pipeline over the existing knowledge base, returns structured analysis with citations.

Separate service from `grp-api` (the chat backend). Lives in the same repo for ergonomics, deploys to the same VPS, but has its own process, port, env file, and systemd unit. Shares the existing Elasticsearch (`grp-manuals`, `rfs-tickets-*`) and Ollama (`bge-m3`).

## Why separate

- Different traffic shape — async, fire-and-forget, batchable. Chat is interactive streaming.
- Different auth — service-to-service API key only. No browser session, no cookies.
- Different cost profile — Sonnet-heavy. Needs its own budget guard, separate from chat.
- Different SLO — webhook delivery is at-least-once with retry. Chat is best-effort live.

## Surface

| Endpoint | Purpose |
|---|---|
| `POST /rfs/analyze` | Submit RFS. Returns `{job_id}` immediately. 202 Accepted. |
| `GET /jobs/{id}` | Poll for status + result. |
| `POST /jobs/{id}/cancel` | Cancel a queued or running job (admin). |
| `GET /health` | Liveness. |
| `GET /ready` | Deps reachable (ES, Ollama, Anthropic, Redis). |

Webhook callback (if `callback_url` provided on submit): service POSTs result to caller. HMAC-signed. Idempotent retries with exponential backoff.

## Files

- `openapi.yaml` — full OpenAPI 3.1 spec. Source of truth for HTTP surface.
- `contracts/` — per-agent input/output contracts.
  - `00-overview.md` — pipeline diagram + shared types
  - `01-classifier.md` — Haiku, categorize
  - `02-retrieval-planner.md` — Haiku, plan ES queries
  - `03-analyst.md` — Sonnet, reasoning + draft analysis (cached prompt)
  - `04-verifier.md` — Haiku, citation + claim check
  - `05-formatter.md` — Haiku, final response shape

## Status

W1 milestone (M1) — API contract published. Implementation starts W3 (`/rfs/analyze` skeleton + queue).

## Runtime (planned)

- Port: `8001` (loopback) — nginx fronts on a path prefix or vhost.
- systemd unit: `rag-api`
- Env file: `/etc/rag-api.env`
- Worker: same process for v1 (`asyncio` task queue backed by Redis). Split out to dedicated worker process if pilot reveals saturation.
