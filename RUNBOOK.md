# GRP Support AI — Operations Runbook

Production VPS: `173.212.247.3`. Repo working tree on VPS: `/opt/grp-chat`.

All commands assume you are SSH'd into the VPS as a sudoer.

## Components

| Service       | Port  | systemd unit  | Working dir          |
|---------------|-------|---------------|----------------------|
| FastAPI (uvicorn) | 8000 (loopback) | `grp-api` | `/opt/grp-chat` |
| nginx (HTTPS) | 443 + 80 redirect | `nginx` | `/etc/nginx/sites-enabled/grp-chat-tls` |
| Elasticsearch | 9200  | `elasticsearch` | `/etc/elasticsearch` |
| Ollama        | 11434 | `ollama`      | n/a                  |

Public URL: `https://173.212.247.3.nip.io` (Let's Encrypt cert auto-renews).
Built React frontend lives in `/opt/grp-chat-web` (served as static by nginx).

Env file (loaded by `grp-api`): `/etc/grp-api.env` — contains `ES_USER`,
`ES_PASSWORD`, `ANTHROPIC_API_KEY`, `JWT_SECRET`, optional `SMTP_*`,
`SLACK_WEBHOOK_URL`, `MONTHLY_TOKEN_BUDGET`, `IMG_PUBLIC_BASE`, `FRONTEND_URL`.

## Deploy a code change

```bash
ssh root@173.212.247.3
cd /opt/grp-chat
sudo -u claudeuser git pull
systemctl restart grp-api
journalctl -u grp-api -n 50 --no-pager   # confirm clean start

# Frontend rebuild (when web/ changed):
cd /opt/grp-chat/web && sudo -u claudeuser npm ci --silent && sudo -u claudeuser npm run build
sudo rm -rf /opt/grp-chat-web/* /opt/grp-chat-web/.[!.]* 2>/dev/null
sudo cp -r out/. /opt/grp-chat-web/
sudo chown -R www-data:www-data /opt/grp-chat-web
```

CI/CD does this automatically on push to `main` once the GitHub Actions
workflow is enabled (see `.github/workflows/deploy.yml`).

## Restart everything

```bash
systemctl restart grp-api nginx elasticsearch ollama
```

In dependency order. ES first if it is the one stuck.

## Rotate Anthropic API key

```bash
nano /etc/grp-api.env                                       # update ANTHROPIC_API_KEY=...
systemctl restart grp-api
curl -fsS http://127.0.0.1:8000/health                      # sanity
```

Revoke the old key in the Anthropic console after the new one is live.

## Rotate JWT secret

Rotating `JWT_SECRET` invalidates **all active sessions and signed image
URLs**. Plan a maintenance window.

```bash
NEW=$(openssl rand -hex 64)
sed -i "s|^JWT_SECRET=.*|JWT_SECRET=$NEW|" /etc/grp-api.env
systemctl restart grp-api
```

All users will have to log in again.

## User locked out / forgot password

Two options. Prefer (1) if SMTP is configured.

1. **Self-service reset** — the user clicks "Forgot password" on the login
   page. They get an email with a 1-hour reset link.

2. **Admin reset** — sign in as admin at `https://173.212.247.3.nip.io/admin/users/`
   and click "Reset", or `POST /auth/users/<email>/reset-password` directly.
   Tell the user the new password out-of-band; ask them to change it on first login.

```bash
TOKEN=$(curl -fsS -X POST http://127.0.0.1:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@example.com","password":"...."}' | jq -r .access_token)

curl -fsS -X POST http://127.0.0.1:8000/auth/users/joe@x.com/reset-password \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"new_password":"<temp pass>"}'
```

## Service is down (from a user report)

```bash
curl -fsS http://127.0.0.1:8000/health || systemctl status grp-api
journalctl -u grp-api -n 200 --no-pager
```

If the process is dead, the watchdog cron should have already restarted it
within 2 minutes (see `/etc/cron.d/grp-api-watchdog`). If it has been longer,
restart manually:

```bash
systemctl restart grp-api
```

If it crashes again immediately, check the journalctl tail for the
exception. Common causes: `ES_PASSWORD` wrong, ES down, Ollama down.

## Elasticsearch is down

```bash
systemctl status elasticsearch
journalctl -u elasticsearch -n 100 --no-pager
df -h /var/lib/elasticsearch                # disk full?
free -m                                     # OOM?
systemctl restart elasticsearch
curl -fsS -u elastic:$ES_PASSWORD https://127.0.0.1:9200/_cluster/health -k
```

If ES disk is full, run retention now (see "Index retention" below) or extend
the disk.

## Ollama (embeddings) is down

```bash
systemctl status ollama
ollama list               # is bge-m3 still pulled?
ollama pull bge-m3        # if missing
systemctl restart ollama
curl -fsS http://127.0.0.1:11434/api/tags
```

## Index retention (audit / chats)

The audit and chat indices grow forever by default. Run retention manually
or via the daily cron:

```bash
# manual one-shot — admin token required
TOKEN=$(curl ... auth/login ...)
curl -fsS -X POST 'http://127.0.0.1:8000/admin/retention/run?audit_days=90&chats_days=365' \
  -H "Authorization: Bearer $TOKEN"
```

Recommended cron (`/etc/cron.d/grp-retention`):

```cron
# Daily at 03:15 — drop audit older than 90d, chat older than 365d
15 3 * * * root /usr/local/bin/grp-retention.sh >> /var/log/grp-retention.log 2>&1
```

`/usr/local/bin/grp-retention.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
. /etc/grp-api.env
TOKEN=$(curl -fsS -X POST http://127.0.0.1:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"$ADMIN_EMAIL\",\"password\":\"$ADMIN_PASSWORD\"}" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
curl -fsS -X POST 'http://127.0.0.1:8000/admin/retention/run?audit_days=90&chats_days=365' \
  -H "Authorization: Bearer $TOKEN"
```

(Add `ADMIN_EMAIL` and `ADMIN_PASSWORD` to `/etc/grp-api.env` for the cron
or use a long-lived API key — see "API keys" below.)

## Watchdog + Slack alerting

`/etc/cron.d/grp-api-watchdog`:

```cron
*/2 * * * * root /usr/local/bin/grp-watchdog.sh
```

`/usr/local/bin/grp-watchdog.sh`:

```bash
#!/usr/bin/env bash
set -u
. /etc/grp-api.env
notify() {
  if [ -n "${SLACK_WEBHOOK_URL:-}" ]; then
    curl -fsS -X POST -H 'Content-Type: application/json' \
      -d "{\"text\":\":rotating_light: $1\"}" "$SLACK_WEBHOOK_URL" >/dev/null || true
  fi
  logger -t grp-watchdog "$1"
}
if ! curl -fsS -m 10 http://127.0.0.1:8000/health >/dev/null 2>&1; then
  notify "grp-api /health failing — restarting"
  systemctl restart grp-api
fi
if ! curl -fsS -m 10 -u "elastic:$ES_PASSWORD" -k https://127.0.0.1:9200/_cluster/health >/dev/null 2>&1; then
  notify "Elasticsearch unreachable on grp-support VPS"
fi
if ! curl -fsS -m 10 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  notify "Ollama unreachable on grp-support VPS"
fi
```

Make sure the script is `chmod +x`. Set `SLACK_WEBHOOK_URL` in
`/etc/grp-api.env` (Slack incoming webhook URL).

## API keys (service-to-service)

For automation (cron, Slack bot, scheduled report) prefer an API key over
a JWT. Mint it as admin:

```bash
TOKEN=$(...auth/login...)
curl -fsS -X POST http://127.0.0.1:8000/api-keys \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"slackbot","owner":"admin@example.com"}'
# → {"key": "grp_...."}     ← shown ONCE
```

Use the key as:

```
Authorization: ApiKey grp_xxxx...
```

Revoke a key:

```bash
curl -fsS -X DELETE http://127.0.0.1:8000/api-keys/<id> -H "Authorization: Bearer $TOKEN"
```

## Cost / usage check

```bash
TOKEN=$(...auth/login...)
curl -fsS http://127.0.0.1:8000/audit/usage -H "Authorization: Bearer $TOKEN" | jq
```

Returns per-user month-to-date input/output/cached tokens and an estimated
USD cost (rates configurable via `COST_INPUT_PER_M` / `COST_OUTPUT_PER_M`
env vars).

If a user is suddenly burning huge tokens, revoke their JWT (force re-login
by rotating `JWT_SECRET`) or delete/disable the account, and inspect their
recent activity:

```bash
curl -fsS "http://127.0.0.1:8000/audit?user=joe@x.com&size=200" \
  -H "Authorization: Bearer $TOKEN" | jq
```

## Token budget guard

Set `MONTHLY_TOKEN_BUDGET=20000000` (20M tokens) in `/etc/grp-api.env` and
restart `grp-api`. Once the cumulative `input_tokens + output_tokens` for
the current calendar month meets or exceeds the budget, all `/query` and
`/query/stream` calls return 429 until the next month rolls over. A Slack
notification fires once when the budget is first hit.

## Image endpoint security

Manual screenshots are served via `GET /images/{path}` and require an
HMAC-signed query parameter (`?sig=...&exp=...`). Signed URLs are minted by
the backend at response time and are valid for `IMG_SIGN_TTL` seconds
(default = 12h). Anyone trying to fetch an image without a signature gets
403.

The TLS vhost (`/etc/nginx/sites-enabled/grp-chat-tls`) already proxies
`/images/` to FastAPI on `:8000` and `IMG_PUBLIC_BASE` is set to
`https://173.212.247.3.nip.io/images`, so signed URLs work end-to-end.
