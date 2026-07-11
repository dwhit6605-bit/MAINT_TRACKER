"""Hazmat suit inventory, pressure testing, and assignment tracking."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
from backend.database import get_db
from backend.auth import require_tech, require_superadmin

router = APIRouter(prefix="/api/hazmat", tags=["hazmat"])

SUIT_SHELF_LIFE = {
    "Level A": 10,
    "Level B": 10,
    "MT94":    7,
    "GORE":    10,
    "Paper":   None,
}

VALID_STATUS = {"serviceable", "out_for_test", "expended", "condemned"}


class SuitCreate(BaseModel):
    suit_type:        str
    model:            Optional[str] = None
    size:             str
    serial_num:       Optional[str] = None
    manufacture_date: Optional[str] = None
    shelf_life_years: Optional[float] = None
    expiry_date:      Optional[str] = None
    status:           str = "serviceable"
    notes:            Optional[str] = None


class SuitUpdate(SuitCreate):
    assigned_to:   Optional[str] = None
    assigned_date: Optional[str] = None


class TestRecord(BaseModel):
    tested_date: str
    tested_by:   Optional[str] = None
    result:      str = "pass"
    next_due:    Optional[str] = None
    notes:       Optional[str] = None


class AssignBody(BaseModel):
    assigned_to:  Optional[str] = None  # None = return
    issued_date:  Optional[str] = None
    returned_date: Optional[str] = None
    notes:        Optional[str] = None


class PaperAdjust(BaseModel):
    quantity: int


# ── Dashboard ────────────────────────────────────────────────────────────────

@router.get("/dashboard")
async def hazmat_dashboard(db=Depends(get_db)):
    async def scalar(sql, params=()):
        async with db.execute(sql, params) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

    total        = await scalar("SELECT COUNT(*) FROM hazmat_suits WHERE status NOT IN ('expended','condemned')")
    serviceable  = await scalar("SELECT COUNT(*) FROM hazmat_suits WHERE status='serviceable'")
    assigned     = await scalar("SELECT COUNT(*) FROM hazmat_suits WHERE status='serviceable' AND assigned_to IS NOT NULL AND assigned_to != ''")
    expended_ytd = await scalar("SELECT COUNT(*) FROM hazmat_suits WHERE status IN ('expended','condemned') AND updated_at >= date('now','start of year')")

    overdue_test = await scalar("""
        SELECT COUNT(*) FROM hazmat_suits s
        WHERE s.status = 'serviceable'
        AND (
            SELECT next_due FROM hazmat_suit_tests WHERE suit_id=s.id ORDER BY id DESC LIMIT 1
        ) < date('now')
    """)
    due_30 = await scalar("""
        SELECT COUNT(*) FROM hazmat_suits s
        WHERE s.status = 'serviceable'
        AND (
            SELECT next_due FROM hazmat_suit_tests WHERE suit_id=s.id ORDER BY id DESC LIMIT 1
        ) BETWEEN date('now') AND date('now','+30 days')
    """)
    never_tested = await scalar("""
        SELECT COUNT(*) FROM hazmat_suits s
        WHERE s.status = 'serviceable'
        AND NOT EXISTS (SELECT 1 FROM hazmat_suit_tests WHERE suit_id=s.id)
    """)
    expiring_90 = await scalar("""
        SELECT COUNT(*) FROM hazmat_suits
        WHERE status = 'serviceable'
        AND expiry_date IS NOT NULL
        AND expiry_date BETWEEN date('now') AND date('now','+90 days')
    """)

    async with db.execute("""
        SELECT s.id, s.suit_type, s.model, s.size, s.serial_num, s.assigned_to,
               t.next_due,
               CAST(julianday('now') - julianday(t.next_due) AS INTEGER) as days_overdue
        FROM hazmat_suits s
        LEFT JOIN (
            SELECT suit_id, MAX(id) as mid FROM hazmat_suit_tests GROUP BY suit_id
        ) latest ON latest.suit_id = s.id
        LEFT JOIN hazmat_suit_tests t ON t.id = latest.mid
        WHERE s.status = 'serviceable'
        AND (t.next_due IS NULL OR t.next_due < date('now'))
        ORDER BY t.next_due ASC NULLS FIRST
        LIMIT 10
    """) as cur:
        overdue_list = [dict(r) for r in await cur.fetchall()]

    async with db.execute("""
        SELECT size, quantity FROM hazmat_paper_stock ORDER BY
            CASE size WHEN 'XS' THEN 1 WHEN 'S' THEN 2 WHEN 'M' THEN 3
                      WHEN 'L' THEN 4 WHEN 'XL' THEN 5 WHEN 'XXL' THEN 6 ELSE 7 END
    """) as cur:
        paper = [dict(r) for r in await cur.fetchall()]

    return {
        "total": total, "serviceable": serviceable,
        "assigned": assigned, "unassigned": serviceable - assigned,
        "expended_ytd": expended_ytd,
        "overdue_test": overdue_test, "due_30": due_30,
        "never_tested": never_tested, "expiring_90": expiring_90,
        "overdue_list": overdue_list, "paper": paper,
    }


# ── Suits ────────────────────────────────────────────────────────────────────

@router.get("/suits")
async def list_suits(status: str = None, suit_type: str = None, db=Depends(get_db)):
    q = """
        SELECT s.*,
            t.tested_date as last_tested, t.result as last_result,
            t.next_due as next_due, t.tested_by as last_tested_by
        FROM hazmat_suits s
        LEFT JOIN (
            SELECT suit_id, MAX(id) as mid FROM hazmat_suit_tests GROUP BY suit_id
        ) latest ON latest.suit_id = s.id
        LEFT JOIN hazmat_suit_tests t ON t.id = latest.mid
        WHERE 1=1
    """
    params = []
    if status:
        q += " AND s.status=?"
        params.append(status)
    if suit_type:
        q += " AND s.suit_type=?"
        params.append(suit_type)
    q += " ORDER BY s.suit_type, s.size, s.serial_num"
    async with db.execute(q, params) as cur:
        return [dict(r) for r in await cur.fetchall()]


@router.post("/suits", status_code=201)
async def create_suit(data: SuitCreate, request: Request, db=Depends(get_db)):
    require_tech(request)
    # Auto-set expiry if manufacture_date + shelf_life known
    expiry = data.expiry_date
    if not expiry and data.manufacture_date and data.shelf_life_years:
        from datetime import date
        from dateutil.relativedelta import relativedelta
        try:
            mfg = date.fromisoformat(data.manufacture_date)
            expiry = (mfg + relativedelta(years=int(data.shelf_life_years))).isoformat()
        except Exception:
            pass
    shelf = data.shelf_life_years if data.shelf_life_years else SUIT_SHELF_LIFE.get(data.suit_type)
    async with db.execute("""
        INSERT INTO hazmat_suits
            (suit_type, model, size, serial_num, manufacture_date,
             shelf_life_years, expiry_date, status, notes)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (data.suit_type, data.model, data.size, data.serial_num,
          data.manufacture_date, shelf, expiry, data.status, data.notes)) as cur:
        suit_id = cur.lastrowid
    await db.commit()
    return {"id": suit_id}


