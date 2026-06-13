"""Rolling Stock — vehicle registry + FK5105 dispatch inspections."""
import json
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional
from backend.database import get_db
from backend.auth import require_admin, require_tech

router = APIRouter(prefix="/api/rolling-stock", tags=["rolling_stock"])


# ── Models ────────────────────────────────────────────────────────────────────

class VehicleCreate(BaseModel):
    make: str
    model: str
    year: Optional[str] = None
    tag_number: Optional[str] = None
    key_number: Optional[str] = None
    license_plate: Optional[str] = None
    vin: Optional[str] = None
    color: Optional[str] = None
    status: str = "available"
    notes: Optional[str] = None


class InspectionCreate(BaseModel):
    date_out: Optional[str] = None
    date_in: Optional[str] = None
    beginning_mileage: Optional[int] = None
    ending_mileage: Optional[int] = None
    operator_name: Optional[str] = None
    operator_phone: Optional[str] = None
    dispatcher_name: Optional[str] = None
    accident_card: bool = False
    results: dict = {}
    remarks: dict = {}
    notes: Optional[str] = None
    status: str = "dispatched"


class InspectionReturn(BaseModel):
    date_in: str
    ending_mileage: Optional[int] = None
    dispatcher_in_name: Optional[str] = None
    notes: Optional[str] = None


# ── Vehicles ──────────────────────────────────────────────────────────────────

@router.get("")
async def list_vehicles(db=Depends(get_db)):
    async with db.execute("""
        SELECT v.*,
          (SELECT COUNT(*) FROM vehicle_inspections WHERE vehicle_id=v.id) as inspection_count,
          (SELECT date_out FROM vehicle_inspections WHERE vehicle_id=v.id
           ORDER BY created_at DESC LIMIT 1) as last_dispatched
        FROM rolling_stock v ORDER BY v.make, v.model
    """) as cur:
        return [dict(r) for r in await cur.fetchall()]


@router.post("", status_code=201)
async def create_vehicle(request: Request, data: VehicleCreate, db=Depends(get_db)):
    require_tech(request)
    async with db.execute("""
        INSERT INTO rolling_stock (year,make,model,tag_number,key_number,license_plate,vin,color,status,notes)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (data.year, data.make, data.model, data.tag_number, data.key_number,
          data.license_plate, data.vin, data.color, data.status, data.notes)) as cur:
        vid = cur.lastrowid
    await db.commit()
    return {"id": vid}


@router.put("/{vid}")
async def update_vehicle(vid: int, request: Request, data: VehicleCreate, db=Depends(get_db)):
    require_tech(request)
    await db.execute("""
        UPDATE rolling_stock SET year=?,make=?,model=?,tag_number=?,key_number=?,
            license_plate=?,vin=?,color=?,status=?,notes=?,updated_at=datetime('now')
        WHERE id=?
    """, (data.year, data.make, data.model, data.tag_number, data.key_number,
          data.license_plate, data.vin, data.color, data.status, data.notes, vid))
    await db.commit()
    return {"ok": True}


@router.delete("/{vid}", status_code=204)
async def delete_vehicle(vid: int, request: Request, db=Depends(get_db)):
    require_admin(request)
    await db.execute("DELETE FROM rolling_stock WHERE id=?", (vid,))
    await db.commit()


# ── Inspections ───────────────────────────────────────────────────────────────

@router.get("/{vid}/inspections")
async def list_inspections(vid: int, db=Depends(get_db)):
    async with db.execute("""
        SELECT * FROM vehicle_inspections WHERE vehicle_id=? ORDER BY created_at DESC
    """, (vid,)) as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    for r in rows:
        r["results"] = json.loads(r["results"] or "{}")
        r["remarks"] = json.loads(r["remarks"] or "{}")
    return rows


@router.post("/{vid}/inspections", status_code=201)
async def create_inspection(vid: int, data: InspectionCreate, request: Request, db=Depends(get_db)):
    require_tech(request)
    async with db.execute("""
        INSERT INTO vehicle_inspections
            (vehicle_id,date_out,date_in,beginning_mileage,ending_mileage,
             operator_name,operator_phone,dispatcher_name,accident_card,
             results,remarks,notes,status)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (vid, data.date_out, data.date_in, data.beginning_mileage, data.ending_mileage,
          data.operator_name, data.operator_phone, data.dispatcher_name,
          1 if data.accident_card else 0,
          json.dumps(data.results), json.dumps(data.remarks),
          data.notes, data.status)) as cur:
        iid = cur.lastrowid
    # Auto-set vehicle status to dispatched
    await db.execute(
        "UPDATE rolling_stock SET status='dispatched', updated_at=datetime('now') WHERE id=?", (vid,)
    )
    await db.commit()
    return {"id": iid}


