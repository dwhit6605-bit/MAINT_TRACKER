"""Parts & inventory, stocked across multiple storage locations.

``inventory_stock`` holds the quantity per (item, location); ``inventory_items.quantity``
is a rollup of that sum maintained by database triggers. Everything here writes stock
through :func:`_apply_delta` so the rollup, the per-bin counts, and the transaction log
can never disagree.

Callers that predate multi-location (maintenance parts-used, reorder receive, SKO issue)
omit a location; :func:`resolve_location` sends them to the item's largest bin so their
behaviour is unchanged from the user's point of view.
"""
import asyncio
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
from backend.database import get_db
from backend.models import InventoryItemCreate, InventoryAdjust
from backend.auth import require_admin, require_tech, require_superadmin
from backend.notifications import send_low_stock_alert

router = APIRouter(prefix="/api/inventory", tags=["inventory"])

UNASSIGNED = "UNASSIGNED"


class LocationCreate(BaseModel):
    code: str
    name: Optional[str] = None
    zone: Optional[str] = None
    active: bool = True
    sort_order: int = 0


class StockSet(BaseModel):
    location_id: int
    quantity: int
    reference: Optional[str] = None
    performed_by: Optional[str] = None


class StockTransfer(BaseModel):
    from_location_id: int
    to_location_id: int
    quantity: int
    reference: Optional[str] = None
    performed_by: Optional[str] = None


# ── Stock helpers ─────────────────────────────────────────────────────────────

async def ensure_unassigned(db) -> int:
    async with db.execute(
        "SELECT id FROM inventory_locations WHERE code=?", (UNASSIGNED,)
    ) as cur:
        row = await cur.fetchone()
    if row:
        return row["id"]
    async with db.execute(
        "INSERT INTO inventory_locations (code,name,sort_order) VALUES (?,?,999)",
        (UNASSIGNED, "Unassigned"),
    ) as cur:
        return cur.lastrowid


async def resolve_location(db, item_id: int, location_id: Optional[int] = None) -> int:
    """Pick the bin an unlocated operation should hit: the item's largest."""
    if location_id is not None:
        async with db.execute(
            "SELECT id FROM inventory_locations WHERE id=?", (location_id,)
        ) as cur:
            if not await cur.fetchone():
                raise HTTPException(404, "Location not found")
        return location_id
    async with db.execute(
        "SELECT location_id FROM inventory_stock WHERE item_id=? ORDER BY quantity DESC LIMIT 1",
        (item_id,),
    ) as cur:
        row = await cur.fetchone()
    return row["location_id"] if row else await ensure_unassigned(db)


