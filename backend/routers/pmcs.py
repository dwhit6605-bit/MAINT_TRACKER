import os
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from typing import Optional, List
from backend.database import get_db
from backend.pmcs_pdf import generate_pmcs_pdf

router = APIRouter(prefix="/api/pmcs", tags=["pmcs"])

ARCHIVE_DIR = os.path.join("uploads", "pmcs")


# ── Pydantic models ───────────────────────────────────────────────────────────

class TemplateCreate(BaseModel):
    title: str
    description: Optional[str] = None
    equipment_id: Optional[int] = None  # legacy single-equipment field (kept for compat)

class ItemCreate(BaseModel):
    item_no: Optional[str] = None
    interval: str = "B"
    check_item: str
    procedure: Optional[str] = None
    not_ready_if: Optional[str] = None
    order_index: int = 0
    equipment_id: Optional[int] = None  # which equipment this check belongs to
    creates_task: bool = False          # if true, a fault auto-creates a maintenance task

class ItemUpdate(ItemCreate):
    pass

class TemplateEquipmentAdd(BaseModel):
    equipment_id: int

class SessionStart(BaseModel):
    operator_name: Optional[str] = None
    operator_rank: Optional[str] = None

class ResultSubmit(BaseModel):
    item_id: int
    status: str = "ok"   # ok | fault | na
    notes: Optional[str] = None

class SessionComplete(BaseModel):
    operator_name: Optional[str] = None
    operator_rank: Optional[str] = None
    notes: Optional[str] = None
    results: List[ResultSubmit]


# ── Templates ─────────────────────────────────────────────────────────────────

@router.get("/templates")
async def list_templates(db=Depends(get_db)):
    async with db.execute("""
        SELECT t.*,
               (SELECT COUNT(*) FROM pmcs_items WHERE template_id=t.id) as item_count,
               (SELECT COUNT(*) FROM pmcs_sessions WHERE template_id=t.id AND status='completed') as session_count,
               e.name as equipment_name
        FROM pmcs_templates t
        LEFT JOIN equipment e ON e.id = t.equipment_id
        ORDER BY t.title
    """) as cur:
        rows = await cur.fetchall()
    result = []
    for row in rows:
        r = dict(row)
        # Fetch linked equipment list
        async with db.execute("""
            SELECT e.id, e.name, e.category, e.serial_num
            FROM pmcs_template_equipment te
            JOIN equipment e ON e.id = te.equipment_id
            WHERE te.template_id = ?
            ORDER BY te.order_index, e.name
        """, (r["id"],)) as cur2:
            r["linked_equipment"] = [dict(eq) for eq in await cur2.fetchall()]
        result.append(r)
    return result


@router.get("/templates/{tmpl_id}")
async def get_template(tmpl_id: int, db=Depends(get_db)):
    async with db.execute("""
        SELECT t.*, e.name as equipment_name
        FROM pmcs_templates t
        LEFT JOIN equipment e ON e.id = t.equipment_id
        WHERE t.id=?
    """, (tmpl_id,)) as cur:
        tmpl = await cur.fetchone()
    if not tmpl:
        raise HTTPException(404, "Template not found")
    tmpl = dict(tmpl)
    # Items with equipment details
    async with db.execute("""
        SELECT pi.*, e.name as equipment_name
        FROM pmcs_items pi
        LEFT JOIN equipment e ON e.id = pi.equipment_id
        WHERE pi.template_id=?
        ORDER BY pi.equipment_id NULLS LAST, pi.order_index, pi.id
    """, (tmpl_id,)) as cur:
        tmpl["items"] = [dict(i) for i in await cur.fetchall()]
    # Linked equipment
    async with db.execute("""
        SELECT e.id, e.name, e.category, e.serial_num
        FROM pmcs_template_equipment te
        JOIN equipment e ON e.id = te.equipment_id
        WHERE te.template_id = ?
        ORDER BY te.order_index, e.name
    """, (tmpl_id,)) as cur:
        tmpl["linked_equipment"] = [dict(eq) for eq in await cur.fetchall()]
    return tmpl