@router.patch("/inspections/{iid}/return")
async def return_inspection(iid: int, data: InspectionReturn, request: Request, db=Depends(get_db)):
    require_tech(request)
    async with db.execute("SELECT vehicle_id FROM vehicle_inspections WHERE id=?", (iid,)) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Inspection not found")
    vid = row["vehicle_id"]
    await db.execute("""
        UPDATE vehicle_inspections
        SET date_in=?, ending_mileage=?, dispatcher_name=COALESCE(?,dispatcher_name),
            notes=COALESCE(?,notes), status='returned', updated_at=datetime('now')
        WHERE id=?
    """, (data.date_in, data.ending_mileage, data.dispatcher_in_name, data.notes, iid))
    # Auto-set vehicle back to available
    await db.execute(
        "UPDATE rolling_stock SET status='available', updated_at=datetime('now') WHERE id=?", (vid,)
    )
    await db.commit()
    return {"ok": True}


@router.get("/inspections/{iid}")
async def get_inspection(iid: int, db=Depends(get_db)):
    async with db.execute("""
        SELECT i.*, v.year, v.make, v.model, v.tag_number, v.key_number,
               v.license_plate, v.color
        FROM vehicle_inspections i JOIN rolling_stock v ON v.id=i.vehicle_id
        WHERE i.id=?
    """, (iid,)) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Inspection not found")
    r = dict(row)
    r["results"] = json.loads(r["results"] or "{}")
    r["remarks"] = json.loads(r["remarks"] or "{}")
    return r


@router.get("/inspections/{iid}/print", response_class=HTMLResponse)
async def print_inspection(iid: int, db=Depends(get_db)):
    async with db.execute("""
        SELECT i.*, v.year, v.make, v.model, v.tag_number, v.key_number,
               v.license_plate, v.color
        FROM vehicle_inspections i JOIN rolling_stock v ON v.id=i.vehicle_id
        WHERE i.id=?
    """, (iid,)) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(404)
    r = dict(row)
    r["results"] = json.loads(r["results"] or "{}")
    r["remarks"] = json.loads(r["remarks"] or "{}")
    return _render_fk5105(r)


# ── FK5105 HTML renderer ──────────────────────────────────────────────────────

