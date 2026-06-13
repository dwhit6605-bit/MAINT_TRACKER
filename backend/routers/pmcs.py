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
    equipment_id: Optional[int] = None

class ItemCreate(BaseModel):
    item_no: Optional[str] = None
    interval: str = "B"
    check_item: str
    procedure: Optional[str] = None
    not_ready_if: Optional[str] = None
    order_index: int = 0

class ItemUpdate(ItemCreate):
    pass

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
    return [dict(r) for r in rows]


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
    async with db.execute(
        "SELECT * FROM pmcs_items WHERE template_id=? ORDER BY order_index, id",
        (tmpl_id,)
    ) as cur:
        items = await cur.fetchall()
    return {**dict(tmpl), "items": [dict(i) for i in items]}


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


# ── Items ─────────────────────────────────────────────────────────────────────

@router.post("/templates/{tmpl_id}/items", status_code=201)
async def add_item(tmpl_id: int, data: ItemCreate, db=Depends(get_db)):
    async with db.execute("""
        INSERT INTO pmcs_items (template_id, item_no, interval, check_item,
                                procedure, not_ready_if, order_index)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (tmpl_id, data.item_no, data.interval, data.check_item,
          data.procedure, data.not_ready_if, data.order_index)) as cur:
        item_id = cur.lastrowid
    await db.commit()
    return {"id": item_id}


@router.put("/items/{item_id}")
async def update_item(item_id: int, data: ItemUpdate, db=Depends(get_db)):
    await db.execute("""
        UPDATE pmcs_items SET item_no=?, interval=?, check_item=?,
               procedure=?, not_ready_if=?, order_index=?
        WHERE id=?
    """, (data.item_no, data.interval, data.check_item,
          data.procedure, data.not_ready_if, data.order_index, item_id))
    await db.commit()
    return {"ok": True}


@router.delete("/items/{item_id}")
async def delete_item(item_id: int, db=Depends(get_db)):
    await db.execute("DELETE FROM pmcs_items WHERE id=?", (item_id,))
    await db.commit()
    return {"ok": True}


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

    # persist results
    await db.execute("DELETE FROM pmcs_results WHERE session_id=?", (session_id,))
    fault_count = 0
    for r in data.results:
        if r.status == "fault":
            fault_count += 1
        await db.execute("""
            INSERT INTO pmcs_results (session_id, item_id, status, notes)
            VALUES (?, ?, ?, ?)
        """, (session_id, r.item_id, r.status, r.notes))

    # fetch full items for PDF
    async with db.execute(
        "SELECT * FROM pmcs_items WHERE template_id=? ORDER BY order_index, id",
        (session["template_id"],)
    ) as cur:
        items = [dict(i) for i in await cur.fetchall()]

    results_by_item = {r.item_id: r for r in data.results}
    items_results = []
    for it in items:
        res = results_by_item.get(it["id"])
        items_results.append({
            "item_no":     it.get("item_no") or str(items.index(it) + 1),
            "interval":    it.get("interval", "B"),
            "check_item":  it.get("check_item", ""),
            "procedure":   it.get("procedure") or "",
            "not_ready_if": it.get("not_ready_if") or "",
            "status":      res.status if res else "na",
            "notes":       res.notes if res else "",
        })

    now = datetime.utcnow().isoformat()
    op_name = data.operator_name or session.get("operator_name") or ""
    op_rank = data.operator_rank or session.get("operator_rank") or ""

    pdf_bytes = generate_pmcs_pdf(
        template_title=session["title"],
        equipment_name=session.get("equipment_name") or "",
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
    return {"ok": True, "session_id": session_id, "fault_count": fault_count, "filename": filename}


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
    """All PMCS sessions for an equipment record, across all its templates."""
    async with db.execute("""
        SELECT s.id, s.started_at, s.completed_at, s.status,
               s.operator_name, s.operator_rank, s.fault_count, s.notes,
               t.id as template_id, t.title as template_title
        FROM pmcs_sessions s
        JOIN pmcs_templates t ON t.id = s.template_id
        WHERE t.equipment_id = ?
        ORDER BY s.started_at DESC
        LIMIT 100
    """, (equipment_id,)) as cur:
        return [dict(r) for r in await cur.fetchall()]