@router.post("/templates/{tmpl_id}/duplicate", status_code=201)
async def duplicate_template(tmpl_id: int, db=Depends(get_db)):
    """Clone a template with all its linked equipment and check items."""
    async with db.execute("SELECT * FROM pmcs_templates WHERE id=?", (tmpl_id,)) as cur:
        src = await cur.fetchone()
    if not src:
        raise HTTPException(404, "Template not found")
    src = dict(src)

    async with db.execute("""
        INSERT INTO pmcs_templates (title, description, equipment_id)
        VALUES (?, ?, ?)
    """, (f"Copy of {src['title']}", src["description"], src["equipment_id"])) as cur:
        new_id = cur.lastrowid

    # Clone linked equipment
    async with db.execute(
        "SELECT equipment_id, order_index FROM pmcs_template_equipment WHERE template_id=?",
        (tmpl_id,)
    ) as cur:
        eq_rows = await cur.fetchall()
    for eq in eq_rows:
        await db.execute(
            "INSERT OR IGNORE INTO pmcs_template_equipment (template_id, equipment_id, order_index) VALUES (?,?,?)",
            (new_id, eq["equipment_id"], eq["order_index"])
        )

    # Clone items
    async with db.execute(
        "SELECT * FROM pmcs_items WHERE template_id=? ORDER BY order_index, id",
        (tmpl_id,)
    ) as cur:
        items = await cur.fetchall()
    for it in items:
        await db.execute("""
            INSERT INTO pmcs_items
                (template_id, item_no, interval, check_item, procedure,
                 not_ready_if, order_index, equipment_id, creates_task)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (new_id, it["item_no"], it["interval"], it["check_item"],
              it["procedure"], it["not_ready_if"], it["order_index"],
              it["equipment_id"], it["creates_task"]))

    await db.commit()
    return {"id": new_id}


@router.post("/templates", status_code=201)
async def create_template(data: TemplateCreate, db=Depends(get_db)):
    async with db.execute("""
        INSERT INTO pmcs_templates (title, description, equipment_id)
        VALUES (?, ?, ?)
    """, (data.title, data.description, data.equipment_id)) as cur:
        tmpl_id = cur.lastrowid
    await db.commit()
    return {"id": tmpl_id}


@router.put("/templates/{tmpl_id}")
async def update_template(tmpl_id: int, data: TemplateCreate, db=Depends(get_db)):
    await db.execute("""
        UPDATE pmcs_templates SET title=?, description=?, equipment_id=?,
               updated_at=datetime('now')
        WHERE id=?
    """, (data.title, data.description, data.equipment_id, tmpl_id))
    await db.commit()
    return {"ok": True}


@router.delete("/templates/{tmpl_id}")
async def delete_template(tmpl_id: int, db=Depends(get_db)):
    await db.execute("DELETE FROM pmcs_templates WHERE id=?", (tmpl_id,))
    await db.commit()
    return {"ok": True}


# ── Template Equipment (multi-equipment PMCS) ──────────────────────────────────

@router.get("/templates/{tmpl_id}/equipment")
async def list_template_equipment(tmpl_id: int, db=Depends(get_db)):
    async with db.execute("""
        SELECT e.id, e.name, e.category, e.serial_num, te.order_index,
               (SELECT COUNT(*) FROM pmcs_items WHERE template_id=? AND equipment_id=e.id) as item_count
        FROM pmcs_template_equipment te
        JOIN equipment e ON e.id = te.equipment_id
        WHERE te.template_id = ?
        ORDER BY te.order_index, e.name
    """, (tmpl_id, tmpl_id)) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.post("/templates/{tmpl_id}/equipment", status_code=201)
async def add_template_equipment(tmpl_id: int, data: TemplateEquipmentAdd, db=Depends(get_db)):
    async with db.execute("SELECT id FROM pmcs_templates WHERE id=?", (tmpl_id,)) as cur:
        if not await cur.fetchone():
            raise HTTPException(404, "Template not found")
    async with db.execute("SELECT id FROM equipment WHERE id=?", (data.equipment_id,)) as cur:
        if not await cur.fetchone():
            raise HTTPException(404, "Equipment not found")
    try:
        async with db.execute("""
            INSERT INTO pmcs_template_equipment (template_id, equipment_id)
            VALUES (?, ?)
        """, (tmpl_id, data.equipment_id)) as cur:
            row_id = cur.lastrowid
        await db.commit()
        return {"id": row_id}
    except Exception:
        raise HTTPException(409, "Equipment already added to this PMCS template")


@router.delete("/templates/{tmpl_id}/equipment/{eq_id}")
async def remove_template_equipment(tmpl_id: int, eq_id: int, db=Depends(get_db)):
    await db.execute(
        "DELETE FROM pmcs_template_equipment WHERE template_id=? AND equipment_id=?",
        (tmpl_id, eq_id)
    )
    # Optionally remove items scoped to this equipment from this template
    await db.execute(
        "DELETE FROM pmcs_items WHERE template_id=? AND equipment_id=?",
        (tmpl_id, eq_id)
    )
    await db.commit()
    return {"ok": True}


# ── Reorder ──────────────────────────────────────────────────────────────────

@router.put("/templates/{tmpl_id}/equipment/reorder")
async def reorder_equipment(tmpl_id: int, eq_ids: List[int], db=Depends(get_db)):
    """Accept an ordered list of equipment IDs and update their order_index."""
    for idx, eq_id in enumerate(eq_ids):
        await db.execute(
            "UPDATE pmcs_template_equipment SET order_index=? WHERE template_id=? AND equipment_id=?",
            (idx, tmpl_id, eq_id)
        )
    await db.commit()
    return {"ok": True}


@router.put("/templates/{tmpl_id}/items/reorder")
async def reorder_items(tmpl_id: int, item_ids: List[int], db=Depends(get_db)):
    """Accept an ordered list of item IDs and update their order_index."""
    for idx, item_id in enumerate(item_ids):
        await db.execute(
            "UPDATE pmcs_items SET order_index=? WHERE id=? AND template_id=?",
            (idx, item_id, tmpl_id)
        )
    await db.commit()
    return {"ok": True}


# ── Items ─────────────────────────────────────────────────────────────────────

@router.post("/templates/{tmpl_id}/items", status_code=201)
async def add_item(tmpl_id: int, data: ItemCreate, db=Depends(get_db)):
    async with db.execute("""
        INSERT INTO pmcs_items (template_id, item_no, interval, check_item,
                                procedure, not_ready_if, order_index,
                                equipment_id, creates_task)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (tmpl_id, data.item_no, data.interval, data.check_item,
          data.procedure, data.not_ready_if, data.order_index,
          data.equipment_id, 1 if data.creates_task else 0)) as cur:
        item_id = cur.lastrowid
    await db.commit()
    return {"id": item_id}


