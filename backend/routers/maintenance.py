import os
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from backend.database import get_db
from backend.models import MaintenanceTaskCreate, MaintenanceComplete
from backend.da2404 import generate_da2404
from backend import audit

router = APIRouter(prefix="/api/maintenance", tags=["maintenance"])


@router.get("")
async def list_tasks(equipment_id: int = None, status: str = None, db=Depends(get_db)):
    query = """
        SELECT m.*, e.name as equipment_name, e.location
        FROM maintenance_tasks m
        JOIN equipment e ON e.id = m.equipment_id
        WHERE 1=1
    """
    params = []
    if equipment_id:
        query += " AND m.equipment_id = ?"
        params.append(equipment_id)
    if status:
        query += " AND m.status = ?"
        params.append(status)
    query += " ORDER BY m.next_due ASC NULLS LAST, m.created_at DESC"
    async with db.execute(query, params) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.post("", status_code=201)
async def create_task(data: MaintenanceTaskCreate, db=Depends(get_db)):
    async with db.execute("""
        INSERT INTO maintenance_tasks
            (equipment_id, title, description, task_type, interval_days, last_done, next_due, status, assigned_to, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (data.equipment_id, data.title, data.description, data.task_type,
          data.interval_days, data.last_done, data.next_due, data.status,
          data.assigned_to, data.notes)) as cur:
        task_id = cur.lastrowid
    await audit.log(db, "maintenance", task_id, "created",
                    equipment_id=data.equipment_id,
                    detail={"title": data.title, "next_due": data.next_due})
    await db.commit()
    return {"id": task_id}


@router.post("/{task_id}/complete")
async def complete_task(task_id: int, data: MaintenanceComplete, db=Depends(get_db)):
    async with db.execute("SELECT * FROM maintenance_tasks WHERE id=?", (task_id,)) as cur:
        task = await cur.fetchone()
    if not task:
        raise HTTPException(404, "Task not found")

    now = datetime.utcnow().date().isoformat()
    next_due = data.next_due
    if not next_due and task["interval_days"]:
        next_due = (datetime.utcnow().date() + timedelta(days=task["interval_days"])).isoformat()

    await db.execute("""
        UPDATE maintenance_tasks
        SET status='completed', completed_at=datetime('now'), completed_by=?,
            last_done=?, next_due=?, notes=COALESCE(?, notes), updated_at=datetime('now')
        WHERE id=?
    """, (data.completed_by, now, next_due, data.notes, task_id))

    # if recurring, create the next task
    if task["interval_days"] and next_due:
        await db.execute("""
            INSERT INTO maintenance_tasks
                (equipment_id, title, description, task_type, interval_days, next_due, status, assigned_to)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
        """, (task["equipment_id"], task["title"], task["description"],
              task["task_type"], task["interval_days"], next_due, task["assigned_to"]))

    await audit.log(db, "maintenance", task_id, "completed",
                    equipment_id=task["equipment_id"],
                    actor=data.completed_by,
                    detail={"next_due": next_due, "notes": data.notes})
    await db.commit()
    return {"ok": True, "next_due": next_due}


@router.put("/{task_id}")
async def update_task(task_id: int, data: MaintenanceTaskCreate, db=Depends(get_db)):
    await db.execute("""
        UPDATE maintenance_tasks
        SET title=?, description=?, task_type=?, interval_days=?, last_done=?,
            next_due=?, status=?, assigned_to=?, notes=?, updated_at=datetime('now')
        WHERE id=?
    """, (data.title, data.description, data.task_type, data.interval_days,
          data.last_done, data.next_due, data.status, data.assigned_to, data.notes, task_id))
    await audit.log(db, "maintenance", task_id, "updated",
                    equipment_id=data.equipment_id,
                    detail={"title": data.title, "status": data.status, "next_due": data.next_due})
    await db.commit()
    return {"ok": True}


@router.delete("/{task_id}")
async def delete_task(task_id: int, db=Depends(get_db)):
    await db.execute("DELETE FROM maintenance_tasks WHERE id=?", (task_id,))
    await db.commit()
    return {"ok": True}


@router.get("/{task_id}/da2404")
async def export_da2404(
    task_id: int,
    organization: str = "",
    tm_number: str = "",
    tm_date: str = "",
    manhours: str = "",
    supervisor: str = "",
    db=Depends(get_db),
):
    async with db.execute("""
        SELECT m.*, e.name as equipment_name, e.serial_num, e.model
        FROM maintenance_tasks m
        JOIN equipment e ON e.id = m.equipment_id
        WHERE m.id = ?
    """, (task_id,)) as cur:
        task = await cur.fetchone()
    if not task:
        raise HTTPException(404, "Task not found")

    task = dict(task)
    nomenclature = task["equipment_name"]
    if task.get("model"):
        nomenclature += f" / {task['model']}"

    line_items = [{
        "item_no": "1",
        "status": "/",
        "deficiency": task.get("description") or task.get("title", ""),
        "corrective_action": task.get("notes") or "Completed per TM.",
        "initial": (task.get("completed_by") or "")[:3].upper(),
    }]

    inspection_date = task.get("completed_at", "")[:10] if task.get("completed_at") else datetime.utcnow().date().isoformat()

    pdf_bytes = generate_da2404(
        organization=organization,
        nomenclature=nomenclature,
        serial_nsn=task.get("serial_num") or "",
        inspection_date=inspection_date,
        inspection_type="Scheduled",
        tm_number=tm_number,
        tm_date=tm_date,
        line_items=line_items,
        inspector_name=task.get("completed_by") or "",
        inspector_time="",
        supervisor_name=supervisor,
        supervisor_time="",
        manhours=manhours,
    )

    # Save as a permanent equipment attachment
    eq_id = task["equipment_id"]
    upload_dir = os.path.join("uploads", "equipment", str(eq_id))
    os.makedirs(upload_dir, exist_ok=True)
    stored_name = f"DA2404_task{task_id}_{inspection_date}.pdf"
    file_path = os.path.join(upload_dir, stored_name)
    with open(file_path, "wb") as f:
        f.write(pdf_bytes)
    original_name = f"DA2404_{task['title'].replace(' ', '_')}_{inspection_date}.pdf"
    # Upsert: replace any prior DA2404 for this task so re-exports don't accumulate duplicates
    async with db.execute(
        "SELECT id FROM equipment_attachments WHERE equipment_id=? AND filename=?",
        (eq_id, stored_name)
    ) as cur:
        existing = await cur.fetchone()
    if not existing:
        await db.execute("""
            INSERT INTO equipment_attachments (equipment_id, filename, original_name, file_type, file_size)
            VALUES (?, ?, ?, 'application/pdf', ?)
        """, (eq_id, stored_name, original_name, len(pdf_bytes)))
        await db.commit()

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{original_name}"'},
    )


@router.post("/refresh-overdue")
async def refresh_overdue(db=Depends(get_db)):
    """Mark pending tasks past their due date as overdue."""
    today = datetime.utcnow().date().isoformat()
    await db.execute("""
        UPDATE maintenance_tasks SET status='overdue', updated_at=datetime('now')
        WHERE status='pending' AND next_due IS NOT NULL AND next_due < ?
    """, (today,))
    await db.commit()
    return {"ok": True}