CHECKLIST = [
    ("At Dispatch", [
        ("dispatch_window",   "Window"),
        ("dispatch_license",  "License"),
        ("dispatch_cc_key",   "Credit Card & Key"),
    ]),
    ("Lights", [
        ("lights_headlights", "Headlights — High & Low Beam"),
        ("lights_brake",      "Brake Lights"),
        ("lights_tail",       "Tail Lights"),
        ("lights_backup",     "Backup Lights"),
        ("lights_plate",      "License Plate Lights"),
        ("lights_turn",       "Turn Signals"),
        ("lights_flasher",    "Emergency Flasher"),
        ("lights_dome",       "Dome Light & Panel Lights"),
    ]),
    ("Glass and Mirrors", [
        ("glass_windows",  "A. All Windows"),
        ("glass_mirrors",  "B. Side & Rearview Mirrors"),
        ("glass_wipers",   "C. Windshield Wipers"),
    ]),
    ("Tires and Spare", [
        ("tire_inflation", "A. Proper Inflation"),
        ("tire_cuts",      "B. Cuts, Gouges, or Bulges"),
        ("tire_tread",     "C. Tread Wear Left"),
        ("tire_lugnuts",   "D. Lug Nuts Present & Tight"),
        ("tire_jack",      "E. Jack & Lug Wrench"),
        ("tire_wheels",    "F. Wheels / Wheel Covers"),
    ]),
    ("Brakes", [
        ("brake_parking",   "A. Emergency / Parking Brake"),
        ("brake_operation", "B. Braking Operation (No Pull / No Metal-on-Metal Noise)"),
    ]),
    ("Steering", [
        ("steering_op", "Steering Operation with Engine Running"),
    ]),
    ("Under the Hood", [
        ("hood_ps_fluid",   "A. Power Steering Fluid"),
        ("hood_brake_fluid","B. Brake Fluid"),
        ("hood_trans_fluid","C. Transmission Fluid"),
        ("hood_engine_oil", "D. Engine Oil"),
        ("hood_belts",      "E. Belts For Wear & Tightness"),
        ("hood_leaks",      "F. Fluid / Oil Leaks"),
        ("hood_washer",     "G. Windshield Washer Fluid"),
    ]),
    ("Battery", [
        ("batt_cables",  "A. Ensure Cables are Tight"),
        ("batt_visual",  "B. Visually Check Battery"),
    ]),
    ("Exterior", [
        ("ext_chrome",    "A. Chrome / Body Molding & Grill"),
        ("ext_bumpers",   "B. Front & Rear Bumper"),
        ("ext_doors",     "C. Doors"),
        ("ext_fenders",   "D. Fenders"),
        ("ext_hood",      "E. Hood / Trunk Lid"),
        ("ext_roof",      "F. Roof / Vehicle Sides"),
        ("ext_exhaust",   "G. Muffler / Exhaust System"),
        ("ext_antenna",   "H. Antenna"),
        ("ext_clean",     "I. Exterior Cleanliness"),
    ]),
    ("Interior", [
        ("int_carpet",    "A. Carpet"),
        ("int_doors",     "B. Door Panels & Hardware"),
        ("int_headliner", "C. Headliner"),
        ("int_upholstery","D. Upholstery"),
        ("int_hvac",      "E. Heater / Air Conditioning"),
        ("int_radio",     "F. Radio"),
        ("int_horn",      "G. Horn"),
        ("int_instruments","H. Instruments & Gages"),
        ("int_form1627",  "I. GSA Form 1627 (Accident Envelope in Glove Box)"),
        ("int_clean",     "J. Interior Cleanliness"),
    ]),
]


def _sat(results, key):
    v = results.get(key, "")
    if v == "SAT":   return '<td class="chk sat">SAT</td>'
    if v == "UNSAT": return '<td class="chk unsat">UNSAT</td>'
    return '<td class="chk"></td>'


