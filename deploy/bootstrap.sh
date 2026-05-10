#!/usr/bin/env bash
# GRP Support AI — fresh-host bootstrap. Idempotent. Run as root on Ubuntu 22.04+.
#
# Prereqs you must do manually first:
#   1. Install Elasticsearch 8.x (apt) — https://www.elastic.co/guide/en/elasticsearch/reference/current/deb.html
#      Reset elastic password, save value.
#   2. Install Ollama — curl -fsSL https://ollama.com/install.sh | sh
#      Then: ollama pull bge-m3
#   3. Place repo at /opt/grp-chat (git clone or rsync)
#   4. Copy .env.example to /etc/grp-api.env, fill all values, chmod 600
#
# Then run: sudo bash deploy/bootstrap.sh

set -euo pipefail

REPO=/opt/grp-chat
WEB_OUT=/opt/grp-chat-web
USER=claudeuser

echo "── 1. apt deps ──"
apt-get update
apt-get install -y python3 python3-pip nginx pandoc curl jq nodejs npm

echo "── 2. service user ──"
id "$USER" >/dev/null 2>&1 || useradd -m -s /bin/bash "$USER"

echo "── 3. python deps ──"
sudo -u "$USER" pip install --break-system-packages --user -r "$REPO/requirements.txt"

echo "── 4. frontend build ──"
cd "$REPO/web"
sudo -u "$USER" npm ci --silent --no-audit --no-fund
sudo -u "$USER" npm run build
mkdir -p "$WEB_OUT"
rm -rf "$WEB_OUT"/*
cp -r out/. "$WEB_OUT"/
chown -R www-data:www-data "$WEB_OUT" 2>/dev/null || true
cd "$REPO"

echo "── 5. systemd units ──"
install -m 644 deploy/systemd/grp-api.service /etc/systemd/system/
install -m 644 deploy/systemd/grp-chat.service /etc/systemd/system/
systemctl daemon-reload

echo "── 6. nginx sites ──"
install -m 644 deploy/nginx/grp-chat /etc/nginx/sites-available/
install -m 644 deploy/nginx/grp-images /etc/nginx/sites-available/
ln -sf /etc/nginx/sites-available/grp-chat /etc/nginx/sites-enabled/
ln -sf /etc/nginx/sites-available/grp-images /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

echo "── 7. start backend ──"
[ -f /etc/grp-api.env ] || { echo "ERROR: /etc/grp-api.env missing — copy from .env.example first"; exit 1; }
chmod 600 /etc/grp-api.env
chown root:root /etc/grp-api.env
systemctl enable --now grp-api
# systemctl enable --now grp-chat   # uncomment if you want Streamlit too

echo "── 8. bootstrap admin (one-time) ──"
echo "   Run manually: ADMIN_EMAIL=you@example.com ADMIN_PASSWORD='ChangeMe123' \\"
echo "                 sudo -u $USER python3 $REPO/bootstrap_admin.py"

echo "── 9. health ──"
sleep 3
curl -fsS http://127.0.0.1:8000/health && echo
echo "Done. Frontend on :8081, images on :8080, API on :8000."
