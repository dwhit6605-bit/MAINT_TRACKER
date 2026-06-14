import os, shutil
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from backend.database import get_db
from backend.models import CalibrationCreate, CalibrationBulkEdit
from backend.auth import require_admin, require_tech
from backend import audit

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads")
router = APIRouter(prefix="/api/calibration", tags=["calibration"])


@router.get("")
async def list_records(equipment_id: int = None, db=Depends(get_db)):
    query = """
        SELECT c.*, e.name as equipment_name, e.serial_num
        FROM calibration_records c
        JOIN equipment e ON e.id = c.equipment_id
        WHERE 1=1
    """
    params = []
    if equipment_id:
        query += " AND c.equipment_id = ?"
        params.append(equipment_id)
    query += " ORDER BY c.calibrated_at DESC"
    async with db.execute(query, params) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.get("/due")
async def due_soon(days: int = 30, db=Depends(get_db)):
    async with db.execute("""
        SELECT c.*, e.name as equipment_name, e.location, e.serial_num
        FROM calibration_records c
        JOIN equipment e ON e.id = c.equipment_id
        WHERE c.id IN (
            SELECT MAX(id) FROM calibration_records GROUP BY equipment_id
        )
        AND c.next_due IS NOT NULL
        AND DATE(c.next_due) <= DATE('now', '+' || ? || ' days')
        ORDER BY c.next_due ASC
    """, (days,)) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.post("", status_code=201)
async def create_record(data: CalibrationCreate, request: Request, db=Depends(get_db)):
    require_tech(request)
    async with db.execute("""
        INSERT INTO calibration_records
            (equipment_id, calibrated_by, calibrated_at, next_due, certificate_num, result, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (data.equipment_id, data.calibrated_by, data.calibrated_at,
          data.next_due, data.certificate_num, data.result, data.notes)) as cur:
        rec_id = cur.lastrowid
    await audit.log(db, "calibration", rec_id, "calibrated",
                    equipment_id=data.equipment_id,
                    actor=data.calibrated_by,
                    detail={"result": data.result, "next_due": data.next_due,
                            "calibrated_at": data.calibrated_at})
    await db.commit()
    return {"id": rec_id}


@router.patch("/bulk")
async def bulk_edit(data: CalibrationBulkEdit, request: Request, db=Depends(get_db)):
    require_tech(request)
    if not data.ids:
        raise HTTPException(400, "No IDs provided")
    sets, params = [], []
    if data.calibrated_at is not None:
        sets.append("calibrated_at=?"); params.append(data.calibrated_at)
    if data.next_due is not None:
        sets.append("next_due=?"); params.append(data.next_due)
    if data.calibrated_by is not None:
        sets.append("calibrated_by=?"); params.append(data.calibrated_by)
    if data.result is not None:
        sets.append("result=?"); params.append(data.result)
    if data.notes is not None:
        sets.append("notes=?"); params.append(data.notes)
    if not sets:
        raise HTTPException(400, "No fields to update")
    placeholders = ",".join("?" * len(data.ids))
    params += data.ids
    await db.execute(
        f"UPDATE calibration_records SET {', '.join(sets)} WHERE id IN ({placeholders})",
        params
    )
    await db.commit()
    return {"updated": len(data.ids)}


@router.post("/{rec_id}/upload-cert")
async def upload_cert(rec_id: int, file: UploadFile = File(...), db=Depends(get_db)):
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    ext = os.path.splitext(file.filename)[1]
    dest = os.path.join(UPLOAD_DIR, f"cert_{rec_id}{ext}")
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    await db.execute("UPDATE calibration_records SET cert_file=? WHERE id=?", (dest, rec_id))
    await db.commit()
    return {"path": dest}


@router.delete("/{rec_id}")
async def delete_record(rec_id: int, request: Request, db=Depends(get_db)):
    require_admin(request)
    async with db.execute(
        "SELECT equipment_id FROM calibration_records WHERE id=?", (rec_id,)
    ) as cur:
        row = await cur.fetchone()
    if row:
        await audit.log(db, "calibration", rec_id, "deleted",
                        equipment_id=row["equipment_id"])
    await db.execute("DELETE FROM calibration_records WHERE id=?", (rec_id,))
    await db.commit()
    return {"ok": True}
