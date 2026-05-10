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
apt-get install -y python3 python3-pip nginx pandoc curl jq nodejs npm certbot

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
systemctl daemon-reload

echo "── 6. TLS cert (Let's Encrypt via nip.io) ──"
HOST="${PUBLIC_HOST:-$(hostname -I | awk '{print $1}' | tr . - ).nip.io}"
mkdir -p /var/www/letsencrypt
if [ ! -f "/etc/letsencrypt/live/$HOST/fullchain.pem" ]; then
    cat > /etc/nginx/sites-available/grp-acme <<EOF
server {
    listen 80;
    server_name $HOST;
    location /.well-known/acme-challenge/ { root /var/www/letsencrypt; }
    location / { return 503 'Setting up TLS\n'; }
}
EOF
    ln -sf /etc/nginx/sites-available/grp-acme /etc/nginx/sites-enabled/grp-acme
    nginx -t && systemctl reload nginx
    EMAIL="${ADMIN_EMAIL:-admin@example.com}"
    certbot certonly --webroot -w /var/www/letsencrypt -d "$HOST" \
        --non-interactive --agree-tos --email "$EMAIL"
    rm -f /etc/nginx/sites-enabled/grp-acme
fi

echo "── 7. nginx TLS site ──"
sed "s|173.212.247.3.nip.io|$HOST|g" deploy/nginx/grp-chat-tls \
    > /etc/nginx/sites-available/grp-chat-tls
ln -sf /etc/nginx/sites-available/grp-chat-tls /etc/nginx/sites-enabled/grp-chat-tls
nginx -t && systemctl reload nginx

echo "── 8. start backend ──"
[ -f /etc/grp-api.env ] || { echo "ERROR: /etc/grp-api.env missing — copy from .env.example first"; exit 1; }
chmod 600 /etc/grp-api.env
chown root:root /etc/grp-api.env
systemctl enable --now grp-api

echo "── 9. bootstrap admin (one-time) ──"
echo "   Run manually: ADMIN_EMAIL=you@example.com ADMIN_PASSWORD='ChangeMe123' \\"
echo "                 sudo -u $USER python3 $REPO/bootstrap_admin.py"

echo "── 10. health ──"
sleep 3
curl -fsS http://127.0.0.1:8000/health && echo
echo "Done. Frontend on https://$HOST, API behind /api/, images on /images/."