@router.put("/suits/{suit_id}")
async def update_suit(suit_id: int, data: SuitUpdate, request: Request, db=Depends(get_db)):
    require_tech(request)
    expiry = data.expiry_date
    if not expiry and data.manufacture_date and data.shelf_life_years:
        from datetime import date
        from dateutil.relativedelta import relativedelta
        try:
            mfg = date.fromisoformat(data.manufacture_date)
            expiry = (mfg + relativedelta(years=int(data.shelf_life_years))).isoformat()
        except Exception:
            pass
    await db.execute("""
        UPDATE hazmat_suits SET
            suit_type=?, model=?, size=?, serial_num=?, manufacture_date=?,
            shelf_life_years=?, expiry_date=?, status=?, assigned_to=?,
            assigned_date=?, notes=?, updated_at=datetime('now')
        WHERE id=?
    """, (data.suit_type, data.model, data.size, data.serial_num,
          data.manufacture_date, data.shelf_life_years, expiry, data.status,
          data.assigned_to or None, data.assigned_date or None, data.notes, suit_id))
    await db.commit()
    return {"ok": True}


@router.delete("/suits/{suit_id}")
async def delete_suit(suit_id: int, request: Request, db=Depends(get_db)):
    require_superadmin(request)
    await db.execute("DELETE FROM hazmat_suits WHERE id=?", (suit_id,))
    await db.commit()
    return {"ok": True}


# ── Assign / Return ──────────────────────────────────────────────────────────

