# GEAR GUARD — Ubuntu 22.04 Deployment Guide

Target: Ubuntu 22.04 LTS, domain `gear.whitwerx.net`

---

## 1. System dependencies

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3.12 python3.12-venv python3-pip nginx certbot python3-certbot-nginx git
```

> **Python 3.12 note** — Ubuntu 22.04 ships Python 3.10. Install 3.12 via deadsnakes if needed:
> ```bash
> sudo add-apt-repository ppa:deadsnakes/ppa -y
> sudo apt install -y python3.12 python3.12-venv python3.12-distutils
> ```

---

## 2. Clone the repo

```bash
sudo mkdir -p /opt/maint-super
sudo chown $USER:$USER /opt/maint-super
cd /opt/maint-super
git clone https://github.com/dwhit6605-bit/MAINT_TRACKER.git .
```

---

## 3. Python environment

```bash
python3.12 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

---

## 4. Configure environment

```bash
cp .env.example .env
nano .env
```

Minimum required values:

```
DB_PATH=maint.db
BASE_URL=https://gear.whitwerx.net

# Brevo SMTP (optional — comment out to disable email alerts)
NOTIFY_EMAIL_TO=you@example.com
NOTIFY_EMAIL_FROM=your-verified-sender@yourdomain.com
SMTP_HOST=smtp-relay.brevo.com
SMTP_PORT=587
SMTP_USER=your-brevo-login@example.com
SMTP_PASS=your-brevo-smtp-key
NOTIFY_DAYS_AHEAD=7
```

---

## 5. Create upload directories and initialize DB

```bash
mkdir -p uploads/equipment uploads/pmcs
source venv/bin/activate
python - <<'EOF'
import asyncio
from backend.database import init_db
asyncio.run(init_db())
EOF
echo "DB initialized"
```

---

## 6. (Optional) Import seed data from CSV

Place SharePoint CSV exports in `lists/` then run:

```bash
source venv/bin/activate
python scripts/import_csv.py
```

---

## 7. systemd service

```bash
sudo nano /etc/systemd/system/maint-super.service
```

Paste:

```ini
[Unit]
Description=GEAR GUARD
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory=/opt/maint-super
EnvironmentFile=/opt/maint-super/.env
ExecStart=/opt/maint-super/venv/bin/uvicorn main:app --host 127.0.0.1 --port 8001 --workers 2
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo chown -R www-data:www-data /opt/maint-super
sudo systemctl daemon-reload
sudo systemctl enable maint-super
sudo systemctl start maint-super
sudo systemctl status maint-super
```

---

## 8. nginx reverse proxy

```bash
sudo nano /etc/nginx/sites-available/maint-super
```

Paste:

```nginx
server {
    listen 80;
    server_name gear.whitwerx.net;

    client_max_body_size 55M;

    location /uploads/ {
        alias /opt/maint-super/uploads/;
        expires 7d;
        add_header Cache-Control "public";
    }

    location / {
        proxy_pass         http://127.0.0.1:8001;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
}
```

Enable and reload:

```bash
sudo ln -s /etc/nginx/sites-available/maint-super /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

---

## 9. HTTPS with Certbot

```bash
sudo certbot --nginx -d gear.whitwerx.net
```

Certbot auto-edits the nginx config and sets up renewal.

---

## 10. Verify

```bash
curl -I https://gear.whitwerx.net/api/dashboard
# expect HTTP/2 200
```

---

## Updating

```bash
cd /opt/maint-super
git pull
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart maint-super
```

---

## Logs

```bash
sudo journalctl -u maint-super -f
sudo tail -f /var/log/nginx/error.log
```

---

## Backup

```bash
tar czf maint-super-backup-$(date +%Y%m%d).tar.gz /opt/maint-super/maint.db /opt/maint-super/uploads/
```

Daily cron:

```bash
0 2 * * * tar czf /backups/maint-super-$(date +\%Y\%m\%d).tar.gz /opt/maint-super/maint.db /opt/maint-super/uploads/ 2>/dev/null
```
