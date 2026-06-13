import os
import re
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
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
from backend.notifications import run_daily_check

scheduler = AsyncIOScheduler()

# Paths that don't require authentication
_PUBLIC_PATHS = {"/login", "/api/auth/login"}
_PUBLIC_PREFIXES = ("/static", "/uploads")
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


app = FastAPI(title="MAINT SUPER", lifespan=lifespan)


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
    valid = {"dashboard", "equipment", "maintenance", "calibration", "inventory", "pmcs", "users", "skos", "readiness", "rolling-stock"}
    if page not in valid:
        page = "dashboard"
    return templates.TemplateResponse("index.html", {"request": request, "page": page})
