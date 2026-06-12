from fastapi import APIRouter, Depends, HTTPException
from backend.database import get_db
from backend.models import EquipmentCreate, EquipmentUpdate
from backend import audit

router = APIRouter(prefix="/api/equipment", tags=["equipment"])


@router.get("")
async def list_equipment(db=Depends(get_db)):
    async with db.execute("""
        SELECT e.*,
            (SELECT COUNT(*) FROM maintenance_tasks WHERE equipment_id = e.id AND status = 'pending') as pending_tasks,
            (SELECT COUNT(*) FROM maintenance_tasks WHERE equipment_id = e.id AND status = 'overdue') as overdue_tasks,
            (SELECT next_due FROM calibration_records WHERE equipment_id = e.id ORDER BY calibrated_at DESC LIMIT 1) as cal_next_due
        FROM equipment e ORDER BY e.name
    """) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.get("/assigned-units")
async def list_assigned_units(db=Depends(get_db)):
    async with db.execute("""
        SELECT DISTINCT assigned_to FROM equipment
        WHERE assigned_to IS NOT NULL AND assigned_to != ''
        ORDER BY assigned_to
    """) as cur:
        rows = await cur.fetchall()
    # seed defaults if DB has none yet
    db_units = [r["assigned_to"] for r in rows]
    defaults = ["ALPHA", "BRAVO", "CAGE"]
    merged = defaults[:]
    for u in db_units:
        if u not in merged:
            merged.append(u)
    return merged


@router.get("/locations")
async def list_locations(db=Depends(get_db)):
    async with db.execute("""
        SELECT DISTINCT location FROM equipment
        WHERE location IS NOT NULL AND location != ''
        ORDER BY location
    """) as cur:
        rows = await cur.fetchall()
    return [r["location"] for r in rows]


@router.get("/{eq_id}")
async def get_equipment(eq_id: int, db=Depends(get_db)):
    async with db.execute("SELECT * FROM equipment WHERE id = ?", (eq_id,)) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Equipment not found")
    return dict(row)


@router.post("", status_code=201)
async def create_equipment(data: EquipmentCreate, db=Depends(get_db)):
    async with db.execute("""
        INSERT INTO equipment (name, category, serial_num, model, manufacturer, location, assigned_to, status, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (data.name, data.category, data.serial_num, data.model,
          data.manufacturer, data.location, data.assigned_to, data.status, data.notes)) as cur:
        eq_id = cur.lastrowid
    await audit.log(db, "equipment", eq_id, "created", equipment_id=eq_id,
                    detail={"name": data.name, "category": data.category})
    await db.commit()
    return {"id": eq_id}


@router.put("/{eq_id}")
async def update_equipment(eq_id: int, data: EquipmentUpdate, db=Depends(get_db)):
    await db.execute("""
        UPDATE equipment SET name=?, category=?, serial_num=?, model=?, manufacturer=?,
            location=?, assigned_to=?, status=?, notes=?, updated_at=datetime('now')
        WHERE id=?
    """, (data.name, data.category, data.serial_num, data.model,
          data.manufacturer, data.location, data.assigned_to, data.status, data.notes, eq_id))
    await audit.log(db, "equipment", eq_id, "updated", equipment_id=eq_id,
                    detail={"name": data.name, "status": data.status})
    await db.commit()
    return {"ok": True}


@router.delete("/{eq_id}")
async def delete_equipment(eq_id: int, db=Depends(get_db)):
    async with db.execute("SELECT name FROM equipment WHERE id=?", (eq_id,)) as cur:
        row = await cur.fetchone()
    name = row["name"] if row else "unknown"
    await db.execute("DELETE FROM equipment WHERE id=?", (eq_id,))
    await audit.log(db, "equipment", eq_id, "deleted", equipment_id=eq_id,
                    detail={"name": name})
    await db.commit()
    return {"ok": True}
