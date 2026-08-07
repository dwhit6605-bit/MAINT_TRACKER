"""Pressure cylinders — SCBA and O2 bottle requalification tracking.

A cylinder has two independent clocks:

  * **Hydrostatic requalification** — recurring. DOT requires a pressure test
    every 5 years for most SCBA cylinders (3 years for some steel/aluminum
    specs), so the interval is per-cylinder rather than hard-coded.
  * **Service life** — terminal. Composite (carbon/hoop-wrapped) cylinders are
    condemned 15 years after manufacture no matter how many hydros they pass.
    Steel and aluminum have no expiry, so ``service_life_years`` is nullable.

Cylinders live in the ``equipment`` table; this router adds the date fields and
the test history on top rather than duplicating the 70 existing records.
"""
from datetime import date, datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Optional, List
from backend.database import get_db
from backend.auth import require_tech, require_superadmin

router = APIRouter(prefix="/api/cylinders", tags=["cylinders"])

CYLINDER_CATEGORY = "SCBA / Breathing Apparatus"
DEFAULT_HYDRO_MONTHS = 60      # 5 years
DUE_SOON_DAYS = 90


# ── Models ────────────────────────────────────────────────────────────────────

class CylinderUpdate(BaseModel):
    mfg_date: Optional[str] = None
    cylinder_type: Optional[str] = None
    hydro_interval_months: Optional[int] = None
    service_life_years: Optional[int] = None


class BulkUpdate(BaseModel):
    equipment_ids: List[int]
    mfg_date: Optional[str] = None
    cylinder_type: Optional[str] = None
    hydro_interval_months: Optional[int] = None
    service_life_years: Optional[int] = None


class TestCreate(BaseModel):
    test_type: str = "hydrostatic"
    tested_at: str
    next_due: Optional[str] = None
    result: str = "pass"
    facility: Optional[str] = None
    rin: Optional[str] = None
    notes: Optional[str] = None


# ── Date helpers ──────────────────────────────────────────────────────────────

