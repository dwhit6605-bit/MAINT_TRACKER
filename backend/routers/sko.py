from fastapi import APIRouter, Depends, HTTPException, Request
from backend.database import get_db
from backend.auth import require_admin
from backend.models import SkoCreate, SkoCheckout, SkoCheckin, SkoPartsUsed

router = APIRouter(prefix="/api/skos", tags=["skos"])


async def _sko_equipment(sko_id: int, db):
    async with db.execute("""
        SELECT se.id as link_id, e.id, e.name, e.serial_num, e.model, e.category,
               e.status, e.location, e.assigned_to
        FROM sko_equipment se
        JOIN equipment e ON e.id = se.equipment_id
        WHERE se.sko_id=?
        ORDER BY e.name
    """, (sko_id,)) as cur:
        return [dict(r) for r in await cur.fetchall()]


async def _sko_with_pmcs(sko_id: int, db):
    """Return equipment list augmented with their PMCS templates."""
    equip = await _sko_equipment(sko_id, db)
    for e in equip:
        async with db.execute("""
            SELECT DISTINCT pt.id, pt.title
            FROM pmcs_templates pt
            JOIN pmcs_template_equipment pte ON pte.template_id = pt.id
            WHERE pte.equipment_id = ?
            ORDER BY pt.title
        """, (e["id"],)) as cur:
            e["pmcs_templates"] = [dict(r) for r in await cur.fetchall()]
        async with db.execute("""
            SELECT status, next_due FROM maintenance_tasks
            WHERE equipment_id=? AND status IN ('pending','overdue')
            ORDER BY next_due LIMIT 1
        """, (e["id"],)) as cur:
            t = await cur.fetchone()
            e["next_task"] = dict(t) if t else None
    return equip


@router.get("")
async def list_skos(db=Depends(get_db)):
    async with db.execute("SELECT * FROM skos ORDER BY name") as cur:
        rows = await cur.fetchall()
    result = []
    for r in rows:
        sko = dict(r)
        sko["equipment"] = await _sko_equipment(sko["id"], db)
        sko["equipment_count"] = len(sko["equipment"])
        async with db.execute(
            "SELECT * FROM sko_checkouts WHERE sko_id=? AND returned_at IS NULL LIMIT 1",
            (sko["id"],)
        ) as cur:
            co = await cur.fetchone()
            sko["active_checkout"] = dict(co) if co else None
        result.append(sko)
    return result


@router.get("/{sko_id}")
async def get_sko(sko_id: int, db=Depends(get_db)):
    async with db.execute("SELECT * FROM skos WHERE id=?", (sko_id,)) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "SKO not found")
    sko = dict(row)
    sko["equipment"] = await _sko_with_pmcs(sko_id, db)
    async with db.execute(
        "SELECT * FROM sko_checkouts WHERE sko_id=? ORDER BY checkout_date DESC LIMIT 20",
        (sko_id,)
    ) as cur:
        sko["checkout_history"] = [dict(c) for c in await cur.fetchall()]
    async with db.execute("""
        SELECT sp.*, ii.name as item_name, ii.unit
        FROM sko_parts_used sp
        JOIN inventory_items ii ON ii.id = sp.item_id
        WHERE sp.sko_id=?
        ORDER BY sp.created_at DESC LIMIT 50
    """, (sko_id,)) as cur:
        sko["parts_log"] = [dict(r) for r in await cur.fetchall()]
    return sko


@router.post("", status_code=201)
async def create_sko(request: Request, data: SkoCreate, db=Depends(get_db)):
    require_admin(request)
    async with db.execute(
        "INSERT INTO skos (name, nsn, description, notes) VALUES (?,?,?,?)",
        (data.name, data.nsn, data.description, data.notes)
    ) as cur:
        sko_id = cur.lastrowid
    for eq_id in (data.equipment_ids or []):
        await db.execute(
            "INSERT OR IGNORE INTO sko_equipment (sko_id, equipment_id) VALUES (?,?)",
            (sko_id, eq_id)
        )
    await db.commit()
    return {"id": sko_id}


@router.put("/{sko_id}")
async def update_sko(sko_id: int, request: Request, data: SkoCreate, db=Depends(get_db)):
    require_admin(request)
    await db.execute(
        "UPDATE skos SET name=?, nsn=?, description=?, notes=?, updated_at=datetime('now') WHERE id=?",
        (data.name, data.nsn, data.description, data.notes, sko_id)
    )
    if data.equipment_ids is not None:
        await db.execute("DELETE FROM sko_equipment WHERE sko_id=?", (sko_id,))
        for eq_id in data.equipment_ids:
            await db.execute(
                "INSERT OR IGNORE INTO sko_equipment (sko_id, equipment_id) VALUES (?,?)",
                (sko_id, eq_id)
            )
    await db.commit()
    return {"ok": True}


