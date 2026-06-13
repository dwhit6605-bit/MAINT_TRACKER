"""Inventory reorder requests."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
from backend.database import get_db
from backend.auth import require_admin

router = APIRouter(prefix="/api/reorder", tags=["reorder"])


class ReorderCreate(BaseModel):
    item_id: int
    qty_requested: int = 1
    requested_by: Optional[str] = None
    supplier: Optional[str] = None
    notes: Optional[str] = None


class ReorderUpdate(BaseModel):
    status: str  # pending | ordered | received | cancelled


@router.get("")
async def list_reorders(db=Depends(get_db)):
    async with db.execute("""
        SELECT r.*, i.name as item_name, i.part_number, i.unit,
               i.quantity as current_qty, i.min_stock, i.location
        FROM reorder_requests r
        JOIN inventory_items i ON i.id = r.item_id
        ORDER BY r.created_at DESC
    """) as cur:
        return [dict(row) for row in await cur.fetchall()]


@router.post("", status_code=201)
async def create_reorder(data: ReorderCreate, db=Depends(get_db)):
    async with db.execute("""
        INSERT INTO reorder_requests (item_id, qty_requested, requested_by, supplier, notes)
        VALUES (?,?,?,?,?)
    """, (data.item_id, data.qty_requested, data.requested_by, data.supplier, data.notes)) as cur:
        rid = cur.lastrowid
    await db.commit()
    return {"id": rid}


@router.patch("/{rid}")
async def update_reorder(rid: int, data: ReorderUpdate, request: Request, db=Depends(get_db)):
    require_admin(request)
    valid = {"pending", "ordered", "received", "cancelled"}
    if data.status not in valid:
        raise HTTPException(400, f"status must be one of {valid}")
    await db.execute("""
        UPDATE reorder_requests SET status=?, updated_at=datetime('now') WHERE id=?
    """, (data.status, rid))
    # If received, bump inventory quantity by qty_requested
    if data.status == "received":
        async with db.execute(
            "SELECT item_id, qty_requested FROM reorder_requests WHERE id=?", (rid,)
        ) as cur:
            row = await cur.fetchone()
        if row:
            await db.execute("""
                UPDATE inventory_items
                SET quantity = quantity + ?, updated_at=datetime('now')
                WHERE id=?
            """, (row["qty_requested"], row["item_id"]))
            await db.execute("""
                INSERT INTO inventory_transactions
                    (item_id, action, quantity, reference, performed_by)
                VALUES (?, 'add', ?, 'Reorder received', 'system')
            """, (row["item_id"], row["qty_requested"]))
    await db.commit()
    return {"ok": True}


@router.delete("/{rid}", status_code=204)
async def delete_reorder(rid: int, request: Request, db=Depends(get_db)):
    require_admin(request)
    await db.execute("DELETE FROM reorder_requests WHERE id=?", (rid,))
    await db.commit()
