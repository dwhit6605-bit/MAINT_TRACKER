"""Equipment-type maintenance checklist templates.

One checklist per unique equipment name; steps shared across all gear
with that name. Steps auto-populate as PMCS items when equipment is
added to a PMCS template.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
from backend.database import get_db
from backend.auth import require_tech

router = APIRouter(prefix="/api/eq-checklists", tags=["eq-checklists"])


class StepCreate(BaseModel):
    step_no:   Optional[str] = None
    interval:  str = "B"
    title:     str
    procedure: Optional[str] = None

class StepUpdate(StepCreate):
    order_index: Optional[int] = None


async def _get_or_create(db, name: str) -> int:
    async with db.execute(
        "SELECT id FROM equipment_type_checklists WHERE equipment_name=?", (name,)
    ) as cur:
        row = await cur.fetchone()
    if row:
        return row[0]
    async with db.execute(
        "INSERT INTO equipment_type_checklists (equipment_name) VALUES (?)", (name,)
    ) as cur:
        return cur.lastrowid


@router.get("/by-name")
async def get_by_name(name: str, db=Depends(get_db)):
    async with db.execute(
        "SELECT * FROM equipment_type_checklists WHERE equipment_name=?", (name,)
    ) as cur:
        cl = await cur.fetchone()
    if not cl:
        return {"checklist": None, "steps": []}
    async with db.execute("""
        SELECT * FROM equipment_type_checklist_steps
        WHERE checklist_id=? ORDER BY order_index, id
    """, (cl["id"],)) as cur:
        steps = [dict(s) for s in await cur.fetchall()]
    return {"checklist": dict(cl), "steps": steps}


@router.get("/for-equipment/{equipment_id}")
async def get_for_equipment(equipment_id: int, db=Depends(get_db)):
    async with db.execute("SELECT name FROM equipment WHERE id=?", (equipment_id,)) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Equipment not found")
    return await get_by_name(row["name"], db)


@router.post("/steps", status_code=201)
async def add_step(name: str, data: StepCreate, request: Request, db=Depends(get_db)):
    require_tech(request)
    cl_id = await _get_or_create(db, name)
    async with db.execute(
        "SELECT COALESCE(MAX(order_index),0)+1 FROM equipment_type_checklist_steps WHERE checklist_id=?",
        (cl_id,)
    ) as cur:
        next_idx = (await cur.fetchone())[0]
    async with db.execute("""
        INSERT INTO equipment_type_checklist_steps
            (checklist_id, step_no, interval, title, procedure, order_index)
        VALUES (?,?,?,?,?,?)
    """, (cl_id, data.step_no, data.interval, data.title, data.procedure, next_idx)) as cur:
        step_id = cur.lastrowid
    await db.execute(
        "UPDATE equipment_type_checklists SET updated_at=datetime('now') WHERE id=?", (cl_id,)
    )
    await db.commit()
    return {"id": step_id, "ok": True}


@router.put("/steps/{step_id}")
async def update_step(step_id: int, data: StepUpdate, request: Request, db=Depends(get_db)):
    require_tech(request)
    await db.execute("""
        UPDATE equipment_type_checklist_steps
        SET step_no=?, interval=?, title=?, procedure=?,
            order_index=COALESCE(?,order_index)
        WHERE id=?
    """, (data.step_no, data.interval, data.title, data.procedure, data.order_index, step_id))
    await db.commit()
    return {"ok": True}


@router.delete("/steps/{step_id}")
async def delete_step(step_id: int, request: Request, db=Depends(get_db)):
    require_tech(request)
    await db.execute("DELETE FROM equipment_type_checklist_steps WHERE id=?", (step_id,))
    await db.commit()
    return {"ok": True}


@router.put("/steps/reorder")
async def reorder_steps(name: str, step_ids: list[int], request: Request, db=Depends(get_db)):
    require_tech(request)
    for idx, sid in enumerate(step_ids):
        await db.execute(
            "UPDATE equipment_type_checklist_steps SET order_index=? WHERE id=?", (idx, sid)
        )
    await db.commit()
    return {"ok": True}