def _parse(d) -> Optional[date]:
    if not d:
        return None
    try:
        return datetime.strptime(str(d)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _add_months(d: date, months: int) -> date:
    """Calendar-month arithmetic, clamped to the last valid day of the month."""
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    # Step back from the 1st of the following month to clamp Feb 30 -> Feb 28/29
    nxt = date(year + (1 if month == 12 else 0), 1 if month == 12 else month + 1, 1)
    last_day = (nxt - timedelta(days=1)).day
    return date(year, month, min(d.day, last_day))


def _add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:          # Feb 29 -> Feb 28
        return d.replace(year=d.year + years, day=28)


def _assess(cyl: dict, today: Optional[date] = None) -> dict:
    """Derive hydro-due, expiry, and an overall status for one cylinder."""
    today = today or date.today()
    mfg = _parse(cyl.get("mfg_date"))
    interval = cyl.get("hydro_interval_months") or DEFAULT_HYDRO_MONTHS
    life = cyl.get("service_life_years")

    last_pass = _parse(cyl.get("last_hydro"))
    # Fall back to the manufacture date: a new cylinder's clock starts at DOM
    basis = last_pass or mfg
    hydro_due = _add_months(basis, interval) if basis else None
    expires = _add_years(mfg, life) if (mfg and life) else None

    if cyl.get("last_result") in ("fail", "condemned"):
        status, detail = "condemned", "Failed requalification"
    elif expires and today >= expires:
        status, detail = "expired", f"Past {life}-year service life"
    elif hydro_due and today > hydro_due:
        status, detail = "overdue", f"Hydro overdue since {hydro_due}"
    elif hydro_due and (hydro_due - today).days <= DUE_SOON_DAYS:
        status, detail = "due_soon", f"Hydro due {hydro_due}"
    elif not basis:
        status, detail = "unknown", "No manufacture date or hydro on file"
    else:
        status, detail = "current", f"Hydro due {hydro_due}"

    return {
        **cyl,
        "hydro_due":      hydro_due.isoformat() if hydro_due else None,
        "expires":        expires.isoformat() if expires else None,
        "days_to_hydro":  (hydro_due - today).days if hydro_due else None,
        "days_to_expiry": (expires - today).days if expires else None,
        "cyl_status":     status,
        "cyl_detail":     detail,
    }


async def _fetch(db, where: str = "", params: tuple = ()) -> List[dict]:
    async with db.execute(f"""
        SELECT e.id, e.name, e.serial_num, e.status, e.location, e.notes,
               e.mfg_date, e.cylinder_type, e.hydro_interval_months, e.service_life_years,
               (SELECT tested_at FROM cylinder_tests t
                 WHERE t.equipment_id=e.id AND t.result='pass'
                 ORDER BY t.tested_at DESC LIMIT 1) as last_hydro,
               (SELECT result FROM cylinder_tests t
                 WHERE t.equipment_id=e.id ORDER BY t.tested_at DESC LIMIT 1) as last_result,
               (SELECT COUNT(*) FROM cylinder_tests t WHERE t.equipment_id=e.id) as test_count
        FROM equipment e
        WHERE e.category=? {where}
        ORDER BY e.name, e.serial_num
    """, (CYLINDER_CATEGORY, *params)) as cur:
        return [_assess(dict(r)) for r in await cur.fetchall()]


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("")
async def list_cylinders(db=Depends(get_db)):
    rows = await _fetch(db)
    counts = {}
    for r in rows:
        counts[r["cyl_status"]] = counts.get(r["cyl_status"], 0) + 1
    # Anything that must not be filled
    grounded = sum(counts.get(k, 0) for k in ("overdue", "expired", "condemned"))
    return {"cylinders": rows, "counts": counts, "total": len(rows), "grounded": grounded}


@router.put("/{eq_id}")
async def update_cylinder(eq_id: int, request: Request, data: CylinderUpdate, db=Depends(get_db)):
    require_tech(request)
    async with db.execute(
        "SELECT id FROM equipment WHERE id=? AND category=?", (eq_id, CYLINDER_CATEGORY)
    ) as cur:
        if not await cur.fetchone():
            raise HTTPException(404, "Cylinder not found")
    await db.execute("""
        UPDATE equipment SET mfg_date=?, cylinder_type=?, hydro_interval_months=?,
            service_life_years=?, updated_at=datetime('now')
        WHERE id=?
    """, (data.mfg_date, data.cylinder_type, data.hydro_interval_months,
          data.service_life_years, eq_id))
    await db.commit()
    return {"ok": True}


@router.post("/bulk")
async def bulk_update(request: Request, data: BulkUpdate, db=Depends(get_db)):
    """Apply the same manufacture date / type / intervals to many cylinders.

    Only the fields actually supplied are written, so a bulk 'set type to
    composite' will not blank out per-cylinder manufacture dates.
    """
    require_tech(request)
    if not data.equipment_ids:
        raise HTTPException(400, "No cylinders selected")

    sets, vals = [], []
    for col in ("mfg_date", "cylinder_type", "hydro_interval_months", "service_life_years"):
        v = getattr(data, col)
        if v is not None:
            sets.append(f"{col}=?")
            vals.append(v)
    if not sets:
        raise HTTPException(400, "Nothing to update")

    marks = ",".join("?" for _ in data.equipment_ids)
    await db.execute(
        f"UPDATE equipment SET {', '.join(sets)}, updated_at=datetime('now') "
        f"WHERE category=? AND id IN ({marks})",
        (*vals, CYLINDER_CATEGORY, *data.equipment_ids),
    )
    await db.commit()
    return {"ok": True, "updated": len(data.equipment_ids)}


@router.get("/{eq_id}/tests")
async def list_tests(eq_id: int, db=Depends(get_db)):
    async with db.execute(
        "SELECT * FROM cylinder_tests WHERE equipment_id=? ORDER BY tested_at DESC", (eq_id,)
    ) as cur:
        return [dict(r) for r in await cur.fetchall()]


@router.post("/{eq_id}/tests", status_code=201)
async def create_test(eq_id: int, request: Request, data: TestCreate, db=Depends(get_db)):
    require_tech(request)
    async with db.execute(
        "SELECT hydro_interval_months FROM equipment WHERE id=? AND category=?",
        (eq_id, CYLINDER_CATEGORY),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Cylinder not found")

    tested = _parse(data.tested_at)
    if not tested:
        raise HTTPException(400, "tested_at must be YYYY-MM-DD")
    if tested > date.today():
        raise HTTPException(400, "Test date cannot be in the future")

    next_due = data.next_due
    if not next_due and data.result == "pass":
        interval = row["hydro_interval_months"] or DEFAULT_HYDRO_MONTHS
        next_due = _add_months(tested, interval).isoformat()

    async with db.execute("""
        INSERT INTO cylinder_tests
            (equipment_id,test_type,tested_at,next_due,result,facility,rin,notes)
        VALUES (?,?,?,?,?,?,?,?)
    """, (eq_id, data.test_type, data.tested_at, next_due, data.result,
          data.facility, data.rin, data.notes)) as cur:
        tid = cur.lastrowid

    # A condemned cylinder must not stay in the active pool
    if data.result in ("fail", "condemned"):
        await db.execute(
            "UPDATE equipment SET status='retired', updated_at=datetime('now') WHERE id=?", (eq_id,)
        )
    await db.commit()
    return {"id": tid, "next_due": next_due}


@router.delete("/tests/{tid}")
async def delete_test(tid: int, request: Request, db=Depends(get_db)):
    require_superadmin(request)
    async with db.execute("SELECT id FROM cylinder_tests WHERE id=?", (tid,)) as cur:
        if not await cur.fetchone():
            raise HTTPException(404, "Test record not found")
    await db.execute("DELETE FROM cylinder_tests WHERE id=?", (tid,))
    await db.commit()
    return {"ok": True}
