import os
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from backend.database import init_db
from backend.routers import equipment, maintenance, calibration, inventory, dashboard, attachments
from backend.routers import audit, qr, pmcs
from backend.notifications import run_daily_check

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # Daily notification check at 06:00 UTC
    scheduler.add_job(run_daily_check, "cron", hour=6, minute=0,
                      id="daily_notify", replace_existing=True)
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="MAINT SUPER", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="frontend/static"), name="static")
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

templates = Jinja2Templates(directory="frontend/templates")

app.include_router(equipment.router)
app.include_router(maintenance.router)
app.include_router(calibration.router)
app.include_router(inventory.router)
app.include_router(dashboard.router)
app.include_router(attachments.router)
app.include_router(audit.router)
app.include_router(qr.router)
app.include_router(pmcs.router)


@app.get("/pmcs/{template_id}", response_class=HTMLResponse)
async def pmcs_checklist(request: Request, template_id: int):
    import aiosqlite
    db_path = os.getenv("DB_PATH", "maint.db")
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT t.*, e.name as equipment_name
            FROM pmcs_templates t
            LEFT JOIN equipment e ON e.id = t.equipment_id
            WHERE t.id=?
        """, (template_id,)) as cur:
            tmpl = await cur.fetchone()
        if not tmpl:
            return HTMLResponse("<h2>Checklist not found</h2>", status_code=404)
        async with db.execute(
            "SELECT * FROM pmcs_items WHERE template_id=? ORDER BY order_index, id",
            (template_id,)
        ) as cur:
            items = [dict(i) for i in await cur.fetchall()]
    return templates.TemplateResponse(
        "pmcs_checklist.html",
        {"request": request, "template": dict(tmpl), "items": items}
    )


@app.get("/", response_class=HTMLResponse)
@app.get("/{page}", response_class=HTMLResponse)
async def spa(request: Request, page: str = "dashboard"):
    valid = {"dashboard", "equipment", "maintenance", "calibration", "inventory", "pmcs"}
    if page not in valid:
        page = "dashboard"
    return templates.TemplateResponse("index.html", {"request": request, "page": page})
