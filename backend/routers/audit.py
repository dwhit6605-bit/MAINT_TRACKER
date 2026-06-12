import json
from fastapi import APIRouter, Depends
from backend.database import get_db

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("/equipment/{equipment_id}")
async def get_equipment_audit(equipment_id: int, db=Depends(get_db)):
    async with db.execute("""
        SELECT * FROM audit_log
        WHERE equipment_id = ?
        ORDER BY created_at DESC
        LIMIT 200
    """, (equipment_id,)) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.get("")
async def get_all_audit(limit: int = 100, db=Depends(get_db)):
    async with db.execute("""
        SELECT a.*, e.name as equipment_name
        FROM audit_log a
        LEFT JOIN equipment e ON e.id = a.equipment_id
        ORDER BY a.created_at DESC
        LIMIT ?
    """, (limit,)) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]