def _render_fk5105(r: dict) -> str:
    results = r["results"]
    remarks = r["remarks"]
    vehicle = f"{r.get('year','')} {r.get('make','')} {r.get('model','')}".strip()
    faults = [k for k, v in results.items() if v == "UNSAT"]

    rows_html = ""
    for section, items in CHECKLIST:
        rows_html += f'<tr><td class="sect" colspan="4">{section}</td></tr>'
        for key, label in items:
            remark = remarks.get(key, "")
            rows_html += f"""<tr>
              <td class="item">{label}</td>
              {_sat(results, key)}
              {_sat(results, key)}
              <td class="rem">{remark}</td>
            </tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>FK Form 5105 — {vehicle}</title>
<style>
  *, *::before, *::after {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ font-family:Arial,Helvetica,sans-serif; font-size:9pt; color:#000;
          padding:0.4in; max-width:8.5in; margin:0 auto; }}
  h1 {{ font-size:11pt; font-weight:bold; text-align:center; margin-bottom:2px; }}
  .sub {{ font-size:8pt; text-align:center; margin-bottom:8px; }}
  .header-grid {{ display:grid; grid-template-columns:1fr 1fr 1fr; gap:4px; margin-bottom:6px; border:1px solid #000; padding:4px; }}
  .hf {{ display:flex; flex-direction:column; }}
  .hf label {{ font-size:7pt; font-weight:bold; color:#444; }}
  .hf span {{ font-size:9pt; border-bottom:1px solid #999; min-height:16px; padding-bottom:1px; }}
  table {{ width:100%; border-collapse:collapse; margin-bottom:6px; }}
  th {{ font-size:8pt; font-weight:bold; background:#1e3a5f; color:#fff; padding:3px 4px; text-align:center; }}
  td {{ border:1px solid #aaa; padding:2px 4px; font-size:8pt; vertical-align:middle; }}
  td.sect {{ background:#dce8f5; font-weight:bold; font-size:8pt; padding:3px 4px; }}
  td.chk {{ text-align:center; width:52px; font-weight:bold; }}
  td.sat {{ color:#166534; }}
  td.unsat {{ color:#991b1b; background:#fef2f2; }}
  td.rem {{ font-size:7.5pt; color:#555; }}
  .sig-grid {{ display:grid; grid-template-columns:1fr 1fr 1fr; gap:8px; margin-top:10px; }}
  .sig-block {{ border-top:1px solid #000; padding-top:2px; font-size:7.5pt; color:#555; }}
  .notice {{ font-size:7pt; border:1px solid #aaa; padding:4px 6px; margin:6px 0; background:#fffbeb; }}
  .fault-banner {{ background:#fef2f2; border:1px solid #dc2626; color:#991b1b; font-weight:bold;
                   font-size:9pt; padding:4px 8px; margin-bottom:6px; border-radius:3px; }}
  @media print {{
    body {{ padding:0.25in; }}
    button {{ display:none !important; }}
    .no-print {{ display:none !important; }}
  }}
</style>
</head>
<body>

<button class="no-print" onclick="window.print()"
  style="float:right;padding:6px 16px;background:#1e3a5f;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:9pt;margin-bottom:8px;">
  🖨 Print / Save PDF
</button>

<h1>GSA Vehicle Sign-In / Out Inspection Checklist</h1>
<div class="sub">FK FORM 5105, NOV 2011 &nbsp;·&nbsp; Inspection #{r['id']}</div>

{'<div class="fault-banner">⚠ ' + str(len(faults)) + ' UNSAT item' + ('s' if len(faults)!=1 else '') + ' — vehicle requires attention before dispatch</div>' if faults else ''}

<div class="header-grid">
  <div class="hf"><label>Year / Make / Model</label><span>{vehicle}</span></div>
  <div class="hf"><label>Tag #</label><span>{r.get('tag_number') or ''}</span></div>
  <div class="hf"><label>Key #</label><span>{r.get('key_number') or ''}</span></div>
  <div class="hf"><label>License Plate</label><span>{r.get('license_plate') or ''}</span></div>
  <div class="hf"><label>Date Out</label><span>{r.get('date_out') or ''}</span></div>
  <div class="hf"><label>Date In</label><span>{r.get('date_in') or ''}</span></div>
  <div class="hf"><label>Beginning Mileage</label><span>{r.get('beginning_mileage') or ''}</span></div>
  <div class="hf"><label>Ending Mileage</label><span>{r.get('ending_mileage') or ''}</span></div>
  <div class="hf"><label>Accident Avoidance Card</label><span>{'YES' if r.get('accident_card') else 'NO'}</span></div>
</div>

<table>
  <thead>
    <tr>
      <th style="text-align:left;">Item</th>
      <th>Operator SAT/UNSAT</th>
      <th>Dispatcher SAT/UNSAT</th>
      <th style="text-align:left;">Remarks</th>
    </tr>
  </thead>
  <tbody>
    {rows_html}
  </tbody>
</table>

<div class="notice">
  Use only <strong>Unleaded Regular</strong> gasoline. Use only <strong>10W30</strong> standard motor oil.
  Higher-grade fuels or synthetic oil is UNAUTHORIZED. Only exterior car washes are AUTHORIZED.
  Vehicle must be returned with a <strong>full tank of fuel</strong> and clean inside and out.
</div>

<div class="sig-grid">
  <div class="sig-block">Operator Name (Print): {r.get('operator_name') or ''}<br><br>Signature: ___________________________<br><br>Unit Phone: {r.get('operator_phone') or ''}</div>
  <div class="sig-block">Dispatcher Out (Print): {r.get('dispatcher_name') or ''}<br><br>Signature: ___________________________<br><br>Date Out: {r.get('date_out') or ''}</div>
  <div class="sig-block">Dispatcher In (Print): ___________________________<br><br>Signature: ___________________________<br><br>Date In: {r.get('date_in') or ''}</div>
</div>

</body>
</html>"""
