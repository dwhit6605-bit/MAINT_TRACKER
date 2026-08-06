"""Power & Air — generator / breathing-air compressor registry, run logs and dispatches.

Two lifecycles share one log table:
  * ``run``      — fixed or vehicle-mounted units. Start/stop, hour meter, PMCS.
  * ``dispatch`` — portable units signed out to an operator, then signed back in.

Service intervals are driven by accumulated hours, not calendar days, so the
asset's hour meter is advanced from each closed log's ending reading.
"""
import json
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional
from backend.database import get_db
from backend.auth import require_tech, require_superadmin

router = APIRouter(prefix="/api/power-assets", tags=["power_assets"])


# ── Models ────────────────────────────────────────────────────────────────────

class AssetCreate(BaseModel):
    asset_type: str = "generator"
    name: str
    make: Optional[str] = None
    model: Optional[str] = None
    serial_num: Optional[str] = None
    rating: Optional[str] = None
    fuel_type: Optional[str] = None
    location: Optional[str] = None
    portable: bool = False
    hour_meter: float = 0
    service_interval_hours: Optional[float] = None
    last_service_hours: float = 0
    status: str = "available"
    notes: Optional[str] = None


class LogCreate(BaseModel):
    log_type: str = "run"
    checklist_type: str = "gen_diesel"
    date_out: Optional[str] = None
    hours_start: Optional[float] = None
    operator_name: Optional[str] = None
    operator_phone: Optional[str] = None
    dispatcher_name: Optional[str] = None
    destination: Optional[str] = None
    results: dict = {}
    remarks: dict = {}
    notes: Optional[str] = None


class LogClose(BaseModel):
    date_in: str
    hours_end: Optional[float] = None
    dispatcher_in_name: Optional[str] = None
    notes: Optional[str] = None


class ServiceReset(BaseModel):
    last_service_hours: Optional[float] = None
    notes: Optional[str] = None


# ── Assets ────────────────────────────────────────────────────────────────────

@router.get("")
async def list_assets(db=Depends(get_db)):
    async with db.execute("""
        SELECT a.*,
          (SELECT COUNT(*) FROM power_asset_logs WHERE asset_id=a.id) as log_count,
          (SELECT date_out FROM power_asset_logs WHERE asset_id=a.id
           ORDER BY created_at DESC LIMIT 1) as last_run,
          (SELECT id FROM power_asset_logs WHERE asset_id=a.id AND status='open'
           ORDER BY created_at DESC LIMIT 1) as open_log_id
        FROM power_assets a ORDER BY a.asset_type, a.name
    """) as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    for r in rows:
        interval = r.get("service_interval_hours")
        r["hours_until_service"] = (
            round((r["last_service_hours"] + interval) - r["hour_meter"], 1)
            if interval else None
        )
    return rows


