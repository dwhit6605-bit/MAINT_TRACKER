from fastapi import APIRouter, Depends, HTTPException, Request
from backend.database import get_db
from backend.auth import require_admin
from backend.models import SkoCreate, SkoComponentCreate, SkoCheckout, SkoCheckin

router = APIRouter(prefix="/api/skos", tags=["skos"])


@router.get("")
async def list_skos(db=Depends(get_db)):
    async with db.execute("SELECT * FROM skos ORDER BY name") as cur:
        rows = await cur.fetchall()
    result = []
    for r in rows:
        sko = dict(r)
        async with db.execute(
            "SELECT * FROM sko_components WHERE sko_id=? ORDER BY item_name", (sko["id"],)
        ) as cur2:
            sko["components"] = [dict(c) for c in await cur2.fetchall()]
        async with db.execute(
            "SELECT * FROM sko_checkouts WHERE sko_id=? AND returned_at IS NULL LIMIT 1",
            (sko["id"],)
        ) as cur3:
            sko["active_checkout"] = dict(co) if (co := await cur3.fetchone()) else None
        result.append(sko)
    return result


@router.get("/{sko_id}")
async def get_sko(sko_id: int, db=Depends(get_db)):
    async with db.execute("SELECT * FROM skos WHERE id=?", (sko_id,)) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "SKO not found")
    sko = dict(row)
    async with db.execute(
        "SELECT * FROM sko_components WHERE sko_id=? ORDER BY item_name", (sko_id,)
    ) as cur:
        sko["components"] = [dict(c) for c in await cur.fetchall()]
    async with db.execute(
        "SELECT * FROM sko_checkouts WHERE sko_id=? ORDER BY checkout_date DESC LIMIT 20",
        (sko_id,)
    ) as cur:
        sko["checkout_history"] = [dict(c) for c in await cur.fetchall()]
    return sko


@router.post("", status_code=201)
async def create_sko(request: Request, data: SkoCreate, db=Depends(get_db)):
    require_admin(request)
    async with db.execute(
        "INSERT INTO skos (name, nsn, description, status, notes) VALUES (?,?,?,?,?)",
        (data.name, data.nsn, data.description, data.status, data.notes)
    ) as cur:
        sko_id = cur.lastrowid
    for comp in (data.components or []):
        await db.execute(
            "INSERT INTO sko_components (sko_id, item_name, nsn, quantity_required, quantity_on_hand, notes) VALUES (?,?,?,?,?,?)",
            (sko_id, comp.item_name, comp.nsn, comp.quantity_required, comp.quantity_on_hand, comp.notes)
        )
    await db.commit()
    return {"id": sko_id}


@router.put("/{sko_id}")
async def update_sko(sko_id: int, request: Request, data: SkoCreate, db=Depends(get_db)):
    require_admin(request)
    await db.execute(
        "UPDATE skos SET name=?, nsn=?, description=?, status=?, notes=?, updated_at=datetime('now') WHERE id=?",
        (data.name, data.nsn, data.description, data.status, data.notes, sko_id)
    )
    # replace components
    await db.execute("DELETE FROM sko_components WHERE sko_id=?", (sko_id,))
    for comp in (data.components or []):
        await db.execute(
            "INSERT INTO sko_components (sko_id, item_name, nsn, quantity_required, quantity_on_hand, notes) VALUES (?,?,?,?,?,?)",
            (sko_id, comp.item_name, comp.nsn, comp.quantity_required, comp.quantity_on_hand, comp.notes)
        )
    await db.commit()
    return {"ok": True}


@router.delete("/{sko_id}", status_code=204)
async def delete_sko(sko_id: int, request: Request, db=Depends(get_db)):
    require_admin(request)
    await db.execute("DELETE FROM skos WHERE id=?", (sko_id,))
    await db.commit()


# ── Components ────────────────────────────────────────────────────────────────

@router.put("/{sko_id}/components/{comp_id}")
async def update_component(sko_id: int, comp_id: int, request: Request,
                           data: SkoComponentCreate, db=Depends(get_db)):
    require_admin(request)
    await db.execute(
        "UPDATE sko_components SET item_name=?, nsn=?, quantity_required=?, quantity_on_hand=?, notes=? WHERE id=? AND sko_id=?",
        (data.item_name, data.nsn, data.quantity_required, data.quantity_on_hand, data.notes, comp_id, sko_id)
    )
    await db.commit()
    # recalculate SKO status
    await _refresh_status(sko_id, db)
    return {"ok": True}


async def _refresh_status(sko_id: int, db):
    async with db.execute(
        "SELECT quantity_required, quantity_on_hand FROM sko_components WHERE sko_id=?", (sko_id,)
    ) as cur:
        comps = await cur.fetchall()
    if not comps:
        return
    short = any(c["quantity_on_hand"] < c["quantity_required"] for c in comps)
    none_  = all(c["quantity_on_hand"] == 0 for c in comps)
    status = "nmc" if none_ else ("incomplete" if short else "complete")
    await db.execute("UPDATE skos SET status=?, updated_at=datetime('now') WHERE id=?", (status, sko_id))
    await db.commit()


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


@router.get("/{sko_id}/checkouts")
async def list_checkouts(sko_id: int, db=Depends(get_db)):
    async with db.execute(
        "SELECT * FROM sko_checkouts WHERE sko_id=? ORDER BY checkout_date DESC",
        (sko_id,)
    ) as cur:
        return [dict(r) for r in await cur.fetchall()]
