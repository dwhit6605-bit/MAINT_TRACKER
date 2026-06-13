import asyncio
from fastapi import APIRouter, Depends, HTTPException, Request
from backend.database import get_db
from backend.models import InventoryItemCreate, InventoryAdjust
from backend.auth import require_admin, require_tech
from backend.notifications import send_low_stock_alert

router = APIRouter(prefix="/api/inventory", tags=["inventory"])


@router.get("")
async def list_items(low_stock: bool = False, db=Depends(get_db)):
    query = "SELECT * FROM inventory_items"
    if low_stock:
        query += " WHERE quantity <= min_stock"
    query += " ORDER BY name"
    async with db.execute(query) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.get("/{item_id}")
async def get_item(item_id: int, db=Depends(get_db)):
    async with db.execute("SELECT * FROM inventory_items WHERE id=?", (item_id,)) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Item not found")
    async with db.execute("""
        SELECT * FROM inventory_transactions WHERE item_id=? ORDER BY created_at DESC LIMIT 50
    """, (item_id,)) as cur:
        txns = await cur.fetchall()
    return {**dict(row), "transactions": [dict(t) for t in txns]}


@router.post("", status_code=201)
async def create_item(data: InventoryItemCreate, request: Request, db=Depends(get_db)):
    require_tech(request)
    async with db.execute("""
        INSERT INTO inventory_items
            (name, part_number, category, location, quantity, unit, min_stock, unit_cost, supplier, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (data.name, data.part_number, data.category, data.location,
          data.quantity, data.unit, data.min_stock, data.unit_cost,
          data.supplier, data.notes)) as cur:
        item_id = cur.lastrowid
    if data.quantity > 0:
        await db.execute("""
            INSERT INTO inventory_transactions (item_id, action, quantity, reference)
            VALUES (?, 'add', ?, 'initial stock')
        """, (item_id, data.quantity))
    await db.commit()
    return {"id": item_id}


@router.put("/{item_id}")
async def update_item(item_id: int, data: InventoryItemCreate, request: Request, db=Depends(get_db)):
    require_tech(request)
    await db.execute("""
        UPDATE inventory_items
        SET name=?, part_number=?, category=?, location=?, unit=?, min_stock=?,
            unit_cost=?, supplier=?, notes=?, updated_at=datetime('now')
        WHERE id=?
    """, (data.name, data.part_number, data.category, data.location,
          data.unit, data.min_stock, data.unit_cost, data.supplier, data.notes, item_id))
    await db.commit()
    return {"ok": True}


@router.post("/{item_id}/adjust")
async def adjust_stock(item_id: int, data: InventoryAdjust, request: Request, db=Depends(get_db)):
    require_tech(request)
    async with db.execute("SELECT * FROM inventory_items WHERE id=?", (item_id,)) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Item not found")

    current = row["quantity"]
    if data.action == "add":
        new_qty = current + data.quantity
    elif data.action == "remove":
        new_qty = max(0, current - data.quantity)
    elif data.action == "set":
        new_qty = data.quantity
    else:
        raise HTTPException(400, "action must be add, remove, or set")

    await db.execute("""
        UPDATE inventory_items SET quantity=?, updated_at=datetime('now') WHERE id=?
    """, (new_qty, item_id))
    await db.execute("""
        INSERT INTO inventory_transactions (item_id, action, quantity, reference, performed_by)
        VALUES (?, ?, ?, ?, ?)
    """, (item_id, data.action, data.quantity, data.reference, data.performed_by))
    await db.commit()
    # Fire low-stock alert if qty dropped to or below min_stock
    min_stock = row["min_stock"] or 0
    if min_stock > 0 and new_qty <= min_stock and (data.action in ("remove", "set")):
        asyncio.create_task(send_low_stock_alert(
            item_id, row["name"], new_qty, min_stock, row["unit"] or ""
        ))
    return {"quantity": new_qty}


@router.delete("/{item_id}")
async def delete_item(item_id: int, request: Request, db=Depends(get_db)):
    require_admin(request)
    await db.execute("DELETE FROM inventory_items WHERE id=?", (item_id,))
    await db.commit()
    return {"ok": True}
