# rag-api deploy

VPS: `173.212.247.3`. Repo path: `/opt/grp-chat/rag-api`. Runs on loopback `:8001`, fronted by the existing nginx vhost under `/rag/`.

## First-time install on VPS

```bash
# 1. Pull repo (already cloned at /opt/grp-chat)
cd /opt/grp-chat
sudo -u claudeuser git pull

# 2. Install python deps into claudeuser's --user site (shared with grp-api;
#    grp-api runs the same way — no venv).
sudo -u claudeuser python3 -m pip install --user -r rag-api/requirements.txt

# 3. Env file
sudo install -o root -g claudeuser -m 0640 \
    /opt/grp-chat/rag-api/deploy/rag-api.env.example \
    /etc/rag-api.env
sudo nano /etc/rag-api.env     # fill in ES_PASSWORD, ANTHROPIC_API_KEY, WEBHOOK_DEFAULT_SECRET

# 4. systemd unit
sudo install -o root -g root -m 0644 \
    /opt/grp-chat/rag-api/deploy/rag-api.service \
    /etc/systemd/system/rag-api.service
sudo systemctl daemon-reload
sudo systemctl enable --now rag-api

# 5. nginx — splice the snippet into the TLS server block
sudo cp /etc/nginx/sites-enabled/grp-chat-tls /etc/nginx/sites-enabled/grp-chat-tls.bak
# manually insert the contents of deploy/nginx-rag.snippet inside `server { ... }`
sudo nginx -t && sudo systemctl reload nginx

# 6. Smoke test
curl -fsS http://127.0.0.1:8001/health
curl -fsS https://173.212.247.3.nip.io/rag/health
```

## Verify with a real API key

```bash
# Mint an API key via grp-api (the rag-api shares the same key store)
TOKEN=$(curl -fsS -X POST http://127.0.0.1:8000/auth/login \
    -H 'Content-Type: application/json' \
    -d '{"email":"admin@example.com","password":"..."}' | jq -r .access_token)

KEY=$(curl -fsS -X POST http://127.0.0.1:8000/api-keys \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d '{"name":"rag-api-test","owner":"admin@example.com"}' | jq -r .key)

# Submit a stub RFS
curl -fsS -X POST https://173.212.247.3.nip.io/rag/rfs/analyze \
    -H "Authorization: ApiKey $KEY" \
    -H "Idempotency-Key: $(uuidgen)" \
    -H "Content-Type: application/json" \
    -d '{"rfs":{"lodge_id":"LDG-SMOKE-1","notes":"test"}}' | jq

# Poll
JOB_ID=...
curl -fsS https://173.212.247.3.nip.io/rag/jobs/$JOB_ID \
    -H "Authorization: ApiKey $KEY" | jq
```

Expect `status=succeeded` within ~3 seconds (stub pipeline). `result.category=other`, 5 stub trace entries.

## Deploy a code change

Same flow as grp-api — pull, restart unit:

```bash
cd /opt/grp-chat && sudo -u claudeuser git pull
sudo -u claudeuser python3 -m pip install --user -r rag-api/requirements.txt
sudo systemctl restart rag-api
journalctl -u rag-api -n 50 --no-pager
```

## Watchdog

Add a third check to `/usr/local/bin/grp-watchdog.sh`:

```bash
if ! curl -fsS -m 10 http://127.0.0.1:8001/health >/dev/null 2>&1; then
  notify "rag-api /health failing — restarting"
  systemctl restart rag-api
fi
```

## Rollback

```bash
cd /opt/grp-chat && sudo -u claudeuser git checkout <prev-sha>
sudo systemctl restart rag-api
```

Job state is in Redis DB 3 — restart preserves queued + in-flight jobs (queued ones replay; in-flight ones the next worker will re-pick because they were never `LREM`'d on dequeue. **TODO W7-W8**: add a `dequeue-then-mark-running` reservation to make this safer; for now, idempotency on the caller side handles double-run.)
