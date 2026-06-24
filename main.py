import os
import re
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from backend.database import init_db
from backend.auth import decode_token
from backend.routers import equipment, maintenance, calibration, inventory, dashboard, attachments
from backend.routers import audit, qr, pmcs, csv_io
from backend.routers import auth_router
from backend.routers import sko as sko_router
from backend.routers import settings as settings_router
from backend.routers import readiness as readiness_router
from backend.routers import reorder as reorder_router
from backend.routers import rolling_stock as rolling_stock_router
from backend.routers import task_attachments as task_attachments_router
from backend.routers import faults as faults_router
from backend.routers import eq_checklists as eq_checklists_router
from backend.routers import commander as commander_router
from backend.notifications import run_daily_check

scheduler = AsyncIOScheduler()

# Paths that don't require authentication
_PUBLIC_PATHS = {"/login", "/api/auth/login", "/commander", "/Commander"}
_PUBLIC_PREFIXES = ("/static", "/uploads", "/sw.js")
_PUBLIC_PMCS_RE = re.compile(r"^/pmcs/\d+$")
# PMCS checklist API calls used from the public QR page
_PUBLIC_API_RE = re.compile(
    r"^/api/(pmcs/(templates/\d+/sessions|sessions/\d+/(complete|archive))|qr/(equipment|pmcs)/\d+)$"
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    scheduler.add_job(run_daily_check, "cron", hour=6, minute=0,
                      id="daily_notify", replace_existing=True)
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="GEAR GUARD", lifespan=lifespan)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path

    # Allow public paths unconditionally
    if path in _PUBLIC_PATHS:
        return await call_next(request)
    if any(path.startswith(p) for p in _PUBLIC_PREFIXES):
        return await call_next(request)
    if _PUBLIC_PMCS_RE.match(path) or _PUBLIC_API_RE.match(path):
        return await call_next(request)

    # Extract token from Authorization header, cookie, or query param (for file downloads)
    auth_header = request.headers.get("Authorization", "")
    token = (auth_header.removeprefix("Bearer ").strip()
             or request.cookies.get("auth_token", "")
             or request.query_params.get("token", ""))

    if token:
        try:
            request.state.user = decode_token(token)
            return await call_next(request)
        except Exception:
            pass  # fall through to unauthenticated handling

    # Unauthenticated — only block API calls; HTML pages let client-side auth handle it
    if path.startswith("/api/"):
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    return await call_next(request)


app.mount("/static", StaticFiles(directory="frontend/static"), name="static")
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

templates = Jinja2Templates(directory="frontend/templates")

app.include_router(auth_router.router)
app.include_router(equipment.router)
app.include_router(maintenance.router)
app.include_router(calibration.router)
app.include_router(inventory.router)
app.include_router(dashboard.router)
app.include_router(attachments.router)
app.include_router(audit.router)
app.include_router(qr.router)
app.include_router(pmcs.router)
app.include_router(csv_io.router)
app.include_router(sko_router.router)
app.include_router(settings_router.router)
app.include_router(readiness_router.router)
app.include_router(reorder_router.router)
app.include_router(rolling_stock_router.router)
app.include_router(task_attachments_router.router)
app.include_router(faults_router.router)
app.include_router(eq_checklists_router.router)
app.include_router(commander_router.router)