@router.put("/items/{item_id}")
async def update_item(item_id: int, data: ItemUpdate, db=Depends(get_db)):
    await db.execute("""
        UPDATE pmcs_items SET item_no=?, interval=?, check_item=?,
               procedure=?, not_ready_if=?, order_index=?,
               equipment_id=?, creates_task=?
        WHERE id=?
    """, (data.item_no, data.interval, data.check_item,
          data.procedure, data.not_ready_if, data.order_index,
          data.equipment_id, 1 if data.creates_task else 0, item_id))
    await db.commit()
    return {"ok": True}


@router.delete("/items/{item_id}")
async def delete_item(item_id: int, db=Depends(get_db)):
    await db.execute("DELETE FROM pmcs_items WHERE id=?", (item_id,))
    await db.commit()
    return {"ok": True}


class ItemBulkCreate(BaseModel):
    """Create the same check item for multiple equipment IDs at once."""
    equipment_ids: List[int]
    item_no: Optional[str] = None
    interval: str = "B"
    check_item: str
    procedure: Optional[str] = None
    not_ready_if: Optional[str] = None
    creates_task: bool = False


@router.post("/templates/{tmpl_id}/items/bulk", status_code=201)
async def bulk_add_items(tmpl_id: int, data: ItemBulkCreate, db=Depends(get_db)):
    """Create the same check item for each of the supplied equipment IDs."""
    if not data.equipment_ids:
        raise HTTPException(400, "No equipment IDs supplied")
    async with db.execute(
        "SELECT COUNT(*) FROM pmcs_templates WHERE id=?", (tmpl_id,)
    ) as cur:
        if not (await cur.fetchone())[0]:
            raise HTTPException(404, "Template not found")
    created = 0
    for eq_id in data.equipment_ids:
        async with db.execute(
            "SELECT MAX(order_index) FROM pmcs_items WHERE template_id=? AND equipment_id=?",
            (tmpl_id, eq_id)
        ) as cur:
            row = await cur.fetchone()
            order_index = (row[0] or 0) + 1
        await db.execute("""
            INSERT INTO pmcs_items
                (template_id, item_no, interval, check_item, procedure,
                 not_ready_if, order_index, equipment_id, creates_task)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (tmpl_id, data.item_no, data.interval, data.check_item,
              data.procedure, data.not_ready_if, order_index,
              eq_id, 1 if data.creates_task else 0))
        created += 1
    await db.commit()
    return {"created": created}


@router.post("/templates/{tmpl_id}/equipment/{eq_id}/copy-from/{src_eq_id}", status_code=201)
async def copy_items_between_equipment(tmpl_id: int, eq_id: int, src_eq_id: int, db=Depends(get_db)):
    """Copy all check items from src_eq_id to eq_id within the same template."""
    async with db.execute(
        "SELECT * FROM pmcs_items WHERE template_id=? AND equipment_id=? ORDER BY order_index, id",
        (tmpl_id, src_eq_id)
    ) as cur:
        src_items = [dict(i) for i in await cur.fetchall()]
    async with db.execute(
        "SELECT MAX(order_index) FROM pmcs_items WHERE template_id=? AND equipment_id=?",
        (tmpl_id, eq_id)
    ) as cur:
        row = await cur.fetchone()
        base_index = (row[0] or 0) + 1
    for i, it in enumerate(src_items):
        await db.execute("""
            INSERT INTO pmcs_items
                (template_id, item_no, interval, check_item, procedure,
                 not_ready_if, order_index, equipment_id, creates_task)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (tmpl_id, it["item_no"], it["interval"], it["check_item"],
              it["procedure"], it["not_ready_if"], base_index + i,
              eq_id, it["creates_task"]))
    await db.commit()
    return {"copied": len(src_items)}


