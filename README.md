# grp-support

GRP Support AI — Streamlit chat over GRP manuals, RFS tickets, scripts, code.
JWT-authenticated, multi-user, with audit log + cost dashboard.

## Layout

| File | Role | Runs as |
|---|---|---|
| `grp_chat.py` | Streamlit frontend | `grp-chat.service` → `:8501` (proxied at nginx `:8081`) |
| `api_server.py` | FastAPI backend (Claude-as-agent + ES/Ollama) | `grp-api.service` → `:8000` |
| `bootstrap_admin.py` | One-shot: seed initial admin user | manual run |
| `load_manuals.py` | Loader: chunk + embed manuals → `grp-manuals` index | manual run |
| `load_rfs_embed.py` | Loader: RFS tickets `.xls` → monthly indices | manual run |
| `tests/` | pytest suite (auth, image signing, budget, retention, api keys) | CI / local |
| `RUNBOOK.md` | Operational runbook (rotate keys, retention, alerting, etc.) | docs |
| `.github/workflows/` | CI (pytest) + Deploy (SSH `git pull && systemctl restart`) | GitHub Actions |

## VPS deploy

Working tree on VPS: `/opt/grp-chat`. Both `grp-api` and `grp-chat` services
load env vars from `/etc/grp-api.env`.

Backend depends on Elasticsearch (`:9200`), Ollama (`:11434`, `bge-m3`),
Anthropic API (`anthropic` SDK). Image files live under `/opt/grp-manuals/Doc-Images`
and are served via the FastAPI `/images/<path>?sig=...&exp=...` endpoint
(HMAC-signed URLs).

See [RUNBOOK.md](RUNBOOK.md) for the day-to-day ops procedures.

## Environment variables

Required:

- `ES_USER` / `ES_PASSWORD` — Elasticsearch credentials
- `ANTHROPIC_API_KEY` — for Claude (the search agent)
- `JWT_SECRET` — random ≥64 chars; rotating it invalidates all sessions

Optional (sensible defaults):

- `ANTHROPIC_MODEL` (default `claude-sonnet-4-6`)
- `JWT_TTL_HOURS` (default `12`)
- `IMG_DIR`, `IMG_PUBLIC_BASE`, `IMG_SIGN_TTL`
- `QUERY_RATE_LIMIT_PER_MIN` (default `30`)
- `MONTHLY_TOKEN_BUDGET` — set non-zero to enable hard cap (default `0` = unlimited)
- `COST_INPUT_PER_M`, `COST_OUTPUT_PER_M`, `COST_CACHE_PER_M` — pricing for `/audit/usage`
- `SLACK_WEBHOOK_URL` — for watchdog + budget-exceeded alerts
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM`,
  `SMTP_USE_TLS` — for welcome / reset emails
- `FRONTEND_URL` — used in email links

## Deps

```
pip install -r requirements.txt
```

## Tests

```
pip install pytest httpx
pytest
```

Tests stub Elasticsearch, Ollama, and Anthropic so they run offline.
