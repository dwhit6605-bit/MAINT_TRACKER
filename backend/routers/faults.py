"""Fault reporting — operators submit, techs/admins manage."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
from backend.database import get_db
from backend.auth import require_admin, require_tech, require_superadmin

router = APIRouter(prefix="/api/faults", tags=["faults"])

VALID_SEVERITY = {"routine", "urgent", "critical"}
VALID_STATUS   = {"open", "in_progress", "resolved", "closed"}


class FaultCreate(BaseModel):
    equipment_id: int
    reported_by: str
    severity: str = "routine"
    title: str
    description: Optional[str] = None
    create_task: bool = False


class FaultUpdate(BaseModel):
    severity: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    resolved_by: Optional[str] = None
    resolution: Optional[str] = None


@router.get("")
async def list_faults(status: Optional[str] = None, equipment_id: Optional[int] = None, db=Depends(get_db)):
    query = """
        SELECT f.*, e.name as equipment_name, e.location,
               m.status as linked_task_status, m.title as linked_task_title, m.id as linked_task_id
        FROM fault_reports f
        JOIN equipment e ON e.id = f.equipment_id
        LEFT JOIN maintenance_tasks m ON m.id = f.linked_task_id
        WHERE 1=1
    """
    params = []
    if status:
        query += " AND f.status = ?"
        params.append(status)
    if equipment_id:
        query += " AND f.equipment_id = ?"
        params.append(equipment_id)
    query += " ORDER BY CASE f.severity WHEN 'critical' THEN 0 WHEN 'urgent' THEN 1 ELSE 2 END, f.created_at DESC"
    async with db.execute(query, params) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.post("", status_code=201)
async def create_fault(data: FaultCreate, db=Depends(get_db)):
    if data.severity not in VALID_SEVERITY:
        raise HTTPException(400, f"severity must be one of {VALID_SEVERITY}")
    async with db.execute("SELECT id FROM equipment WHERE id=?", (data.equipment_id,)) as cur:
        if not await cur.fetchone():
            raise HTTPException(404, "Equipment not found")
    async with db.execute("""
        INSERT INTO fault_reports (equipment_id, reported_by, severity, title, description)
        VALUES (?, ?, ?, ?, ?)
    """, (data.equipment_id, data.reported_by, data.severity, data.title, data.description)) as cur:
        fault_id = cur.lastrowid

    task_id = None
    if data.create_task:
        async with db.execute("""
            INSERT INTO maintenance_tasks
                (equipment_id, title, description, task_type, status, source_fault_id)
            VALUES (?, ?, ?, 'corrective', 'pending', ?)
        """, (data.equipment_id, f"Fault: {data.title}", data.description, fault_id)) as cur:
            task_id = cur.lastrowid
        await db.execute("UPDATE fault_reports SET linked_task_id=? WHERE id=?", (task_id, fault_id))

    await db.commit()
    return {"id": fault_id, "task_id": task_id}


@router.patch("/{fault_id}")
async def update_fault(fault_id: int, data: FaultUpdate, request: Request, db=Depends(get_db)):
    require_tech(request)
    async with db.execute("SELECT * FROM fault_reports WHERE id=?", (fault_id,)) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Fault not found")
    if data.status and data.status not in VALID_STATUS:
        raise HTTPException(400, f"status must be one of {VALID_STATUS}")
    if data.severity and data.severity not in VALID_SEVERITY:
        raise HTTPException(400, f"severity must be one of {VALID_SEVERITY}")

    resolved_at = None
    if data.status in ("resolved", "closed") and not row["resolved_at"]:
        from datetime import datetime
        resolved_at = datetime.utcnow().isoformat()

    sets, params = [], []
    if data.title is not None:      sets.append("title=?");       params.append(data.title)
    if data.description is not None: sets.append("description=?"); params.append(data.description)
    if data.severity is not None:   sets.append("severity=?");    params.append(data.severity)
    if data.status is not None:     sets.append("status=?");      params.append(data.status)
    if data.resolved_by is not None: sets.append("resolved_by=?"); params.append(data.resolved_by)
    if data.resolution is not None: sets.append("resolution=?");  params.append(data.resolution)
    if resolved_at:                 sets.append("resolved_at=?"); params.append(resolved_at)
    sets.append("updated_at=datetime('now')")
    params.append(fault_id)
    await db.execute(f"UPDATE fault_reports SET {', '.join(sets)} WHERE id=?", params)
    await db.commit()
    return {"ok": True}


@router.post("/{fault_id}/link-task", status_code=201)
async def link_task_to_fault(fault_id: int, request: Request, db=Depends(get_db)):
    require_tech(request)
    async with db.execute("SELECT * FROM fault_reports WHERE id=?", (fault_id,)) as cur:
        fault = await cur.fetchone()
    if not fault:
        raise HTTPException(404, "Fault not found")
    async with db.execute("""
        INSERT INTO maintenance_tasks
            (equipment_id, title, description, task_type, status, source_fault_id)
        VALUES (?, ?, ?, 'corrective', 'pending', ?)
    """, (fault["equipment_id"], f"Fault: {fault['title']}", fault["description"], fault_id)) as cur:
        task_id = cur.lastrowid
    await db.execute("UPDATE fault_reports SET linked_task_id=? WHERE id=?", (task_id, fault_id))
    await db.commit()
    return {"task_id": task_id}


@router.delete("/{fault_id}")
async def delete_fault(fault_id: int, request: Request, db=Depends(get_db)):
    require_superadmin(request)
    await db.execute("DELETE FROM fault_reports WHERE id=?", (fault_id,))
    await db.commit()
    return {"ok": True}
