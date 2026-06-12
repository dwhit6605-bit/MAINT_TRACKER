"""
Daily notification digest — emails overdue and soon-due items.
Configure via .env:
  NOTIFY_EMAIL_TO   = recipient@example.com
  NOTIFY_EMAIL_FROM = your-verified-sender@yourdomain.com
  SMTP_HOST         = smtp-relay.brevo.com
  SMTP_PORT         = 587
  SMTP_USER         = your-brevo-login-email@example.com
  SMTP_PASS         = your-brevo-smtp-key   (Settings → SMTP & API → SMTP Keys)
  NOTIFY_DAYS_AHEAD = 7   (default: warn this many days before due)
"""
import os
import smtplib
import aiosqlite
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta

DB_PATH = os.getenv("DB_PATH", "maint.db")


async def _load_settings() -> dict:
    """Merge DB app_settings over env vars — DB values take precedence."""
    cfg = {
        "NOTIFY_EMAIL_TO":   os.getenv("NOTIFY_EMAIL_TO", ""),
        "NOTIFY_EMAIL_FROM": os.getenv("NOTIFY_EMAIL_FROM", ""),
        "SMTP_HOST":         os.getenv("SMTP_HOST", ""),
        "SMTP_PORT":         os.getenv("SMTP_PORT", "587"),
        "SMTP_USER":         os.getenv("SMTP_USER", ""),
        "SMTP_PASS":         os.getenv("SMTP_PASS", ""),
        "NOTIFY_DAYS_AHEAD": os.getenv("NOTIFY_DAYS_AHEAD", "7"),
    }
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT key, value FROM app_settings WHERE value IS NOT NULL") as cur:
                for row in await cur.fetchall():
                    cfg[row["key"]] = row["value"]
    except Exception:
        pass
    return cfg


def _send(subject: str, html: str, *, to: str, from_: str,
          host: str, port: int, user: str, password: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = from_
    msg["To"]      = to
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP(host, port) as s:
        s.ehlo()
        s.starttls()
        s.login(user, password)
        s.sendmail(from_, to, msg.as_string())


async def run_daily_check():
    cfg   = await _load_settings()
    TO        = cfg["NOTIFY_EMAIL_TO"]
    FROM      = cfg["NOTIFY_EMAIL_FROM"]
    SMTP_HOST = cfg["SMTP_HOST"]
    SMTP_PORT = int(cfg.get("SMTP_PORT") or 587)
    SMTP_USER = cfg["SMTP_USER"]
    SMTP_PASS = cfg["SMTP_PASS"]
    DAYS      = int(cfg.get("NOTIFY_DAYS_AHEAD") or 7)

    if not (TO and FROM and SMTP_HOST):
        return   # not configured — skip silently

    horizon = (datetime.utcnow().date() + timedelta(days=DAYS)).isoformat()
    today   = datetime.utcnow().date().isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        async with db.execute("""
            SELECT m.title, m.next_due, m.status, e.name as equipment_name, e.location
            FROM maintenance_tasks m
            JOIN equipment e ON e.id = m.equipment_id
            WHERE m.status IN ('overdue','pending')
              AND (m.next_due IS NULL OR m.next_due <= ?)
            ORDER BY m.next_due ASC
        """, (horizon,)) as cur:
            maint = [dict(r) for r in await cur.fetchall()]

        # Latest cal record per equipment — flag if overdue or due within horizon
        async with db.execute("""
            SELECT c.next_due, c.result, e.name as equipment_name, e.serial_num, e.location,
                   CASE WHEN c.next_due < ? THEN 'overdue' ELSE 'due_soon' END as cal_status
            FROM calibration_records c
            JOIN equipment e ON e.id = c.equipment_id
            WHERE c.next_due IS NOT NULL AND c.next_due <= ?
              AND c.id = (
                SELECT id FROM calibration_records c2
                WHERE c2.equipment_id = c.equipment_id
                ORDER BY calibrated_at DESC LIMIT 1
              )
            ORDER BY c.next_due ASC
        """, (today, horizon)) as cur:
            cals = [dict(r) for r in await cur.fetchall()]

    if not maint and not cals:
        return

    def _row_color(due):
        if not due or due < today:
            return "#fef2f2"
        return "#fffbeb"

    maint_rows = "".join(f"""
        <tr style="background:{_row_color(r['next_due'])}">
          <td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;">{r['equipment_name']}</td>
          <td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;">{r['title']}</td>
          <td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;">{r['next_due'] or '—'}</td>
          <td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;">{r['status'].upper()}</td>
          <td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;">{r['location'] or '—'}</td>
        </tr>""" for r in maint)

    cal_rows = "".join(f"""
        <tr style="background:{_row_color(r['next_due'])}">
          <td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;">{r['equipment_name']}</td>
          <td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;">{r['serial_num'] or '—'}</td>
          <td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;">{r['next_due']}</td>
          <td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;font-weight:700;color:{'#dc2626' if r['cal_status']=='overdue' else '#92400e'};">{r['cal_status'].upper().replace('_',' ')}</td>
          <td style="padding:6px 10px;border-bottom:1px solid #e5e7eb;">{r['location'] or '—'}</td>
        </tr>""" for r in cals)

    html = f"""
    <html><body style="font-family:sans-serif;color:#111;">
    <h2 style="color:#1e3a5f;">MAINT SUPER — Daily Alert Digest</h2>
    <p style="color:#6b7280;">{datetime.utcnow().strftime('%B %d, %Y')} · Items overdue or due within {DAYS} days</p>

    {'<h3 style="margin-top:1.5rem;">⚙️ Maintenance</h3><table style="width:100%;border-collapse:collapse;font-size:0.9rem;"><thead><tr style="background:#1e3a5f;color:#fff;"><th style="padding:8px 10px;text-align:left;">Equipment</th><th style="padding:8px 10px;text-align:left;">Task</th><th style="padding:8px 10px;text-align:left;">Due</th><th style="padding:8px 10px;text-align:left;">Status</th><th style="padding:8px 10px;text-align:left;">Location</th></tr></thead><tbody>' + maint_rows + '</tbody></table>' if maint else ''}

    {'<h3 style="margin-top:1.5rem;">🔬 Calibration</h3><table style="width:100%;border-collapse:collapse;font-size:0.9rem;"><thead><tr style="background:#1e3a5f;color:#fff;"><th style="padding:8px 10px;text-align:left;">Equipment</th><th style="padding:8px 10px;text-align:left;">Serial #</th><th style="padding:8px 10px;text-align:left;">Due</th><th style="padding:8px 10px;text-align:left;">Status</th><th style="padding:8px 10px;text-align:left;">Location</th></tr></thead><tbody>' + cal_rows + '</tbody></table>' if cals else ''}

    <p style="margin-top:2rem;font-size:0.8rem;color:#9ca3af;">Sent by MAINT SUPER · maint.whitwerx.net</p>
    </body></html>"""

    subject = f"MAINT SUPER Alert — {len(maint)} maintenance, {len(cals)} calibration items due"
    _send(subject, html, to=TO, from_=FROM,
          host=SMTP_HOST, port=SMTP_PORT, user=SMTP_USER, password=SMTP_PASS)
