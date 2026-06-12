#!/usr/bin/env bash
# MAINT SUPER — one-shot VPS installer
# Usage: curl -fsSL https://raw.githubusercontent.com/dwhit6605-bit/MAINT_TRACKER/main/install.sh | sudo bash
# Or:    sudo bash install.sh
set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
REPO="https://github.com/dwhit6605-bit/MAINT_TRACKER.git"
INSTALL_DIR="/opt/maint-super"
SERVICE_NAME="maint-super"
APP_PORT="8001"
APP_USER="www-data"

# ── Helpers ───────────────────────────────────────────────────────────────────
info()  { echo -e "\033[1;34m[•]\033[0m $*"; }
ok()    { echo -e "\033[1;32m[✓]\033[0m $*"; }
err()   { echo -e "\033[1;31m[✗]\033[0m $*" >&2; exit 1; }
ask()   { echo -e "\033[1;33m[?]\033[0m $*"; }

[[ $EUID -ne 0 ]] && err "Run as root: sudo bash install.sh"

# ── Collect config interactively ──────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════╗"
echo "║        MAINT SUPER  —  Installer         ║"
echo "╚══════════════════════════════════════════╝"
echo ""

ask "Domain name (e.g. maint.whitwerx.net):"
read -r DOMAIN

ask "Notification email address (alerts sent here):"
read -r NOTIFY_TO

ask "From address for outgoing email (must be verified in Brevo):"
read -r NOTIFY_FROM

ask "Brevo SMTP login (your Brevo account email):"
read -r SMTP_USER

ask "Brevo SMTP key (Settings → SMTP & API → SMTP Keys):"
read -r -s SMTP_PASS
echo ""

ask "Days ahead to warn for due maintenance/calibration [default: 7]:"
read -r DAYS_AHEAD
DAYS_AHEAD="${DAYS_AHEAD:-7}"

# ── System packages ───────────────────────────────────────────────────────────
info "Updating system packages…"
apt-get update -qq
apt-get install -y -qq software-properties-common
add-apt-repository -y ppa:deadsnakes/ppa > /dev/null 2>&1
apt-get update -qq
apt-get install -y -qq \
    python3.12 python3.12-venv \
    nginx certbot python3-certbot-nginx git curl
ok "System packages installed"

# ── Clone / update repo ───────────────────────────────────────────────────────
if [[ -d "$INSTALL_DIR/.git" ]]; then
    info "Updating existing repo at $INSTALL_DIR…"
    git -C "$INSTALL_DIR" pull --ff-only
else
    info "Cloning repo to $INSTALL_DIR…"
    mkdir -p "$INSTALL_DIR"
    git clone "$REPO" "$INSTALL_DIR"
fi
ok "Repo ready"

# ── Python venv + deps ────────────────────────────────────────────────────────
info "Creating Python 3.12 virtual environment…"
python3.12 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"
ok "Python environment ready"

# ── Write .env ────────────────────────────────────────────────────────────────
info "Writing .env…"
cat > "$INSTALL_DIR/.env" <<EOF
DB_PATH=maint.db
BASE_URL=https://${DOMAIN}
NOTIFY_EMAIL_TO=${NOTIFY_TO}
NOTIFY_EMAIL_FROM=${NOTIFY_FROM}
SMTP_HOST=smtp-relay.brevo.com
SMTP_PORT=587
SMTP_USER=${SMTP_USER}
SMTP_PASS=${SMTP_PASS}
NOTIFY_DAYS_AHEAD=${DAYS_AHEAD}
EOF
chmod 600 "$INSTALL_DIR/.env"
ok ".env written"

# ── Uploads dirs + init DB ────────────────────────────────────────────────────
info "Creating upload directories and initialising database…"
mkdir -p "$INSTALL_DIR/uploads/equipment" "$INSTALL_DIR/uploads/pmcs" "$INSTALL_DIR/frontend/static"
"$INSTALL_DIR/venv/bin/python" - <<'PYEOF'
import asyncio, sys, os
sys.path.insert(0, os.getcwd())
os.chdir('/opt/maint-super')
from backend.database import init_db
asyncio.run(init_db())
PYEOF
ok "Database initialised"

# ── Ownership ─────────────────────────────────────────────────────────────────
chown -R "$APP_USER":"$APP_USER" "$INSTALL_DIR"
ok "Ownership set to $APP_USER"

# ── systemd service ───────────────────────────────────────────────────────────
info "Installing systemd service…"
cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=MAINT SUPER
After=network.target

[Service]
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${INSTALL_DIR}/.env
ExecStart=${INSTALL_DIR}/venv/bin/uvicorn main:app --host 127.0.0.1 --port ${APP_PORT} --workers 2
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"
sleep 2
systemctl is-active --quiet "$SERVICE_NAME" && ok "Service running" \
    || err "Service failed to start — run: journalctl -u $SERVICE_NAME -n 30"

# ── nginx ─────────────────────────────────────────────────────────────────────
info "Configuring nginx…"
cat > "/etc/nginx/sites-available/${SERVICE_NAME}" <<EOF
server {
    listen 80;
    server_name ${DOMAIN};
    client_max_body_size 55M;

    location /uploads/ {
        alias ${INSTALL_DIR}/uploads/;
    }

    location / {
        proxy_pass         http://127.0.0.1:${APP_PORT};
        proxy_set_header   Host \$host;
        proxy_set_header   X-Real-IP \$remote_addr;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60s;
    }
}
EOF

ln -sf "/etc/nginx/sites-available/${SERVICE_NAME}" \
       "/etc/nginx/sites-enabled/${SERVICE_NAME}"

# Remove default site if still linked
rm -f /etc/nginx/sites-enabled/default

nginx -t && systemctl reload nginx
ok "nginx configured"

# ── HTTPS via certbot ─────────────────────────────────────────────────────────
info "Obtaining TLS certificate for ${DOMAIN}…"
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos \
    --email "$NOTIFY_TO" --redirect
ok "HTTPS enabled"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════╗"
echo "║           Installation Complete          ║"
echo "╚══════════════════════════════════════════╝"
echo ""
ok "MAINT SUPER is live at:  https://${DOMAIN}"
echo ""
echo "  Logs:    journalctl -u ${SERVICE_NAME} -f"
echo "  Restart: systemctl restart ${SERVICE_NAME}"
echo "  Update:  git -C ${INSTALL_DIR} pull && systemctl restart ${SERVICE_NAME}"
echo ""
