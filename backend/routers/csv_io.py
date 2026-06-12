"""CSV import / export endpoints for all major entities."""
import csv
import io
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from backend.database import get_db
from backend import audit

router = APIRouter(prefix="/api/csv", tags=["csv"])

# ── helpers ────────────────────────────────────────────────────────────────────

def _csv_response(rows: list[dict], filename: str) -> StreamingResponse:
    if not rows:
        raise HTTPException(404, "No data to export")
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _parse_upload(content: bytes) -> tuple[list[str], list[dict]]:
    text = content.decode("utf-8-sig")  # strip BOM from Excel exports
    reader = csv.DictReader(io.StringIO(text))
    rows = [dict(r) for r in reader]
    headers = reader.fieldnames or []
    return list(headers), rows


def _str(v): return (v or "").strip() or None
def _int(v):
    try: return int(v)
    except: return 0
def _float(v):
    try: return float(v)
    except: return None


# ── Equipment export ───────────────────────────────────────────────────────────

@router.get("/equipment/export")
async def export_equipment(db=Depends(get_db)):
    async with db.execute("""
        SELECT name, category, serial_num, model, manufacturer,
               location, assigned_to, status, notes
        FROM equipment ORDER BY name
    """) as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    return _csv_response(rows, "equipment.csv")


# ── Equipment import ───────────────────────────────────────────────────────────
# Required columns: name, category
# Optional: serial_num, model, manufacturer, location, assigned_to, status, notes
# Upserts on serial_num when present, otherwise inserts

@router.post("/equipment/import")
async def import_equipment(file: UploadFile = File(...), db=Depends(get_db)):
    _, rows = _parse_upload(await file.read())
    created = updated = skipped = 0
    errors = []

    for i, row in enumerate(rows, start=2):
        name = _str(row.get("name"))
        category = _str(row.get("category"))
        if not name or not category:
            errors.append(f"Row {i}: missing required 'name' or 'category'")
            skipped += 1
            continue

        serial = _str(row.get("serial_num"))
        vals = (
            name, category, serial,
            _str(row.get("model")), _str(row.get("manufacturer")),
            _str(row.get("location")), _str(row.get("assigned_to")),
            _str(row.get("status")) or "active",
            _str(row.get("notes")),
        )

        if serial:
            async with db.execute(
                "SELECT id FROM equipment WHERE serial_num=?", (serial,)
            ) as cur:
                existing = await cur.fetchone()
        else:
            existing = None

        if existing:
            await db.execute("""
                UPDATE equipment SET name=?,category=?,serial_num=?,model=?,manufacturer=?,
                    location=?,assigned_to=?,status=?,notes=?,updated_at=datetime('now')
                WHERE id=?
            """, (*vals, existing["id"]))
            await audit.log(db, "equipment", existing["id"], "updated",
                            equipment_id=existing["id"], detail={"name": name, "source": "csv_import"})
            updated += 1
        else:
            async with db.execute("""
                INSERT INTO equipment (name,category,serial_num,model,manufacturer,
                    location,assigned_to,status,notes)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, vals) as cur:
                eq_id = cur.lastrowid
            await audit.log(db, "equipment", eq_id, "created",
                            equipment_id=eq_id, detail={"name": name, "source": "csv_import"})
            created += 1

    await db.commit()
    return {"created": created, "updated": updated, "skipped": skipped, "errors": errors}


# ── Equipment template (blank CSV) ─────────────────────────────────────────────

@router.get("/equipment/template")
async def equipment_template():
    headers = ["name","category","serial_num","model","manufacturer",
               "location","assigned_to","status","notes"]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    writer.writerow(["Example Item","Test Equipment","SN-12345","Model X",
                     "Acme Corp","Survey Training Room","ALPHA","active",""])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="equipment_template.csv"'},
    )


# ── Inventory export ───────────────────────────────────────────────────────────

@router.get("/inventory/export")
async def export_inventory(db=Depends(get_db)):
    async with db.execute("""
        SELECT name, part_number, category, location, unit, quantity,
               min_stock, unit_cost, supplier, notes
        FROM inventory_items ORDER BY name
    """) as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    return _csv_response(rows, "inventory.csv")


# ── Inventory import ───────────────────────────────────────────────────────────
# Required: name
# Optional: part_number, category, location, unit, quantity, min_stock, unit_cost, supplier, notes
# Upserts on part_number when present

@router.post("/inventory/import")
async def import_inventory(file: UploadFile = File(...), db=Depends(get_db)):
    _, rows = _parse_upload(await file.read())
    created = updated = skipped = 0
    errors = []

    for i, row in enumerate(rows, start=2):
        name = _str(row.get("name"))
        if not name:
            errors.append(f"Row {i}: missing required 'name'")
            skipped += 1
            continue

        part_num = _str(row.get("part_number"))
        vals = (
            name, part_num,
            _str(row.get("category")), _str(row.get("location")),
            _str(row.get("unit")) or "ea",
            _int(row.get("quantity")), _int(row.get("min_stock")),
            _float(row.get("unit_cost")),
            _str(row.get("supplier")), _str(row.get("notes")),
        )

        if part_num:
            async with db.execute(
                "SELECT id FROM inventory_items WHERE part_number=?", (part_num,)
            ) as cur:
                existing = await cur.fetchone()
        else:
            existing = None

        if existing:
            await db.execute("""
                UPDATE inventory_items SET name=?,part_number=?,category=?,location=?,
                    unit=?,min_stock=?,unit_cost=?,supplier=?,notes=?,updated_at=datetime('now')
                WHERE id=?
            """, (vals[0], vals[1], vals[2], vals[3], vals[4], vals[6], vals[7], vals[8], vals[9],
                  existing["id"]))
            updated += 1
        else:
            await db.execute("""
                INSERT INTO inventory_items
                    (name,part_number,category,location,unit,quantity,min_stock,unit_cost,supplier,notes)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, vals)
            created += 1

    await db.commit()
    return {"created": created, "updated": updated, "skipped": skipped, "errors": errors}