# ── Sessions ──────────────────────────────────────────────────────────────────

@router.get("/sessions")
async def list_sessions(template_id: int = None, db=Depends(get_db)):
    query = """
        SELECT s.*, t.title as template_title, e.name as equipment_name
        FROM pmcs_sessions s
        JOIN pmcs_templates t ON t.id = s.template_id
        LEFT JOIN equipment e ON e.id = t.equipment_id
        WHERE s.status = 'completed'
    """
    params = []
    if template_id:
        query += " AND s.template_id = ?"
        params.append(template_id)
    query += " ORDER BY s.completed_at DESC"
    async with db.execute(query, params) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.post("/templates/{tmpl_id}/sessions", status_code=201)
async def start_session(tmpl_id: int, data: SessionStart, db=Depends(get_db)):
    async with db.execute(
        "SELECT id FROM pmcs_templates WHERE id=?", (tmpl_id,)
    ) as cur:
        if not await cur.fetchone():
            raise HTTPException(404, "Template not found")
    async with db.execute("""
        INSERT INTO pmcs_sessions (template_id, operator_name, operator_rank)
        VALUES (?, ?, ?)
    """, (tmpl_id, data.operator_name, data.operator_rank)) as cur:
        session_id = cur.lastrowid
    await db.commit()
    return {"id": session_id}