@router.delete("/{sko_id}", status_code=204)
async def delete_sko(sko_id: int, request: Request, db=Depends(get_db)):
    require_admin(request)
    await db.execute("DELETE FROM skos WHERE id=?", (sko_id,))
    await db.commit()


# ── Equipment membership ──────────────────────────────────────────────────────

@router.post("/{sko_id}/equipment/{equipment_id}", status_code=201)
async def add_equipment(sko_id: int, equipment_id: int, request: Request, db=Depends(get_db)):
    require_admin(request)
    await db.execute(
        "INSERT OR IGNORE INTO sko_equipment (sko_id, equipment_id) VALUES (?,?)",
        (sko_id, equipment_id)
    )
    await db.commit()
    return {"ok": True}


@router.delete("/{sko_id}/equipment/{equipment_id}", status_code=204)
async def remove_equipment(sko_id: int, equipment_id: int, request: Request, db=Depends(get_db)):
    require_admin(request)
    await db.execute(
        "DELETE FROM sko_equipment WHERE sko_id=? AND equipment_id=?",
        (sko_id, equipment_id)
    )
    await db.commit()


# ── Parts / Inventory usage ───────────────────────────────────────────────────

@router.post("/{sko_id}/parts", status_code=201)
async def log_parts(sko_id: int, data: SkoPartsUsed, db=Depends(get_db)):
    async with db.execute("SELECT id FROM skos WHERE id=?", (sko_id,)) as cur:
        if not await cur.fetchone():
            raise HTTPException(404, "SKO not found")
    async with db.execute("SELECT quantity FROM inventory_items WHERE id=?", (data.item_id,)) as cur:
        item = await cur.fetchone()
    if not item:
        raise HTTPException(404, "Inventory item not found")
    await db.execute(
        "INSERT INTO sko_parts_used (sko_id, item_id, quantity, used_by, notes) VALUES (?,?,?,?,?)",
        (sko_id, data.item_id, data.quantity, data.used_by, data.notes)
    )
    await db.execute(
        "UPDATE inventory_items SET quantity = MAX(0, quantity - ?), updated_at=datetime('now') WHERE id=?",
        (data.quantity, data.item_id)
    )
    await db.execute(
        "INSERT INTO inventory_transactions (item_id, action, quantity, reference, performed_by) VALUES (?,?,?,?,?)",
        (data.item_id, "remove", data.quantity, f"SKO #{sko_id}", data.used_by)
    )
    await db.commit()
    return {"ok": True}


# ── Checkout / Check-in ───────────────────────────────────────────────────────

@router.post("/{sko_id}/checkout", status_code=201)
async def checkout_sko(sko_id: int, data: SkoCheckout, db=Depends(get_db)):
    async with db.execute("SELECT id FROM skos WHERE id=?", (sko_id,)) as cur:
        if not await cur.fetchone():
            raise HTTPException(404, "SKO not found")
    async with db.execute(
        "SELECT id FROM sko_checkouts WHERE sko_id=? AND returned_at IS NULL", (sko_id,)
    ) as cur:
        if await cur.fetchone():
            raise HTTPException(400, "SKO is already checked out")
    async with db.execute(
        "INSERT INTO sko_checkouts (sko_id, checked_out_by, expected_return, notes) VALUES (?,?,?,?)",
        (sko_id, data.checked_out_by, data.expected_return, data.notes)
    ) as cur:
        co_id = cur.lastrowid
    await db.commit()
    return {"id": co_id}


@router.post("/{sko_id}/checkin")
async def checkin_sko(sko_id: int, data: SkoCheckin, db=Depends(get_db)):
    async with db.execute(
        "SELECT id FROM sko_checkouts WHERE sko_id=? AND returned_at IS NULL", (sko_id,)
    ) as cur:
        co = await cur.fetchone()
    if not co:
        raise HTTPException(400, "SKO is not currently checked out")
    await db.execute(
        "UPDATE sko_checkouts SET returned_at=datetime('now'), notes=COALESCE(notes||' | '||?, notes, ?) WHERE id=?",
        (data.notes, data.notes, co["id"])
    )
    await db.commit()
    return {"ok": True}