# ── Inventory template ─────────────────────────────────────────────────────────

@router.get("/inventory/template")
async def inventory_template():
    headers = ["name","part_number","category","location","unit",
               "quantity","min_stock","unit_cost","supplier","notes"]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    writer.writerow(["Battery 9V","NSN-6135-01-XXX","Batteries","Survey Training Room",
                     "ea","24","6","1.50","GCSS-Army",""])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="inventory_template.csv"'},
    )


# ── Maintenance export ─────────────────────────────────────────────────────────

@router.get("/maintenance/export")
async def export_maintenance(db=Depends(get_db)):
    async with db.execute("""
        SELECT e.name as equipment_name, e.serial_num,
               t.title, t.task_type, t.status, t.next_due, t.last_done,
               t.interval_days, t.assigned_to, t.notes
        FROM maintenance_tasks t
        JOIN equipment e ON e.id = t.equipment_id
        ORDER BY e.name, t.next_due
    """) as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    return _csv_response(rows, "maintenance.csv")


# ── Calibration export ─────────────────────────────────────────────────────────

@router.get("/calibration/export")
async def export_calibration(db=Depends(get_db)):
    async with db.execute("""
        SELECT e.name as equipment_name, e.serial_num,
               c.calibrated_at, c.next_due, c.calibrated_by,
               c.certificate_num, c.result, c.notes
        FROM calibration_records c
        JOIN equipment e ON e.id = c.equipment_id
        ORDER BY e.name, c.calibrated_at DESC
    """) as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    return _csv_response(rows, "calibration.csv")


# ── PMCS items export ──────────────────────────────────────────────────────────

@router.get("/pmcs/{tmpl_id}/items/export")
async def export_pmcs_items(tmpl_id: int, db=Depends(get_db)):
    async with db.execute(
        "SELECT title FROM pmcs_templates WHERE id=?", (tmpl_id,)
    ) as cur:
        tmpl = await cur.fetchone()
    if not tmpl:
        raise HTTPException(404, "Template not found")
    async with db.execute("""
        SELECT item_no, interval, check_item, procedure, not_ready_if, order_index
        FROM pmcs_items WHERE template_id=? ORDER BY order_index, id
    """, (tmpl_id,)) as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    safe_title = tmpl["title"].replace(" ", "_")[:30]
    return _csv_response(rows or [{"item_no":"","interval":"B","check_item":"",
                                   "procedure":"","not_ready_if":"","order_index":""}],
                         f"pmcs_{safe_title}.csv")


# ── PMCS items import ──────────────────────────────────────────────────────────
# Columns: item_no, interval (B/D/A/W/M), check_item, procedure, not_ready_if, order_index
# Appends to existing items (does not delete)

@router.post("/pmcs/{tmpl_id}/items/import")
async def import_pmcs_items(tmpl_id: int, file: UploadFile = File(...), db=Depends(get_db)):
    async with db.execute(
        "SELECT id FROM pmcs_templates WHERE id=?", (tmpl_id,)
    ) as cur:
        if not await cur.fetchone():
            raise HTTPException(404, "Template not found")

    async with db.execute(
        "SELECT MAX(order_index) as mx FROM pmcs_items WHERE template_id=?", (tmpl_id,)
    ) as cur:
        row = await cur.fetchone()
    next_order = (row["mx"] or 0) + 1

    _, rows = _parse_upload(await file.read())
    created = skipped = 0
    errors = []
    VALID_INTERVALS = {"B", "D", "A", "W", "M"}

    for i, row in enumerate(rows, start=2):
        check_item = _str(row.get("check_item"))
        if not check_item:
            errors.append(f"Row {i}: missing 'check_item'")
            skipped += 1
            continue
        interval = (_str(row.get("interval")) or "B").upper()
        if interval not in VALID_INTERVALS:
            interval = "B"
        order = _int(row.get("order_index")) or next_order
        await db.execute("""
            INSERT INTO pmcs_items (template_id, item_no, interval, check_item,
                                   procedure, not_ready_if, order_index)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (tmpl_id, _str(row.get("item_no")), interval, check_item,
              _str(row.get("procedure")), _str(row.get("not_ready_if")), order))
        next_order += 1
        created += 1

    await db.commit()
    return {"created": created, "skipped": skipped, "errors": errors}


# ── PMCS items template ────────────────────────────────────────────────────────

@router.get("/pmcs/items/template")
async def pmcs_items_template():
    headers = ["item_no","interval","check_item","procedure","not_ready_if","order_index"]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    writer.writerow(["1","B","Check oil level","Remove dipstick and verify between MIN/MAX","Oil below MIN mark","1"])
    writer.writerow(["2","B","Check tire pressure","Verify 35 PSI all tires","Any tire below 30 PSI","2"])
    writer.writerow(["3","D","Check fuel gauge","Verify adequate fuel for mission","Fuel below 1/4 tank","3"])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="pmcs_items_template.csv"'},
    )