async def _apply_delta(db, item_id: int, location_id: int, delta: int) -> int:
    """Add ``delta`` to one bin, clamped at zero. Returns the bin's new quantity."""
    async with db.execute(
        "SELECT quantity FROM inventory_stock WHERE item_id=? AND location_id=?",
        (item_id, location_id),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        new_qty = max(0, delta)
        await db.execute(
            "INSERT INTO inventory_stock (item_id,location_id,quantity) VALUES (?,?,?)",
            (item_id, location_id, new_qty),
        )
    else:
        new_qty = max(0, row["quantity"] + delta)
        await db.execute(
            "UPDATE inventory_stock SET quantity=?, updated_at=datetime('now') "
            "WHERE item_id=? AND location_id=?",
            (new_qty, item_id, location_id),
        )
    return new_qty


async def consume_stock(db, item_id: int, qty: int, *, reference: str = None,
                        performed_by: str = None, location_id: int = None) -> int:
    """Draw ``qty`` off an item. Shared by maintenance, SKO issue and reorder."""
    loc = await resolve_location(db, item_id, location_id)
    await _apply_delta(db, item_id, loc, -abs(qty))
    await db.execute(
        "INSERT INTO inventory_transactions (item_id,action,quantity,reference,performed_by,location_id) "
        "VALUES (?,'remove',?,?,?,?)",
        (item_id, abs(qty), reference, performed_by, loc),
    )
    return loc


async def receive_stock(db, item_id: int, qty: int, *, reference: str = None,
                        performed_by: str = None, location_id: int = None) -> int:
    loc = await resolve_location(db, item_id, location_id)
    await _apply_delta(db, item_id, loc, abs(qty))
    await db.execute(
        "INSERT INTO inventory_transactions (item_id,action,quantity,reference,performed_by,location_id) "
        "VALUES (?,'add',?,?,?,?)",
        (item_id, abs(qty), reference, performed_by, loc),
    )
    return loc


async def _breakdown(db, item_ids=None):
    """Per-location stock keyed by item id (bins holding zero are omitted)."""
    sql = """
        SELECT s.item_id, s.location_id, s.quantity, l.code, l.name, l.zone
        FROM inventory_stock s JOIN inventory_locations l ON l.id=s.location_id
        WHERE s.quantity <> 0
        ORDER BY l.sort_order, l.code
    """
    out = {}
    async with db.execute(sql) as cur:
        for r in await cur.fetchall():
            if item_ids is not None and r["item_id"] not in item_ids:
                continue
            out.setdefault(r["item_id"], []).append({
                "location_id": r["location_id"], "code": r["code"],
                "name": r["name"], "zone": r["zone"], "quantity": r["quantity"],
            })
    return out


# ── Locations (registered before /{item_id} so the path doesn't shadow them) ───

@router.get("/locations")
async def list_locations(db=Depends(get_db)):
    async with db.execute("""
        SELECT l.*,
          (SELECT COUNT(*) FROM inventory_stock s WHERE s.location_id=l.id AND s.quantity<>0) as item_count,
          (SELECT COALESCE(SUM(quantity),0) FROM inventory_stock s WHERE s.location_id=l.id) as unit_count
        FROM inventory_locations l ORDER BY l.sort_order, l.code
    """) as cur:
        return [dict(r) for r in await cur.fetchall()]


@router.post("/locations", status_code=201)
async def create_location(data: LocationCreate, request: Request, db=Depends(get_db)):
    require_tech(request)
    code = data.code.strip().upper()
    if not code:
        raise HTTPException(400, "Code is required")
    async with db.execute("SELECT id FROM inventory_locations WHERE code=?", (code,)) as cur:
        if await cur.fetchone():
            raise HTTPException(409, f"Location '{code}' already exists")
    async with db.execute(
        "INSERT INTO inventory_locations (code,name,zone,active,sort_order) VALUES (?,?,?,?,?)",
        (code, data.name or code.replace("-", " ").title(), data.zone,
         1 if data.active else 0, data.sort_order),
    ) as cur:
        return {"id": cur.lastrowid, "code": code}


@router.put("/locations/{loc_id}")
async def update_location(loc_id: int, data: LocationCreate, request: Request, db=Depends(get_db)):
    require_tech(request)
    code = data.code.strip().upper()
    async with db.execute(
        "SELECT id FROM inventory_locations WHERE code=? AND id<>?", (code, loc_id)
    ) as cur:
        if await cur.fetchone():
            raise HTTPException(409, f"Location '{code}' already exists")
    await db.execute(
        "UPDATE inventory_locations SET code=?,name=?,zone=?,active=?,sort_order=? WHERE id=?",
        (code, data.name, data.zone, 1 if data.active else 0, data.sort_order, loc_id),
    )
    await db.commit()
    return {"ok": True}


@router.delete("/locations/{loc_id}")
async def delete_location(loc_id: int, request: Request, db=Depends(get_db)):
    """Refuses while stock is still on the shelf — move it out first."""
    require_superadmin(request)
    async with db.execute(
        "SELECT COALESCE(SUM(quantity),0) q, COUNT(*) n FROM inventory_stock "
        "WHERE location_id=? AND quantity<>0", (loc_id,)
    ) as cur:
        row = await cur.fetchone()
    if row and row["n"]:
        raise HTTPException(
            409, f"{row['n']} item(s) totalling {row['q']} units are still stored here. "
                 f"Transfer them out before deleting this location.")
    await db.execute("DELETE FROM inventory_stock WHERE location_id=?", (loc_id,))
    await db.execute("DELETE FROM inventory_locations WHERE id=?", (loc_id,))
    await db.commit()
    return {"ok": True}


# ── Items ─────────────────────────────────────────────────────────────────────

@router.get("")
async def list_items(low_stock: bool = False, location_id: int = None, db=Depends(get_db)):
    query = "SELECT * FROM inventory_items"
    params = ()
    if location_id:
        query += (" WHERE id IN (SELECT item_id FROM inventory_stock "
                  "WHERE location_id=? AND quantity<>0)")
        params = (location_id,)
    if low_stock:
        query += (" AND" if location_id else " WHERE") + " quantity <= min_stock"
    query += " ORDER BY name"
    async with db.execute(query, params) as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    bd = await _breakdown(db, {r["id"] for r in rows})
    for r in rows:
        r["stock"] = bd.get(r["id"], [])
        r["location_count"] = len(r["stock"])
    return rows


@router.get("/{item_id}/usage")
async def get_item_usage(item_id: int, db=Depends(get_db)):
    async with db.execute("""
        SELECT tp.quantity_used, tp.created_at as used_at,
               m.title as task_title, e.name as equipment_name
        FROM task_parts_used tp
        JOIN maintenance_tasks m ON m.id = tp.task_id
        JOIN equipment e ON e.id = m.equipment_id
        WHERE tp.item_id = ?
        ORDER BY tp.created_at DESC
        LIMIT 100
    """, (item_id,)) as cur:
        return [dict(r) for r in await cur.fetchall()]


@router.get("/{item_id}")
async def get_item(item_id: int, db=Depends(get_db)):
    async with db.execute("SELECT * FROM inventory_items WHERE id=?", (item_id,)) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Item not found")
    async with db.execute("""
        SELECT t.*, l.code as location_code, l2.code as to_location_code
        FROM inventory_transactions t
        LEFT JOIN inventory_locations l  ON l.id  = t.location_id
        LEFT JOIN inventory_locations l2 ON l2.id = t.to_location_id
        WHERE t.item_id=? ORDER BY t.created_at DESC LIMIT 50
    """, (item_id,)) as cur:
        txns = [dict(t) for t in await cur.fetchall()]
    bd = await _breakdown(db, {item_id})
    return {**dict(row), "stock": bd.get(item_id, []), "transactions": txns}


@router.post("", status_code=201)
async def create_item(data: InventoryItemCreate, request: Request, db=Depends(get_db)):
    require_tech(request)
    async with db.execute("""
        INSERT INTO inventory_items
            (name, part_number, category, location, quantity, unit, min_stock, unit_cost, supplier, notes)
        VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, ?)
    """, (data.name, data.part_number, data.category, data.location,
          data.unit, data.min_stock, data.unit_cost,
          data.supplier, data.notes)) as cur:
        item_id = cur.lastrowid
    if data.quantity:
        # Place opening stock in the named location if it exists, else Unassigned
        loc_id = None
        if data.location:
            async with db.execute(
                "SELECT id FROM inventory_locations WHERE code=?",
                (data.location.strip().upper(),)) as cur:
                r = await cur.fetchone()
                loc_id = r["id"] if r else None
        await receive_stock(db, item_id, data.quantity,
                            reference="initial stock", location_id=loc_id)
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
    if data.quantity < 0:
        raise HTTPException(400, "Quantity cannot be negative")

    loc = await resolve_location(db, item_id, data.location_id)
    if data.action == "add":
        await _apply_delta(db, item_id, loc, data.quantity)
    elif data.action == "remove":
        await _apply_delta(db, item_id, loc, -data.quantity)
    elif data.action == "set":
        async with db.execute(
            "SELECT quantity FROM inventory_stock WHERE item_id=? AND location_id=?",
            (item_id, loc)) as cur:
            cur_row = await cur.fetchone()
        await _apply_delta(db, item_id, loc,
                           data.quantity - (cur_row["quantity"] if cur_row else 0))
    else:
        raise HTTPException(400, "action must be add, remove, or set")

    await db.execute(
        "INSERT INTO inventory_transactions (item_id,action,quantity,reference,performed_by,location_id) "
        "VALUES (?,?,?,?,?,?)",
        (item_id, data.action, data.quantity, data.reference, data.performed_by, loc),
    )
    await db.commit()

    async with db.execute("SELECT quantity FROM inventory_items WHERE id=?", (item_id,)) as cur:
        new_total = (await cur.fetchone())["quantity"]

    min_stock = row["min_stock"] or 0
    if min_stock > 0 and new_total <= min_stock and data.action in ("remove", "set"):
        asyncio.create_task(send_low_stock_alert(
            item_id, row["name"], new_total, min_stock, row["unit"] or ""
        ))
    return {"quantity": new_total, "location_id": loc}


@router.post("/{item_id}/stock")
async def set_bin_quantity(item_id: int, data: StockSet, request: Request, db=Depends(get_db)):
    """Set one bin's count outright — the counting-the-shelf workflow."""
    require_tech(request)
    async with db.execute("SELECT name FROM inventory_items WHERE id=?", (item_id,)) as cur:
        if not await cur.fetchone():
            raise HTTPException(404, "Item not found")
    if data.quantity < 0:
        raise HTTPException(400, "Quantity cannot be negative")
    loc = await resolve_location(db, item_id, data.location_id)
    async with db.execute(
        "SELECT quantity FROM inventory_stock WHERE item_id=? AND location_id=?",
        (item_id, loc)) as cur:
        row = await cur.fetchone()
    before = row["quantity"] if row else 0
    await _apply_delta(db, item_id, loc, data.quantity - before)
    await db.execute(
        "INSERT INTO inventory_transactions (item_id,action,quantity,reference,performed_by,location_id) "
        "VALUES (?,'set',?,?,?,?)",
        (item_id, data.quantity, data.reference or "bin count", data.performed_by, loc),
    )
    await db.commit()
    async with db.execute("SELECT quantity FROM inventory_items WHERE id=?", (item_id,)) as cur:
        return {"quantity": (await cur.fetchone())["quantity"], "bin_quantity": data.quantity}


@router.post("/{item_id}/transfer")
async def transfer_stock(item_id: int, data: StockTransfer, request: Request, db=Depends(get_db)):
    """Move stock between bins. The item total is unchanged by design."""
    require_tech(request)
    if data.quantity <= 0:
        raise HTTPException(400, "Transfer quantity must be positive")
    if data.from_location_id == data.to_location_id:
        raise HTTPException(400, "Source and destination are the same location")
    for lid in (data.from_location_id, data.to_location_id):
        async with db.execute("SELECT id FROM inventory_locations WHERE id=?", (lid,)) as cur:
            if not await cur.fetchone():
                raise HTTPException(404, "Location not found")

    async with db.execute(
        "SELECT quantity FROM inventory_stock WHERE item_id=? AND location_id=?",
        (item_id, data.from_location_id)) as cur:
        src = await cur.fetchone()
    available = src["quantity"] if src else 0
    if available < data.quantity:
        raise HTTPException(400, f"Only {available} in the source location")

    await _apply_delta(db, item_id, data.from_location_id, -data.quantity)
    await _apply_delta(db, item_id, data.to_location_id, data.quantity)
    await db.execute(
        "INSERT INTO inventory_transactions "
        "(item_id,action,quantity,reference,performed_by,location_id,to_location_id) "
        "VALUES (?,'transfer',?,?,?,?,?)",
        (item_id, data.quantity, data.reference, data.performed_by,
         data.from_location_id, data.to_location_id),
    )
    await db.commit()
    bd = await _breakdown(db, {item_id})
    return {"ok": True, "stock": bd.get(item_id, [])}


@router.delete("/{item_id}")
async def delete_item(item_id: int, request: Request, db=Depends(get_db)):
    require_superadmin(request)
    await db.execute("DELETE FROM inventory_stock WHERE item_id=?", (item_id,))
    await db.execute("DELETE FROM inventory_transactions WHERE item_id=?", (item_id,))
    await db.execute("DELETE FROM inventory_items WHERE id=?", (item_id,))
    await db.commit()
    return {"ok": True}