@router.post("/suits/{suit_id}/assign")
async def assign_suit(suit_id: int, data: AssignBody, request: Request, db=Depends(get_db)):
    require_tech(request)
    from datetime import date
    today = date.today().isoformat()

    async with db.execute("SELECT * FROM hazmat_suits WHERE id=?", (suit_id,)) as cur:
        suit = await cur.fetchone()
    if not suit:
        raise HTTPException(404, "Suit not found")

    if data.assigned_to:
        # Close any open assignment
        await db.execute("""
            UPDATE hazmat_suit_assignments SET returned_date=?
            WHERE suit_id=? AND returned_date IS NULL
        """, (today, suit_id))
        # Create new assignment record
        await db.execute("""
            INSERT INTO hazmat_suit_assignments (suit_id, assigned_to, issued_date, notes)
            VALUES (?,?,?,?)
        """, (suit_id, data.assigned_to, data.issued_date or today, data.notes))
        await db.execute("""
            UPDATE hazmat_suits SET assigned_to=?, assigned_date=?, updated_at=datetime('now')
            WHERE id=?
        """, (data.assigned_to, data.issued_date or today, suit_id))
    else:
        # Return
        await db.execute("""
            UPDATE hazmat_suit_assignments SET returned_date=?, notes=COALESCE(notes||' '||?,'')
            WHERE suit_id=? AND returned_date IS NULL
        """, (data.returned_date or today, data.notes or '', suit_id))
        await db.execute("""
            UPDATE hazmat_suits SET assigned_to=NULL, assigned_date=NULL, updated_at=datetime('now')
            WHERE id=?
        """, (suit_id,))
    await db.commit()
    return {"ok": True}


@router.get("/suits/{suit_id}/assignments")
async def get_assignments(suit_id: int, db=Depends(get_db)):
    async with db.execute("""
        SELECT * FROM hazmat_suit_assignments WHERE suit_id=? ORDER BY issued_date DESC
    """, (suit_id,)) as cur:
        return [dict(r) for r in await cur.fetchall()]


# ── Pressure Tests ───────────────────────────────────────────────────────────

@router.post("/suits/{suit_id}/tests", status_code=201)
async def add_test(suit_id: int, data: TestRecord, request: Request, db=Depends(get_db)):
    require_tech(request)
    async with db.execute("SELECT id FROM hazmat_suits WHERE id=?", (suit_id,)) as cur:
        if not await cur.fetchone():
            raise HTTPException(404, "Suit not found")
    # Auto-calculate next_due (+1 year) if not provided
    next_due = data.next_due
    if not next_due and data.tested_date:
        from datetime import date
        from dateutil.relativedelta import relativedelta
        try:
            next_due = (date.fromisoformat(data.tested_date) + relativedelta(years=1)).isoformat()
        except Exception:
            pass
    await db.execute("""
        INSERT INTO hazmat_suit_tests (suit_id, tested_date, tested_by, result, next_due, notes)
        VALUES (?,?,?,?,?,?)
    """, (suit_id, data.tested_date, data.tested_by, data.result, next_due, data.notes))
    await db.commit()
    return {"ok": True}


@router.get("/suits/{suit_id}/tests")
async def get_tests(suit_id: int, db=Depends(get_db)):
    async with db.execute("""
        SELECT * FROM hazmat_suit_tests WHERE suit_id=? ORDER BY tested_date DESC
    """, (suit_id,)) as cur:
        return [dict(r) for r in await cur.fetchall()]


# ── Paper stock ──────────────────────────────────────────────────────────────

@router.get("/paper")
async def get_paper(db=Depends(get_db)):
    async with db.execute("""
        SELECT size, quantity FROM hazmat_paper_stock ORDER BY
            CASE size WHEN 'XS' THEN 1 WHEN 'S' THEN 2 WHEN 'M' THEN 3
                      WHEN 'L' THEN 4 WHEN 'XL' THEN 5 WHEN 'XXL' THEN 6 ELSE 7 END
    """) as cur:
        return [dict(r) for r in await cur.fetchall()]


@router.patch("/paper/{size}")
async def adjust_paper(size: str, data: PaperAdjust, request: Request, db=Depends(get_db)):
    require_tech(request)
    await db.execute("""
        INSERT INTO hazmat_paper_stock (size, quantity) VALUES (?,?)
        ON CONFLICT(size) DO UPDATE SET quantity=MAX(0,?), updated_at=datetime('now')
    """, (size, max(0, data.quantity), max(0, data.quantity)))
    await db.commit()
    return {"ok": True}


# ── Roster ───────────────────────────────────────────────────────────────────

@router.get("/roster")
async def get_roster(db=Depends(get_db)):
    async with db.execute("""
        SELECT s.id, s.suit_type, s.model, s.size, s.serial_num,
               s.assigned_to, s.assigned_date, s.status, s.expiry_date,
               t.tested_date as last_tested, t.next_due, t.result as last_result
        FROM hazmat_suits s
        LEFT JOIN (
            SELECT suit_id, MAX(id) as mid FROM hazmat_suit_tests GROUP BY suit_id
        ) latest ON latest.suit_id = s.id
        LEFT JOIN hazmat_suit_tests t ON t.id = latest.mid
        WHERE s.status NOT IN ('expended','condemned')
        ORDER BY s.assigned_to NULLS LAST, s.suit_type, s.size
    """) as cur:
        return [dict(r) for r in await cur.fetchall()]