@router.post("/sessions/{session_id}/complete")
async def complete_session(session_id: int, data: SessionComplete, db=Depends(get_db)):
    async with db.execute("""
        SELECT s.*, t.title, t.equipment_id, e.name as equipment_name
        FROM pmcs_sessions s
        JOIN pmcs_templates t ON t.id = s.template_id
        LEFT JOIN equipment e ON e.id = t.equipment_id
        WHERE s.id=?
    """, (session_id,)) as cur:
        session = await cur.fetchone()
    if not session:
        raise HTTPException(404, "Session not found")
    session = dict(session)

    # Fetch all items for this template (with equipment info)
    async with db.execute("""
        SELECT pi.*, e.name as eq_name
        FROM pmcs_items pi
        LEFT JOIN equipment e ON e.id = pi.equipment_id
        WHERE pi.template_id=?
        ORDER BY pi.equipment_id NULLS LAST, pi.order_index, pi.id
    """, (session["template_id"],)) as cur:
        items = [dict(i) for i in await cur.fetchall()]

    items_by_id = {i["id"]: i for i in items}

    # Persist results
    await db.execute("DELETE FROM pmcs_results WHERE session_id=?", (session_id,))
    fault_count = 0
    tasks_created = 0
    for r in data.results:
        if r.status == "fault":
            fault_count += 1
        await db.execute("""
            INSERT INTO pmcs_results (session_id, item_id, status, notes)
            VALUES (?, ?, ?, ?)
        """, (session_id, r.item_id, r.status, r.notes))

        # Auto-create maintenance task for faults on items that have creates_task set
        if r.status == "fault":
            item = items_by_id.get(r.item_id)
            if item and item.get("creates_task") and item.get("equipment_id"):
                await db.execute("""
                    INSERT INTO maintenance_tasks
                        (equipment_id, title, description, task_type, status, notes)
                    VALUES (?, ?, ?, 'inspection', 'pending', ?)
                """, (
                    item["equipment_id"],
                    f"PMCS Fault: {item['check_item']}",
                    f"Fault identified during PMCS session: {session['title']}",
                    r.notes or ""
                ))
                tasks_created += 1

    # Build items+results for PDF (grouped by equipment)
    results_by_item = {r.item_id: r for r in data.results}
    items_results = []
    for it in items:
        res = results_by_item.get(it["id"])
        items_results.append({
            "item_no":      it.get("item_no") or str(items.index(it) + 1),
            "interval":     it.get("interval", "B"),
            "check_item":   it.get("check_item", ""),
            "procedure":    it.get("procedure") or "",
            "not_ready_if": it.get("not_ready_if") or "",
            "status":       res.status if res else "na",
            "notes":        res.notes if res else "",
            "equipment_name": it.get("eq_name") or "",
        })

    # Fetch linked equipment names for PDF header
    async with db.execute("""
        SELECT e.name FROM pmcs_template_equipment te
        JOIN equipment e ON e.id = te.equipment_id
        WHERE te.template_id=? ORDER BY te.order_index, e.name
    """, (session["template_id"],)) as cur:
        linked_eq_names = [r[0] for r in await cur.fetchall()]
    equipment_display = session.get("equipment_name") or (", ".join(linked_eq_names) if linked_eq_names else "")

    now = datetime.utcnow().isoformat()
    op_name = data.operator_name or session.get("operator_name") or ""
    op_rank = data.operator_rank or session.get("operator_rank") or ""

    pdf_bytes = generate_pmcs_pdf(
        template_title=session["title"],
        equipment_name=equipment_display,
        operator_name=op_name,
        operator_rank=op_rank,
        completed_at=now,
        session_notes=data.notes or "",
        items_results=items_results,
    )

    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    filename = f"PMCS_{session_id}_{now[:10]}.pdf"
    filepath = os.path.join(ARCHIVE_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(pdf_bytes)

    await db.execute("""
        UPDATE pmcs_sessions
        SET status='completed', fault_count=?, archive_path=?,
            operator_name=?, operator_rank=?, notes=?, completed_at=?
        WHERE id=?
    """, (fault_count, filepath, op_name, op_rank, data.notes, now, session_id))
    await db.commit()
    return {
        "ok": True,
        "session_id": session_id,
        "fault_count": fault_count,
        "tasks_created": tasks_created,
        "filename": filename,
    }


@router.get("/sessions/{session_id}/archive")
async def download_archive(session_id: int, db=Depends(get_db)):
    async with db.execute(
        "SELECT archive_path FROM pmcs_sessions WHERE id=?", (session_id,)
    ) as cur:
        row = await cur.fetchone()
    if not row or not row["archive_path"]:
        raise HTTPException(404, "Archive not found")
    path = row["archive_path"]
    if not os.path.exists(path):
        raise HTTPException(404, "Archive file missing")
    with open(path, "rb") as f:
        pdf_bytes = f.read()
    return Response(content=pdf_bytes, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{os.path.basename(path)}"'})


@router.get("/equipment/{equipment_id}/sessions")
async def equipment_pmcs_sessions(equipment_id: int, db=Depends(get_db)):
    """All PMCS sessions for an equipment record — direct assignment or multi-equipment template."""
    async with db.execute("""
        SELECT s.id, s.started_at, s.completed_at, s.status,
               s.operator_name, s.operator_rank, s.fault_count, s.notes,
               t.id as template_id, t.title as template_title
        FROM pmcs_sessions s
        JOIN pmcs_templates t ON t.id = s.template_id
        WHERE t.equipment_id = ?
           OR t.id IN (
               SELECT template_id FROM pmcs_template_equipment WHERE equipment_id = ?
           )
        ORDER BY s.started_at DESC
        LIMIT 100
    """, (equipment_id, equipment_id)) as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    return rows