@app.get("/sw.js")
async def service_worker():
    return FileResponse(
        "frontend/static/sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


@app.get("/Commander", response_class=HTMLResponse)
async def commander_redirect():
    return RedirectResponse("/commander", status_code=301)


@app.get("/commander", response_class=HTMLResponse)
async def commander_public(request: Request):
    import aiosqlite
    from datetime import date
    db_path = os.getenv("DB_PATH", "maint.db")

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row

        async def scalar(sql, params=()):
            async with db.execute(sql, params) as cur:
                row = await cur.fetchone()
                return row[0] if row else 0

        total_eq    = await scalar("SELECT COUNT(*) FROM equipment WHERE status='active'")
        deadline_eq = await scalar("SELECT COUNT(*) FROM equipment WHERE status='deadline'")
        mc_count    = total_eq - deadline_eq
        mc_pct      = round(mc_count / total_eq * 100) if total_eq else 0

        open_faults   = await scalar("SELECT COUNT(*) FROM fault_reports WHERE status IN ('open','in_progress')")
        urgent_faults = await scalar("""
            SELECT COUNT(*) FROM fault_reports
            WHERE status IN ('open','in_progress')
            AND julianday('now') - julianday(created_at) > 2
        """)
        cal_overdue = await scalar("""
            SELECT COUNT(*) FROM (
                SELECT MAX(id) as id FROM calibration_records GROUP BY equipment_id
            ) l JOIN calibration_records c ON c.id = l.id
            WHERE c.next_due IS NOT NULL AND DATE(c.next_due) < DATE('now')
        """)
        cal_due_14d = await scalar("""
            SELECT COUNT(*) FROM (
                SELECT MAX(id) as id FROM calibration_records GROUP BY equipment_id
            ) l JOIN calibration_records c ON c.id = l.id
            WHERE c.next_due IS NOT NULL
            AND DATE(c.next_due) BETWEEN DATE('now') AND DATE('now', '+14 days')
        """)
        maint_overdue = await scalar("SELECT COUNT(*) FROM maintenance_tasks WHERE status='overdue'")

        async with db.execute("SELECT id, name, status FROM rolling_stock WHERE status != 'retired' ORDER BY name") as cur:
            vehicles = [dict(r) for r in await cur.fetchall()]
        rs_available  = sum(1 for v in vehicles if v["status"] == "available")
        rs_dispatched = sum(1 for v in vehicles if v["status"] == "dispatched")
        rs_down       = sum(1 for v in vehicles if v["status"] in ("maintenance","deadline"))

        async with db.execute("""
            SELECT f.title, e.name as equipment_name,
                   CAST(julianday('now') - julianday(f.created_at) AS INTEGER) as age_days,
                   f.severity
            FROM fault_reports f LEFT JOIN equipment e ON e.id = f.equipment_id
            WHERE f.status IN ('open','in_progress')
            ORDER BY age_days DESC LIMIT 8
        """) as cur:
            top_faults = [dict(r) for r in await cur.fetchall()]

        async with db.execute("""
            SELECT c.next_due, e.name as equipment_name,
                   CAST(julianday('now') - julianday(c.next_due) AS INTEGER) as days_over
            FROM calibration_records c JOIN equipment e ON e.id = c.equipment_id
            WHERE c.id IN (SELECT MAX(id) FROM calibration_records GROUP BY equipment_id)
            AND c.next_due IS NOT NULL AND DATE(c.next_due) <= DATE('now', '+14 days')
            ORDER BY c.next_due ASC LIMIT 8
        """) as cur:
            cal_items = [dict(r) for r in await cur.fetchall()]

        async with db.execute("""
            SELECT category,
                   COUNT(*) as total,
                   SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) as active
            FROM equipment WHERE status IN ('active','deadline')
            GROUP BY category ORDER BY total DESC
        """) as cur:
            by_category = [dict(r) for r in await cur.fetchall()]

        async with db.execute("""
            SELECT t.title, MAX(s.completed_at) as last_run,
                   CAST(julianday('now') - julianday(MAX(s.completed_at)) AS INTEGER) as days_since
            FROM pmcs_templates t
            LEFT JOIN pmcs_sessions s ON s.template_id = t.id AND s.status='completed'
            GROUP BY t.id, t.title ORDER BY last_run ASC NULLS FIRST LIMIT 6
        """) as cur:
            pmcs_status = [dict(r) for r in await cur.fetchall()]

    ctx = {
        "request": request,
        "generated": date.today().strftime("%d %b %Y"),
        "mc_pct": mc_pct, "mc_count": mc_count, "total_eq": total_eq,
        "open_faults": open_faults, "urgent_faults": urgent_faults,
        "cal_overdue": cal_overdue, "cal_due_14d": cal_due_14d,
        "maint_overdue": maint_overdue,
        "rs_available": rs_available, "rs_dispatched": rs_dispatched,
        "rs_down": rs_down, "rs_total": len(vehicles),
        "top_faults": top_faults, "cal_items": cal_items,
        "by_category": by_category, "pmcs_status": pmcs_status,
        "vehicles": vehicles,
    }
    return templates.TemplateResponse("commander_dashboard.html", ctx)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


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
        # Items with equipment names
        async with db.execute("""
            SELECT pi.*, e.name as equipment_name, e.serial_num as equipment_serial
            FROM pmcs_items pi
            LEFT JOIN equipment e ON e.id = pi.equipment_id
            WHERE pi.template_id=?
            ORDER BY pi.equipment_id NULLS LAST, pi.order_index, pi.id
        """, (template_id,)) as cur:
            items = [dict(i) for i in await cur.fetchall()]
        # Linked equipment list (for grouping)
        async with db.execute("""
            SELECT e.id, e.name, e.serial_num, e.category
            FROM pmcs_template_equipment te
            JOIN equipment e ON e.id = te.equipment_id
            WHERE te.template_id=?
            ORDER BY te.order_index, e.name
        """, (template_id,)) as cur:
            linked_equipment = [dict(r) for r in await cur.fetchall()]
    return templates.TemplateResponse(
        "pmcs_checklist.html",
        {"request": request, "template": dict(tmpl), "items": items,
         "linked_equipment": linked_equipment}
    )


@app.get("/labels", response_class=HTMLResponse)
async def labels_page(request: Request, ids: str = ""):
    """QR label print sheet — ?ids=1,2,3 or all equipment if omitted."""
    import aiosqlite
    db_path = os.getenv("DB_PATH", "maint.db")
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        if ids:
            id_list = [int(i) for i in ids.split(",") if i.strip().isdigit()]
            placeholders = ",".join("?" * len(id_list))
            async with db.execute(
                f"SELECT id, name, serial_num, category FROM equipment WHERE id IN ({placeholders}) ORDER BY name",
                id_list
            ) as cur:
                equipment_rows = [dict(r) for r in await cur.fetchall()]
        else:
            async with db.execute(
                "SELECT id, name, serial_num, category FROM equipment ORDER BY name"
            ) as cur:
                equipment_rows = [dict(r) for r in await cur.fetchall()]
    return templates.TemplateResponse(
        "labels.html",
        {"request": request, "equipment_list": equipment_rows}
    )


@app.get("/", response_class=HTMLResponse)
@app.get("/{page}", response_class=HTMLResponse)
async def spa(request: Request, page: str = "dashboard"):
    valid = {"dashboard", "equipment", "maintenance", "calibration", "inventory", "pmcs", "users", "skos", "readiness", "rolling-stock", "faults"}
    if page not in valid:
        page = "dashboard"
    return templates.TemplateResponse("index.html", {"request": request, "page": page})
