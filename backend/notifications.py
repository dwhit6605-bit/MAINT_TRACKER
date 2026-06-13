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

        async with db.execute("""
            SELECT v.year, v.make, v.model, v.tag_number, v.license_plate,
                   i.operator_name, i.date_out, i.beginning_mileage
            FROM rolling_stock v
            LEFT JOIN vehicle_inspections i ON i.id = (
                SELECT id FROM vehicle_inspections WHERE vehicle_id=v.id
                ORDER BY created_at DESC LIMIT 1
            )
            WHERE v.status = 'dispatched'
            ORDER BY i.date_out ASC
        """) as cur:
            dispatched = [dict(r) for r in await cur.fetchall()]

    if not maint and not cals and not dispatched:
        return

    def _td(val, color=None, bold=False):
        s = f"padding:6px 10px;border-bottom:1px solid #e5e7eb;"
        if color: s += f"color:{color};"
        if bold:  s += "font-weight:700;"
        return f'<td style="{s}">{val}</td>'

    def _row_bg(due):
        return "#fef2f2" if (not due or due < today) else "#fffbeb"

    def _table(headers, rows_html):
        ths = "".join(f'<th style="padding:8px 10px;text-align:left;background:#1e3a5f;color:#fff;">{h}</th>' for h in headers)
        return f'<table style="width:100%;border-collapse:collapse;font-size:0.875rem;margin-top:0.5rem;"><thead><tr>{ths}</tr></thead><tbody>{rows_html}</tbody></table>'

    maint_rows = "".join(
        f'<tr style="background:{_row_bg(r["next_due"])}">'
        + _td(r["equipment_name"])
        + _td(r["title"])
        + _td(r["next_due"] or "No date")
        + _td(r["status"].upper(), color="#dc2626" if r["status"]=="overdue" else "#92400e", bold=True)
        + _td(r["location"] or "—")
        + "</tr>"
        for r in maint
    )

    cal_rows = "".join(
        f'<tr style="background:{_row_bg(r["next_due"])}">'
        + _td(r["equipment_name"])
        + _td(r["serial_num"] or "—")
        + _td(r["next_due"])
        + _td(r["cal_status"].upper().replace("_"," "), color="#dc2626" if r["cal_status"]=="overdue" else "#92400e", bold=True)
        + _td(r["location"] or "—")
        + "</tr>"
        for r in cals
    )

    rs_rows = "".join(
        f'<tr style="background:#f0f9ff">'
        + _td(f"{r['year'] or ''} {r['make']} {r['model']}".strip())
        + _td(r["tag_number"] or "—")
        + _td(r["license_plate"] or "—")
        + _td(r["operator_name"] or "—")
        + _td(r["date_out"] or "—")
        + "</tr>"
        for r in dispatched
    )

    date_str = datetime.utcnow().strftime("%B %d, %Y")
    summary_parts = []
    if maint:      summary_parts.append(f"{len(maint)} maintenance")
    if cals:       summary_parts.append(f"{len(cals)} calibration")
    if dispatched: summary_parts.append(f"{len(dispatched)} vehicle{'s' if len(dispatched)!=1 else ''} out")
    subject = f"GEAR GUARD — {', '.join(summary_parts)}"

    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:Arial,Helvetica,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:24px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.1);">

  <!-- Header -->
  <tr><td style="background:#1e3a5f;padding:24px 32px;">
    <div style="font-size:20px;font-weight:700;color:#fff;letter-spacing:.5px;">GEAR GUARD</div>
    <div style="font-size:13px;color:rgba(255,255,255,.7);margin-top:4px;">Daily Alert Digest · {date_str}</div>
  </td></tr>

  <!-- Body -->
  <tr><td style="padding:24px 32px;">

    {'<h3 style="margin:0 0 8px;color:#1e3a5f;font-size:15px;">⚙️ Maintenance — ' + str(len(maint)) + ' item' + ('s' if len(maint)!=1 else '') + ' due</h3>' + _table(['Equipment','Task','Due Date','Status','Location'], maint_rows) if maint else ''}

    {'<h3 style="margin:24px 0 8px;color:#1e3a5f;font-size:15px;">🔬 Calibration — ' + str(len(cals)) + ' item' + ('s' if len(cals)!=1 else '') + ' due</h3>' + _table(['Equipment','Serial #','Due Date','Status','Location'], cal_rows) if cals else ''}

    {'<h3 style="margin:24px 0 8px;color:#1e3a5f;font-size:15px;">🚗 Rolling Stock — ' + str(len(dispatched)) + ' vehicle' + ('s' if len(dispatched)!=1 else '') + ' currently dispatched</h3>' + _table(['Vehicle','Tag #','Plate','Operator','Date Out'], rs_rows) if dispatched else ''}

    <div style="margin-top:24px;padding-top:16px;border-top:1px solid #e5e7eb;font-size:12px;color:#9ca3af;">
      Sent automatically by GEAR GUARD &nbsp;·&nbsp;
      <a href="https://maint.whitwerx.net" style="color:#1e3a5f;">maint.whitwerx.net</a>
    </div>
  </td></tr>

</table>
</td></tr></table>
</body></html>"""

    _send(subject, html, to=TO, from_=FROM,
          host=SMTP_HOST, port=SMTP_PORT, user=SMTP_USER, password=SMTP_PASS)
