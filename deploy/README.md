# Deploy

Mirrors live VPS state. Update these files when you change anything on the host.

## Files

| File | Lives on host as | Purpose |
|---|---|---|
| `systemd/grp-api.service` | `/etc/systemd/system/grp-api.service` | FastAPI on `:8000`, 4 workers |
| `systemd/grp-chat.service` | `/etc/systemd/system/grp-chat.service` | Streamlit on `:8501` (legacy, optional) |
| `nginx/grp-chat` | `/etc/nginx/sites-available/grp-chat` | React on `:8081` + `/api/` proxy |
| `nginx/grp-images` | `/etc/nginx/sites-available/grp-images` | Image proxy on `:8080` (HMAC-signed) |
| `bootstrap.sh` | — | Idempotent fresh-host installer |

## New host bootstrap

Order matters:

```bash
# 1. OS prereqs
apt install -y python3 python3-pip nginx pandoc nodejs npm jq curl

# 2. Elasticsearch 8.x — install per official docs, then:
/usr/share/elasticsearch/bin/elasticsearch-reset-password -u elastic
# save the password

# 3. Ollama
curl -fsSL https://ollama.com/install.sh | sh
ollama pull bge-m3

# 4. App
git clone <repo> /opt/grp-chat
cp /opt/grp-chat/.env.example /etc/grp-api.env
$EDITOR /etc/grp-api.env   # fill all values
chmod 600 /etc/grp-api.env

# 5. Run bootstrap
sudo bash /opt/grp-chat/deploy/bootstrap.sh

# 6. First admin
ADMIN_EMAIL=you@example.com ADMIN_PASSWORD='ChangeMe123' \
  sudo -u claudeuser python3 /opt/grp-chat/bootstrap_admin.py
```

## Updating live config

Edit the file in this folder, commit, then on host:

```bash
cd /opt/grp-chat && git pull
sudo install -m 644 deploy/systemd/grp-api.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl restart grp-api
# or for nginx:
sudo install -m 644 deploy/nginx/grp-chat /etc/nginx/sites-available/
sudo nginx -t && sudo systemctl reload nginx
```
