"""App settings (SMTP, etc.) — stored in DB, admin-only writes."""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from typing import Optional
from backend.database import get_db
from backend.auth import require_admin

router = APIRouter(prefix="/api/settings", tags=["settings"])

SMTP_KEYS = ["NOTIFY_EMAIL_TO", "NOTIFY_EMAIL_FROM", "SMTP_HOST",
             "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "NOTIFY_DAYS_AHEAD"]


class SmtpSettings(BaseModel):
    NOTIFY_EMAIL_TO: Optional[str] = None
    NOTIFY_EMAIL_FROM: Optional[str] = None
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: Optional[str] = None
    SMTP_USER: Optional[str] = None
    SMTP_PASS: Optional[str] = None
    NOTIFY_DAYS_AHEAD: Optional[str] = None


@router.get("/smtp")
async def get_smtp(request: Request, db=Depends(get_db)):
    require_admin(request)
    async with db.execute(
        f"SELECT key, value FROM app_settings WHERE key IN ({','.join('?'*len(SMTP_KEYS))})",
        SMTP_KEYS
    ) as cur:
        rows = {r["key"]: r["value"] for r in await cur.fetchall()}
    # Mask password — return asterisks if set, empty if not
    result = {k: rows.get(k, "") for k in SMTP_KEYS}
    if result.get("SMTP_PASS"):
        result["SMTP_PASS"] = "••••••••"
    return result


@router.put("/smtp")
async def save_smtp(request: Request, data: SmtpSettings, db=Depends(get_db)):
    require_admin(request)
    updates = data.model_dump(exclude_none=True)
    # Don't overwrite password if the masked placeholder was submitted
    if updates.get("SMTP_PASS", "").startswith("•"):
        updates.pop("SMTP_PASS", None)
    for key, value in updates.items():
        if key not in SMTP_KEYS:
            continue
        await db.execute("""
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """, (key, value or None))
    await db.commit()
    return {"ok": True}


@router.post("/smtp/test")
async def test_smtp(request: Request, db=Depends(get_db)):
    require_admin(request)
    from backend.notifications import _load_settings, _send
    cfg = await _load_settings()
    if not (cfg.get("SMTP_HOST") and cfg.get("NOTIFY_EMAIL_TO")):
        from fastapi import HTTPException
        raise HTTPException(400, "SMTP not configured — save settings first")
    try:
        _send(
            "MAINT SUPER — Test Email",
            "<p>If you received this, your SMTP settings are working correctly.</p>",
            to=cfg["NOTIFY_EMAIL_TO"], from_=cfg["NOTIFY_EMAIL_FROM"],
            host=cfg["SMTP_HOST"], port=int(cfg.get("SMTP_PORT") or 587),
            user=cfg["SMTP_USER"], password=cfg["SMTP_PASS"],
        )
    except Exception as exc:
        from fastapi import HTTPException
        raise HTTPException(500, str(exc))
    return {"ok": True}


async def load_smtp_settings(db) -> dict:
    """Return merged dict: DB values override env vars. Used by notifications."""
    async with db.execute(
        f"SELECT key, value FROM app_settings WHERE key IN ({','.join('?'*len(SMTP_KEYS))})",
        SMTP_KEYS
    ) as cur:
        return {r["key"]: r["value"] for r in await cur.fetchall() if r["value"]}
