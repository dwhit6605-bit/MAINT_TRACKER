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
from backend.routers import power_assets as power_assets_router
from backend.routers import cylinders as cylinders_router
from backend.routers import supcen as supcen_router
from backend.routers import task_attachments as task_attachments_router
from backend.routers import faults as faults_router
from backend.routers import eq_checklists as eq_checklists_router
from backend.routers import commander as commander_router
from backend.routers import calendar_view as calendar_router
from backend.routers import hazmat as hazmat_router
from backend.notifications import run_daily_check

scheduler = AsyncIOScheduler()

# Paths that don't require authentication
_PUBLIC_PATHS = {"/login", "/api/auth/login", "/api/commander"}
_PUBLIC_PREFIXES = ("/static", "/uploads", "/sw.js")
_PUBLIC_PMCS_RE = re.compile(r"^/pmcs/\d+$")
# PMCS checklist API calls used from the public QR page
_PUBLIC_API_RE = re.compile(
    r"^/api/(pmcs/(templates/\d+/sessions|sessions/\d+/(complete|archive))|qr/(equipment|pmcs|location|inventory)/\d+)$"
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
        except Exception:
            pass  # bad/expired token — fall through to unauthenticated handling
        else:
            # Deliberately outside the try: wrapping call_next here reported every
            # unhandled endpoint error as "Not authenticated", which hid the real
            # cause. A genuine 500 must look like a 500.
            return await call_next(request)

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
app.include_router(power_assets_router.router)
app.include_router(cylinders_router.router)
app.include_router(supcen_router.router)
app.include_router(task_attachments_router.router)
app.include_router(faults_router.router)
app.include_router(eq_checklists_router.router)
app.include_router(commander_router.router)
app.include_router(calendar_router.router)
app.include_router(hazmat_router.router)


@app.get("/sw.js")
async def service_worker():
    return FileResponse(
        "frontend/static/sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


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


@app.get("/location-sheet/{loc_id}", response_class=HTMLResponse)
async def location_sheet_page(request: Request, loc_id: int):
    """Printable inventory sheet for one area — end items plus consumables.

    Rendered server-side rather than printing the SPA, so it lands on paper as a
    signable document with tick boxes, the way the hand receipt does.
    """
    import aiosqlite
    from datetime import date
    db_path = os.getenv("DB_PATH", "maint.db")
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys = ON")
        async with db.execute("SELECT * FROM inventory_locations WHERE id=?", (loc_id,)) as cur:
            loc = await cur.fetchone()
        if not loc:
            return HTMLResponse("<h2>Location not found</h2>", status_code=404)
        async with db.execute("""
            SELECT e.name, e.category, e.serial_num, e.model, e.assigned_to,
                   e.out_for, e.out_since, e.expected_return,
                   (SELECT r.next_due FROM calibration_records r
                     WHERE r.equipment_id=e.id ORDER BY r.calibrated_at DESC LIMIT 1) AS cal_due,
                   (SELECT COUNT(*) FROM maintenance_tasks m
                     WHERE m.equipment_id=e.id AND m.status='overdue') AS overdue_tasks,
                   (SELECT COUNT(*) FROM maintenance_tasks m
                     WHERE m.equipment_id=e.id AND m.status IN ('pending','overdue')) AS open_tasks
            FROM equipment e WHERE e.location_id=? AND e.status<>'retired'
            ORDER BY e.category, e.name
        """, (loc_id,)) as cur:
            equipment = [dict(r) for r in await cur.fetchall()]
        async with db.execute("""
            SELECT i.name, i.part_number, i.unit, i.min_stock, i.quantity AS total,
                   s.quantity AS here, c.nsn AS catalog_nsn
            FROM inventory_stock s
            JOIN inventory_items i ON i.id=s.item_id
            LEFT JOIN supcen_catalog c ON c.id=i.catalog_id
            WHERE s.location_id=? AND s.quantity<>0 ORDER BY i.name
        """, (loc_id,)) as cur:
            stock = [dict(r) for r in await cur.fetchall()]

    today = date.today().isoformat()
    for e in equipment:
        notes, absent = [], bool(e["out_for"])
        if absent:
            n = f"OUT: {str(e['out_for']).upper()}"
            if e["expected_return"]:
                n += f" — due back {e['expected_return']}"
            elif e["out_since"]:
                n += f" — since {e['out_since']}"
            notes.append(n)
        if e["cal_due"] and e["cal_due"] < today:
            notes.append(f"Cal overdue {e['cal_due']}")
        elif not e["cal_due"]:
            notes.append("No cal record")
        if e["overdue_tasks"]:
            notes.append(f"{e['overdue_tasks']} overdue task(s)")
        elif e["open_tasks"]:
            notes.append(f"{e['open_tasks']} open task(s)")
        if e["assigned_to"]:
            notes.append(f"Signed to {e['assigned_to']}")
        e["notes"], e["absent"] = notes, absent
    for i in stock:
        i["low"] = bool(i["min_stock"] and i["total"] <= i["min_stock"])

    return templates.TemplateResponse(
        "location_sheet.html",
        {"request": request, "loc": dict(loc), "equipment": equipment,
         "stock": stock, "today": today,
         "absent": sum(1 for e in equipment if e["absent"])},
    )


@app.get("/labels", response_class=HTMLResponse)
async def labels_page(request: Request, ids: str = "", type: str = "equipment",
                      loc: str = ""):
    """QR label print sheet. The page fetches its own rows client-side; these
    params just tell it which set to build.

    type=equipment (default) | locations | inventory   ·   loc= narrows
    type=inventory to a single shelf, which is the usual reprint case.
    """
    return templates.TemplateResponse(
        "labels.html",
        {"request": request, "label_type": type, "preselect_ids": ids, "preselect_loc": loc},
    )


@app.get("/", response_class=HTMLResponse)
@app.get("/{page}", response_class=HTMLResponse)
async def spa(request: Request, page: str = "dashboard"):
    valid = {"dashboard", "equipment", "maintenance", "calibration", "inventory", "pmcs", "users", "skos", "readiness", "rolling-stock", "power", "cylinders", "supcen", "faults", "calendar", "commander", "hazmat"}
    if page not in valid:
        page = "dashboard"
    return templates.TemplateResponse("index.html", {"request": request, "page": page})
