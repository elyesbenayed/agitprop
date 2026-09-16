#!/usr/bin/env bash
# Installation / mise a jour d'Agitprop sur un VPS Ubuntu 24.04 (Infomaniak VPS Lite ou VPS Cloud).
#
#   Usage : sudo bash deploy/install-vps.sh <domaine> <email>
#   Ex.   : sudo bash deploy/install-vps.sh agitprop.lafrancehumaniste.fr contact@lafrancehumaniste.fr
#
# A lancer depuis le dossier des sources envoye sur le serveur (celui qui contient app/, templates/, static/).
# Relancable sans risque : les donnees (/opt/agitprop/.env et /opt/agitprop/data) sont conservees.
set -euo pipefail

DOMAIN="${1:-}"
EMAIL="${2:-}"
if [ -z "$DOMAIN" ] || [ -z "$EMAIL" ]; then
  echo "Usage : sudo bash deploy/install-vps.sh <domaine> <email>"; exit 1
fi
if [ ! -f app/main.py ]; then
  echo "Lancez ce script depuis le dossier qui contient app/ (cd /root/agitprop-src)"; exit 1
fi
if [ "$(id -u)" != 0 ]; then
  echo "A lancer avec sudo"; exit 1
fi

APP=/opt/agitprop
export DEBIAN_FRONTEND=noninteractive

echo "[1/6] Paquets systeme"
apt-get update -q
apt-get install -y -q python3 python3-venv python3-pip nginx certbot python3-certbot-nginx rsync ufw openssl

echo "[2/6] Sources -> $APP"
id -u agitprop >/dev/null 2>&1 || useradd --system --home "$APP" --shell /usr/sbin/nologin agitprop
mkdir -p "$APP/data"
rsync -a --delete \
  --exclude venv --exclude __pycache__ --exclude '*.db' --exclude '*.db-journal' \
  --exclude .env --exclude data --exclude IDENTIFIANTS.txt --exclude deploy \
  ./ "$APP/"

echo "[3/6] Environnement Python"
[ -d "$APP/venv" ] || python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install -q --upgrade pip
"$APP/venv/bin/pip" install -q -r "$APP/requirements.txt"

echo "[4/6] Configuration"
if [ ! -f "$APP/.env" ]; then
  FERNET=$("$APP/venv/bin/python" -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
  SESSION=$(openssl rand -base64 24 | tr -d '/+=')
  ADMIN_PW=$(openssl rand -base64 15 | tr -d '/+=')
  cat > "$APP/.env" <<EOF
META_APP_ID=
META_APP_SECRET=
META_GRAPH_VERSION=v23.0
FERNET_KEY=$FERNET
SESSION_SECRET=$SESSION
HTTPS_ONLY=1
DATABASE_URL=sqlite:///$APP/data/agitprop.db
ADMIN_EMAIL=$EMAIL
ADMIN_PASSWORD=$ADMIN_PW
EOF
  cat > "$APP/IDENTIFIANTS.txt" <<EOF
Agitprop - identifiants administrateur
=========================================

URL          : https://$DOMAIN
email        : $EMAIL
mot de passe : $ADMIN_PW

A ranger dans le gestionnaire de mots de passe, puis supprimer ce fichier.
EOF
  chmod 600 "$APP/.env" "$APP/IDENTIFIANTS.txt"
fi
chown -R agitprop:agitprop "$APP"

echo "[5/6] Service systemd"
cat > /etc/systemd/system/agitprop.service <<EOF
[Unit]
Description=Agitprop (La France Humaniste)
After=network.target

[Service]
User=agitprop
Group=agitprop
WorkingDirectory=$APP
ExecStart=$APP/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips=127.0.0.1
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable agitprop >/dev/null 2>&1
systemctl restart agitprop

echo "[6/6] nginx + HTTPS (Let's Encrypt) + pare-feu"
cat > /etc/nginx/sites-available/agitprop <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;
    client_max_body_size 20m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
ln -sf /etc/nginx/sites-available/agitprop /etc/nginx/sites-enabled/agitprop
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx
certbot --nginx -d "$DOMAIN" -m "$EMAIL" --agree-tos --non-interactive --redirect

ufw allow OpenSSH >/dev/null
ufw allow 'Nginx Full' >/dev/null
ufw --force enable >/dev/null

sleep 2
systemctl --no-pager --lines=5 status agitprop || true
echo
echo "=============================================="
echo " TERMINE : https://$DOMAIN"
echo " Identifiants admin : $APP/IDENTIFIANTS.txt (lire, ranger, supprimer)"
echo " Ensuite : META_APP_ID / META_APP_SECRET dans $APP/.env puis"
echo "           systemctl restart agitprop"
echo "=============================================="