@router.post("", status_code=201)
async def create_asset(request: Request, data: AssetCreate, db=Depends(get_db)):
    require_tech(request)
    async with db.execute("""
        INSERT INTO power_assets
            (asset_type,name,make,model,serial_num,rating,fuel_type,location,
             portable,hour_meter,service_interval_hours,last_service_hours,status,notes)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (data.asset_type, data.name, data.make, data.model, data.serial_num,
          data.rating, data.fuel_type, data.location, 1 if data.portable else 0,
          data.hour_meter, data.service_interval_hours, data.last_service_hours,
          data.status, data.notes)) as cur:
        aid = cur.lastrowid
    await db.commit()
    return {"id": aid}


@router.put("/{aid}")
async def update_asset(aid: int, request: Request, data: AssetCreate, db=Depends(get_db)):
    require_tech(request)
    await db.execute("""
        UPDATE power_assets SET asset_type=?,name=?,make=?,model=?,serial_num=?,
            rating=?,fuel_type=?,location=?,portable=?,hour_meter=?,
            service_interval_hours=?,last_service_hours=?,status=?,notes=?,
            updated_at=datetime('now')
        WHERE id=?
    """, (data.asset_type, data.name, data.make, data.model, data.serial_num,
          data.rating, data.fuel_type, data.location, 1 if data.portable else 0,
          data.hour_meter, data.service_interval_hours, data.last_service_hours,
          data.status, data.notes, aid))
    await db.commit()
    return {"ok": True}


@router.delete("/{aid}")
async def delete_asset(aid: int, request: Request, db=Depends(get_db)):
    require_superadmin(request)
    async with db.execute("SELECT id FROM power_assets WHERE id=?", (aid,)) as cur:
        if not await cur.fetchone():
            raise HTTPException(404, "Asset not found")
    # SQLite enforces ON DELETE CASCADE only with the foreign_keys pragma on
    await db.execute("DELETE FROM power_asset_logs WHERE asset_id=?", (aid,))
    await db.execute("DELETE FROM power_assets WHERE id=?", (aid,))
    await db.commit()
    return {"ok": True}


@router.post("/{aid}/service", status_code=200)
async def reset_service(aid: int, request: Request, data: ServiceReset, db=Depends(get_db)):
    """Mark scheduled service done — rebase the interval on the current hour meter."""
    require_tech(request)
    async with db.execute("SELECT hour_meter FROM power_assets WHERE id=?", (aid,)) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Asset not found")
    at = data.last_service_hours if data.last_service_hours is not None else row["hour_meter"]
    await db.execute(
        "UPDATE power_assets SET last_service_hours=?, updated_at=datetime('now') WHERE id=?",
        (at, aid),
    )
    await db.commit()
    return {"ok": True, "last_service_hours": at}


@router.post("/import-from-equipment", status_code=201)
async def import_from_equipment(request: Request, db=Depends(get_db)):
    """One-time seed: copy 'Generators / APU' equipment records into the registry.

    Skips anything already imported so it is safe to run more than once.
    """
    require_tech(request)
    async with db.execute("""
        SELECT id, name, manufacturer, model, serial_num, location, notes
        FROM equipment WHERE category='Generators / APU'
    """) as cur:
        src = [dict(r) for r in await cur.fetchall()]

    async with db.execute(
        "SELECT equipment_id FROM power_assets WHERE equipment_id IS NOT NULL"
    ) as cur:
        already = {r["equipment_id"] for r in await cur.fetchall()}

    imported = []
    for e in src:
        if e["id"] in already:
            continue
        name = e["name"] or ""
        portable = 1 if "honda" in (name + " " + (e["manufacturer"] or "")).lower() else 0
        await db.execute("""
            INSERT INTO power_assets
                (asset_type,name,make,model,serial_num,location,portable,notes,equipment_id)
            VALUES ('generator',?,?,?,?,?,?,?,?)
        """, (name, e["manufacturer"], e["model"], e["serial_num"],
              e["location"], portable, e["notes"], e["id"]))
        imported.append(name)
    await db.commit()
    return {"imported": len(imported), "skipped": len(src) - len(imported), "names": imported}


# ── Logs ──────────────────────────────────────────────────────────────────────

@router.get("/{aid}/logs")
async def list_logs(aid: int, db=Depends(get_db)):
    async with db.execute(
        "SELECT * FROM power_asset_logs WHERE asset_id=? ORDER BY created_at DESC", (aid,)
    ) as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    for r in rows:
        r["results"] = json.loads(r["results"] or "{}")
        r["remarks"] = json.loads(r["remarks"] or "{}")
    return rows


@router.post("/{aid}/logs", status_code=201)
async def create_log(aid: int, data: LogCreate, request: Request, db=Depends(get_db)):
    require_tech(request)
    async with db.execute("SELECT hour_meter FROM power_assets WHERE id=?", (aid,)) as cur:
        asset = await cur.fetchone()
    if not asset:
        raise HTTPException(404, "Asset not found")

    async with db.execute("""
        INSERT INTO power_asset_logs
            (asset_id,log_type,checklist_type,date_out,hours_start,operator_name,
             operator_phone,dispatcher_name,destination,results,remarks,notes,status)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'open')
    """, (aid, data.log_type, data.checklist_type, data.date_out, data.hours_start,
          data.operator_name, data.operator_phone, data.dispatcher_name,
          data.destination, json.dumps(data.results), json.dumps(data.remarks),
          data.notes)) as cur:
        lid = cur.lastrowid

    # A start reading ahead of the stored meter is the more current truth
    new_status = "dispatched" if data.log_type == "dispatch" else "running"
    if data.hours_start is not None and data.hours_start > asset["hour_meter"]:
        await db.execute(
            "UPDATE power_assets SET status=?, hour_meter=?, updated_at=datetime('now') WHERE id=?",
            (new_status, data.hours_start, aid),
        )
    else:
        await db.execute(
            "UPDATE power_assets SET status=?, updated_at=datetime('now') WHERE id=?",
            (new_status, aid),
        )
    await db.commit()
    return {"id": lid}


@router.patch("/logs/{lid}/close")
async def close_log(lid: int, data: LogClose, request: Request, db=Depends(get_db)):
    require_tech(request)
    async with db.execute(
        "SELECT asset_id, hours_start, status FROM power_asset_logs WHERE id=?", (lid,)
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Log not found")
    if row["status"] == "closed":
        raise HTTPException(409, "Log is already closed")

    aid = row["asset_id"]
    if (data.hours_end is not None and row["hours_start"] is not None
            and data.hours_end < row["hours_start"]):
        raise HTTPException(
            400,
            f"Ending hours ({data.hours_end}) cannot be less than "
            f"starting hours ({row['hours_start']})",
        )

    await db.execute("""
        UPDATE power_asset_logs
        SET date_in=?, hours_end=?, dispatcher_name=COALESCE(?,dispatcher_name),
            notes=COALESCE(?,notes), status='closed', updated_at=datetime('now')
        WHERE id=?
    """, (data.date_in, data.hours_end, data.dispatcher_in_name, data.notes, lid))

    if data.hours_end is not None:
        await db.execute("""
            UPDATE power_assets SET status='available', hour_meter=MAX(hour_meter,?),
                updated_at=datetime('now') WHERE id=?
        """, (data.hours_end, aid))
    else:
        await db.execute(
            "UPDATE power_assets SET status='available', updated_at=datetime('now') WHERE id=?",
            (aid,),
        )
    await db.commit()
    return {"ok": True}


@router.get("/logs/{lid}")
async def get_log(lid: int, db=Depends(get_db)):
    async with db.execute("""
        SELECT l.*, a.name, a.make, a.model, a.serial_num, a.rating,
               a.fuel_type, a.location, a.asset_type
        FROM power_asset_logs l JOIN power_assets a ON a.id=l.asset_id
        WHERE l.id=?
    """, (lid,)) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(404, "Log not found")
    r = dict(row)
    r["results"] = json.loads(r["results"] or "{}")
    r["remarks"] = json.loads(r["remarks"] or "{}")
    return r


@router.get("/logs/{lid}/print", response_class=HTMLResponse)
async def print_log(lid: int, db=Depends(get_db)):
    async with db.execute("""
        SELECT l.*, a.name, a.make, a.model, a.serial_num, a.rating,
               a.fuel_type, a.location, a.asset_type
        FROM power_asset_logs l JOIN power_assets a ON a.id=l.asset_id
        WHERE l.id=?
    """, (lid,)) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(404)
    r = dict(row)
    r["results"] = json.loads(r["results"] or "{}")
    r["remarks"] = json.loads(r["remarks"] or "{}")
    return _render_log(r)


# ── Checklists (mirrored in the SPA so the form and the printout agree) ────────

CHECKLISTS = {
    "gen_diesel": ("Generator / APU PMCS — Before, During, After Operation", [
        ("Before Operation", [
            ("gd_damage",     "Inspect unit for damage and loose hardware"),
            ("gd_oil",        "Engine oil level"),
            ("gd_coolant",    "Coolant level (NOT WHEN HOT!)"),
            ("gd_fuel",       "Fuel level; drain water separator"),
            ("gd_airfilter",  "Air filter / restriction indicator"),
            ("gd_belts",      "Belts for wear and tightness"),
            ("gd_battery",    "Battery terminals clean and tight"),
            ("gd_leaks",      "Check for fuel, oil, or coolant leaks"),
            ("gd_exhaust",    "Exhaust system secure and unobstructed"),
            ("gd_ground",     "Ground rod / grounding cable connected"),
            ("gd_breakers",   "Output breakers OFF before start"),
            ("gd_hours_pre",  "Record hour meter (Before) — enter reading in Remark"),
        ]),
        ("During Operation", [
            ("gd_start",      "Start engine; no abnormal noise or vibration"),
            ("gd_oil_press",  "Oil pressure within range"),
            ("gd_temp",       "Coolant temperature within range"),
            ("gd_voltage",    "Output voltage within range"),
            ("gd_freq",       "Output frequency (Hz) within range"),
            ("gd_load",       "Applied load within nameplate rating"),
            ("gd_smoke",      "Exhaust smoke normal"),
            ("gd_leaks_load", "No leaks under load"),
        ]),
        ("After Operation", [
            ("gd_cooldown",   "Cool-down at no load before shutdown"),
            ("gd_shutdown",   "Shut down; secure output breakers"),
            ("gd_leaks_post", "Inspect for leaks"),
            ("gd_refuel",     "Refuel"),
            ("gd_clean",      "Clean unit and secure"),
            ("gd_hours_post", "Record hour meter (After) — enter reading in Remark"),
        ]),
    ]),
    "gen_portable": ("Portable Generator PMCS (Honda EU/EM Series)", [
        ("Before Operation", [
            ("gp_damage",     "Inspect exterior for damage"),
            ("gp_oil",        "Engine oil level"),
            ("gp_fuel",       "Fuel level; fuel valve position"),
            ("gp_airfilter",  "Air filter clean"),
            ("gp_exhaust",    "Spark arrestor / exhaust clear"),
            ("gp_cords",      "Cords and receptacles undamaged; GFCI test"),
            ("gp_siting",     "Level surface, ventilated, clear of occupied spaces"),
            ("gp_hours_pre",  "Record hour meter (Before) — enter reading in Remark"),
        ]),
        ("During Operation", [
            ("gp_start",      "Choke and start procedure"),
            ("gp_eco",        "Eco-throttle set as required"),
            ("gp_voltage",    "Output voltage at receptacle"),
            ("gp_load",       "Applied load within rating"),
            ("gp_noise",      "No abnormal noise or vibration"),
            ("gp_co",         "CO hazard — placement and exhaust direction verified"),
        ]),
        ("After Operation", [
            ("gp_unload",     "Remove load; cool down; shut off"),
            ("gp_valve",      "Fuel valve OFF"),
            ("gp_leaks",      "Inspect for leaks"),
            ("gp_refuel",     "Refuel"),
            ("gp_store",      "Store secured"),
            ("gp_hours_post", "Record hour meter (After) — enter reading in Remark"),
        ]),
    ]),
    "compressor": ("Breathing Air Compressor — Operation & Fill Station Checklist", [
        ("Before Operation", [
            ("bc_damage",     "Inspect unit and fill station for damage"),
            ("bc_oil",        "Compressor oil level"),
            ("bc_belt",       "Drive belt condition and tension"),
            ("bc_intake",     "Intake clear of exhaust and contaminants"),
            ("bc_separator",  "Moisture separator drained"),
            ("bc_cartridge",  "Purification cartridge hours remaining within limit"),
            ("bc_comonitor",  "CO monitor powered and within calibration date"),
            ("bc_hoses",      "Fill hoses and connections inspected"),
            ("bc_shield",     "Fill station shield / containment intact"),
            ("bc_hours_pre",  "Record hour meter (Before) — enter reading in Remark"),
        ]),
        ("During Operation", [
            ("bc_start",      "Start; monitor pressure build"),
            ("bc_autodrain",  "Auto-drain cycling correctly"),
            ("bc_pressure",   "Final pressure setting correct"),
            ("bc_co_read",    "CO monitor reading within limit — enter ppm in Remark"),
            ("bc_noise",      "No abnormal noise, vibration, or overheating"),
            ("bc_hydro",      "Cylinder hydro date and visual inspection current before fill"),
            ("bc_fillrate",   "Fill rate controlled; cylinders not overheating"),
        ]),
        ("After Operation", [
            ("bc_depress",    "Depressurize and bleed lines"),
            ("bc_drain",      "Drain moisture separators"),
            ("bc_cart_log",   "Log purification cartridge hours used"),
            ("bc_leaks",      "Inspect for leaks"),
            ("bc_hours_post", "Record hour meter (After) — enter reading in Remark"),
        ]),
        ("Air Quality (Quarterly)", [
            ("bc_sample",     "Air sample taken and submitted"),
            ("bc_coa",        "Certificate of analysis on file (O2, CO, CO2, moisture, oil)"),
        ]),
    ]),
}


# ── Printable log renderer ────────────────────────────────────────────────────

def _sat(results, key):
    v = results.get(key, "")
    if v == "SAT":
        return '<td class="chk sat">SAT</td>'
    if v == "UNSAT":
        return '<td class="chk unsat">UNSAT</td>'
    return '<td class="chk"></td>'


def _esc(v) -> str:
    s = "" if v is None else str(v)
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _render_log(r: dict) -> str:
    results, remarks = r["results"], r["remarks"]
    title, sections = CHECKLISTS.get(r.get("checklist_type"), CHECKLISTS["gen_diesel"])
    faults = [k for k, v in results.items() if v == "UNSAT"]

    unit = " ".join(x for x in [r.get("make"), r.get("model")] if x) or r.get("name") or ""
    hs, he = r.get("hours_start"), r.get("hours_end")
    elapsed = f"{round(he - hs, 1)}" if hs is not None and he is not None else ""
    is_dispatch = r.get("log_type") == "dispatch"

    rows_html = ""
    for section, items in sections:
        rows_html += f'<tr><td class="sect" colspan="3">{_esc(section)}</td></tr>'
        for key, label in items:
            rows_html += (
                f'<tr><td class="item">{_esc(label)}</td>{_sat(results, key)}'
                f'<td class="rem">{_esc(remarks.get(key, ""))}</td></tr>'
            )

    banner = ""
    if faults:
        n = len(faults)
        banner = (f'<div class="fault-banner">&#9888; {n} UNSAT item{"s" if n != 1 else ""} '
                  f'&mdash; unit requires attention before return to service</div>')

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{_esc(title)} &mdash; {_esc(r.get('name'))}</title>
<style>
  /* This is a paper form — never let the viewer's dark theme invert it */
  html {{ color-scheme: only light; background:#fff; }}
  *, *::before, *::after {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ font-family:Arial,Helvetica,sans-serif; font-size:9pt; color:#000;
          background:#fff; padding:0.4in; max-width:8.5in; margin:0 auto; }}
  h1 {{ font-size:11pt; font-weight:bold; text-align:center; margin-bottom:2px; }}
  .sub {{ font-size:8pt; text-align:center; margin-bottom:8px; }}
  .header-grid {{ display:grid; grid-template-columns:1fr 1fr 1fr; gap:4px;
                  margin-bottom:6px; border:1px solid #000; padding:4px; }}
  .hf {{ display:flex; flex-direction:column; }}
  .hf label {{ font-size:7pt; font-weight:bold; color:#444; }}
  .hf span {{ font-size:9pt; border-bottom:1px solid #999; min-height:16px; padding-bottom:1px; }}
  table {{ width:100%; border-collapse:collapse; margin-bottom:6px; }}
  th {{ font-size:8pt; font-weight:bold; background:#1e3a5f; color:#fff;
        padding:3px 4px; text-align:center; }}
  td {{ border:1px solid #aaa; padding:2px 4px; font-size:8pt; vertical-align:middle; }}
  td.sect {{ background:#dce8f5; font-weight:bold; font-size:8pt; padding:3px 4px; }}
  td.chk {{ text-align:center; width:64px; font-weight:bold; }}
  td.sat {{ color:#166534; }}
  td.unsat {{ color:#991b1b; background:#fef2f2; }}
  td.rem {{ font-size:7.5pt; color:#555; }}
  .sig-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-top:10px; }}
  .sig-block {{ border-top:1px solid #000; padding-top:2px; font-size:7.5pt; color:#555; }}
  .notice {{ font-size:7pt; border:1px solid #aaa; padding:4px 6px; margin:6px 0; background:#fffbeb; }}
  .fault-banner {{ background:#fef2f2; border:1px solid #dc2626; color:#991b1b; font-weight:bold;
                   font-size:9pt; padding:4px 8px; margin-bottom:6px; border-radius:3px; }}
  @media print {{ body {{ padding:0.25in; }} .no-print {{ display:none !important; }} }}
</style>
</head>
<body>

<button class="no-print" onclick="window.print()"
  style="float:right;padding:6px 16px;background:#1e3a5f;color:#fff;border:none;
         border-radius:4px;cursor:pointer;font-size:9pt;margin-bottom:8px;">
  &#128424; Print / Save PDF
</button>

<h1>{_esc(title)}</h1>
<div class="sub">{'Dispatch Record' if is_dispatch else 'Run Log'} #{r['id']}</div>

{banner}

<div class="header-grid">
  <div class="hf"><label>Unit</label><span>{_esc(r.get('name'))}</span></div>
  <div class="hf"><label>Make / Model</label><span>{_esc(unit)}</span></div>
  <div class="hf"><label>Serial #</label><span>{_esc(r.get('serial_num'))}</span></div>
  <div class="hf"><label>Rating</label><span>{_esc(r.get('rating'))}</span></div>
  <div class="hf"><label>Fuel</label><span>{_esc(r.get('fuel_type'))}</span></div>
  <div class="hf"><label>{'Destination' if is_dispatch else 'Location'}</label><span>{_esc(r.get('destination') if is_dispatch else r.get('location'))}</span></div>
  <div class="hf"><label>Date Out</label><span>{_esc(r.get('date_out'))}</span></div>
  <div class="hf"><label>Date In</label><span>{_esc(r.get('date_in'))}</span></div>
  <div class="hf"><label>Hours Run</label><span>{_esc(elapsed)}</span></div>
  <div class="hf"><label>Hour Meter Start</label><span>{_esc(hs)}</span></div>
  <div class="hf"><label>Hour Meter End</label><span>{_esc(he)}</span></div>
  <div class="hf"><label>Status</label><span>{_esc(r.get('status'))}</span></div>
</div>

<table>
  <thead>
    <tr>
      <th style="text-align:left;">Item</th>
      <th>SAT / UNSAT</th>
      <th style="text-align:left;">Remarks</th>
    </tr>
  </thead>
  <tbody>
    {rows_html}
  </tbody>
</table>

<div class="notice">
  Never refuel a hot or running engine. Operate generators outdoors only &mdash; engine exhaust
  contains <strong>carbon monoxide</strong>. Verify grounding before applying load. Breathing air
  must meet <strong>NFPA 1989</strong> quality before any cylinder is filled.
</div>

<div class="sig-grid">
  <div class="sig-block">Operator (Print): {_esc(r.get('operator_name'))}<br><br>
    Signature: ___________________________<br><br>Phone: {_esc(r.get('operator_phone'))}</div>
  <div class="sig-block">Supervisor (Print): {_esc(r.get('dispatcher_name'))}<br><br>
    Signature: ___________________________<br><br>Date: {_esc(r.get('date_in') or r.get('date_out'))}</div>
</div>

{f'<div class="notice"><strong>Notes:</strong> {_esc(r.get("notes"))}</div>' if r.get("notes") else ''}

</body>
</html>"""
