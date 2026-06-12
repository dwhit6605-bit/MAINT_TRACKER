"""
Import equipment/calibration/inventory CSV exports into the MAINT SUPER database.
Vehicle tracking CSVs are excluded — this system focuses on equipment, calibration, and inventory.
Run from project root:  python scripts/import_csv.py [--db maint.db]
"""
import csv, io, re, sqlite3, sys, os
from datetime import datetime, date

DB_PATH = sys.argv[sys.argv.index("--db") + 1] if "--db" in sys.argv else "maint.db"
LIST_DIR = "lists"

# ── date parsing ──────────────────────────────────────────────────────────────
_FMTS = ["%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%d-%b-%Y", "%d-%b-%y",
         "%m-%d-%Y", "%B %d, %Y"]

def parse_date(s):
    if not s:
        return None
    s = s.strip()
    if not s or s.lower() in ("n/a", "n/a (lifecycle end)", "tbd", "none", "-", "false", "true"):
        return None
    # "25-Jun"  -> ambiguous year — assume current year or next
    if re.fullmatch(r"\d{1,2}-[A-Za-z]{3}", s):
        try:
            d = datetime.strptime(s, "%d-%b")
            yr = date.today().year
            candidate = d.replace(year=yr)
            if candidate.date() < date.today():
                candidate = d.replace(year=yr + 1)
            return candidate.strftime("%Y-%m-%d")
        except:
            pass
    for fmt in _FMTS:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except:
            pass
    return None

def parse_qty(s):
    """Extract first integer from strings like '7BOX / 29EA', '193', '4 Box'."""
    if not s:
        return 0
    m = re.search(r"\d+", str(s).replace(",", ""))
    return int(m.group()) if m else 0

# ── SharePoint CSV reader (skips ListSchema= header row) ─────────────────────
def read_sp_csv(filename):
    path = os.path.join(LIST_DIR, filename)
    with open(path, encoding="utf-8-sig") as f:
        lines = f.readlines()
    start = 0
    for i, ln in enumerate(lines):
        if ln.startswith("ListSchema="):
            start = i + 1
            break
    reader = csv.DictReader(io.StringIO("".join(lines[start:])))
    return list(reader)

# ── main import ───────────────────────────────────────────────────────────────
def main():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # wipe seeded test data
    for tbl in ("inventory_transactions", "inventory_items",
                "calibration_records", "maintenance_tasks", "equipment"):
        cur.execute(f"DELETE FROM {tbl}")
    con.commit()
    print("Cleared existing data.")

    eq_counts = {}  # name -> id  (dedup tracker)

    def upsert_equipment(name, category, serial=None, model=None,
                         manufacturer=None, location=None, status="active", notes=None):
        key = (name.strip(), serial.strip() if serial else None)
        cur.execute("""
            INSERT INTO equipment (name, category, serial_num, model, manufacturer, location, status, notes)
            VALUES (?,?,?,?,?,?,?,?)
        """, (name.strip(), category, serial.strip() if serial else None,
              model, manufacturer, location.strip() if location else None,
              status.strip().lower() if status else "active", notes))
        return cur.lastrowid

    # ── 1. Calibration Tracker ────────────────────────────────────────────────
    print("\n[1/6] Calibration Tracker…")
    rows = read_sp_csv("Calibration Tracker (1).csv")
    for r in rows:
        name   = r.get("Piece of Equipment", "").strip()
        serial = r.get("Serial %23", "").strip()
        if not name:
            continue
        loc    = r.get("Location", "")
        sect   = r.get("Section", "")
        stat_raw = r.get("Status", "active").lower()
        status = "retired" if "t/i" in stat_raw or "turn-in" in stat_raw else "active"
        notes  = r.get("NOTES", "").strip() or None
        if sect:
            notes = (f"Section: {sect}" + (f" | {notes}" if notes else ""))

        eq_id = upsert_equipment(name, "Test Equipment", serial=serial,
                                 location=loc, status=status, notes=notes)

        last_cal  = parse_date(r.get("Last Cal", ""))
        next_due  = parse_date(r.get("Date Due", ""))
        shop      = r.get("Calibration Shop", "").strip() or None
        if last_cal or next_due:
            cur.execute("""
                INSERT INTO calibration_records
                    (equipment_id, calibrated_by, calibrated_at, next_due, result)
                VALUES (?,?,?,?,?)
            """, (eq_id, shop, last_cal or date.today().isoformat(), next_due, "pass"))
    print(f"  → {len(rows)} calibration items imported")

    # ── 2. SUITS PRESSURE TESTS ───────────────────────────────────────────────
    print("\n[2/6] Suits Pressure Tests…")
    rows = read_sp_csv("SUITS PRESSURE TESTS.csv")
    for r in rows:
        serial = r.get("SERIAL NUMBER ", "").strip()
        model  = r.get("MODEL", "").strip()
        size   = r.get("SIZE", "").strip()
        loc    = r.get("LOCATION ", "").strip()
        result_raw = r.get("PASS Y/N ", "").strip().lower()
        assign = r.get("PERSONAL/CAGE/IRT", "").strip() or None

        if not serial:
            continue

        if result_raw == "retired":
            status = "retired"
            result = "pass"
        elif result_raw in ("n", "no", "fail", "failed"):
            status = "inactive"
            result = "fail"
        else:
            status = "active"
            result = "pass"

        name = f"Hazmat Suit — {model}" if model else "Hazmat Suit"
        notes = f"Size: {size}" if size else None
        if assign:
            notes = (notes + f" | Assigned: {assign}") if notes else f"Assigned: {assign}"

        eq_id = upsert_equipment(name, "Protective Equipment", serial=serial,
                                 model=model, location=loc, status=status, notes=notes)

        test_date = parse_date(r.get("DATE ", ""))
        if test_date or result == "fail":
            cur.execute("""
                INSERT INTO calibration_records
                    (equipment_id, calibrated_by, calibrated_at, result, notes)
                VALUES (?,?,?,?,?)
            """, (eq_id, "In-House Pressure Test",
                  test_date or date.today().isoformat(),
                  result, "Pressure test"))
    print(f"  → {len(rows)} suits imported")

    # ── 3. Bottle Tracker ─────────────────────────────────────────────────────
    print("\n[3/6] Bottle Tracker…")
    rows = read_sp_csv("Bottle Tracker.csv")
    for r in rows:
        equip_name = r.get("Equipment", "SCBA Bottle").strip()
        serial     = r.get("Serial Number", "").strip()
        admin_no   = r.get("Admin No.", "").strip()
        status_raw = r.get("Status", "").strip().lower()
        loc        = r.get("Storage Location", "").strip()
        ensemble   = r.get("Ensemble Number", "").strip()

        if not serial:
            continue

        status = "retired" if "lifecycle" in (r.get("Due Date","").lower()) else "active"
        if "prepped" in status_raw or "turn in" in status_raw:
            status = "retired"

        notes_parts = []
        if admin_no:  notes_parts.append(f"Admin #: {admin_no}")
        if ensemble:  notes_parts.append(f"Ensemble: {ensemble}")
        notes = " | ".join(notes_parts) or None

        eq_id = upsert_equipment(equip_name, "SCBA / Breathing Apparatus",
                                 serial=serial, location=loc, status=status, notes=notes)

        last_test = parse_date(r.get("Last Test Date", ""))
        due_date  = parse_date(r.get("Due Date", ""))
        lifecycle = parse_date(r.get("Lifecycle End Date", ""))

        if last_test:
            cur.execute("""
                INSERT INTO calibration_records
                    (equipment_id, calibrated_by, calibrated_at, next_due, result, notes)
                VALUES (?,?,?,?,?,?)
            """, (eq_id, "Hydro Test", last_test, due_date, "pass",
                  f"Lifecycle end: {lifecycle}" if lifecycle else None))
    print(f"  → {len(rows)} bottles imported")

    # ── 4. Generator Service Tracker ─────────────────────────────────────────
    print("\n[4/5] Generator Service Tracker…")
    rows = read_sp_csv("Generator Service Tracker.csv")
    for r in rows:
        admin   = r.get("ADMIN %23", "").strip()
        model   = r.get("Generator Model", "").strip()
        serial  = r.get("Serial %23", "").strip()
        notes_raw = r.get("Additional Notes", "").strip()
        interval_raw = r.get("Interval", "").strip()
        cur_hrs = r.get("Current Hours", "").strip()
        due_hrs = r.get("Service Due @ Hours", "").strip()

        if not model:
            continue

        name = f"{model}" + (f" ({admin})" if admin else "")
        notes_parts = []
        if interval_raw: notes_parts.append(f"Interval: {interval_raw}")
        if cur_hrs:      notes_parts.append(f"Current hrs: {cur_hrs}")
        if due_hrs:      notes_parts.append(f"Service due at: {due_hrs} hrs")
        if notes_raw:    notes_parts.append(notes_raw)

        eq_id = upsert_equipment(name, "Generators / APU", serial=serial,
                                 manufacturer="Kubota" if "kubota" in model.lower() else None,
                                 location="Motor Pool",
                                 notes=" | ".join(notes_parts) or None)

        next_svc  = parse_date(r.get("Next Service Due", ""))
        last_svc  = parse_date(r.get("Last Service Completed", ""))
        completed = r.get("Service Completed Ths Year", "NO").strip().upper() == "YES"

        cur.execute("""
            INSERT INTO maintenance_tasks
                (equipment_id, title, task_type, interval_days, last_done, next_due, status)
            VALUES (?,?,?,?,?,?,?)
        """, (eq_id, "Generator Service", "scheduled", 365,
              last_svc, next_svc,
              "completed" if completed else ("overdue" if next_svc and next_svc < date.today().isoformat() else "pending")))
    print(f"  → {len(rows)} generators imported")

    # ── 5. Expendables Tracker ────────────────────────────────────────────────
    print("\n[5/5] Expendables Tracker…")
    rows = read_sp_csv("Expendables Tracker (1).csv")
    imported = 0
    for r in rows:
        item = r.get("Item", "").strip()
        if not item:
            continue

        qty_auth  = parse_qty(r.get("Qnty Auth", "0"))
        qty_hand  = parse_qty(r.get("Qnty on Hand", "0"))
        min_req   = parse_qty(r.get("Minimum Required", "0"))
        loc       = r.get("Location", "").strip() or None
        nsn       = r.get("NSN", "").strip() or None
        issued_to = r.get("Issued TO:", "").strip() or None
        status_raw = r.get("Status", "").strip()

        notes_parts = []
        if nsn:       notes_parts.append(f"NSN: {nsn}")
        if issued_to: notes_parts.append(f"Issued to: {issued_to}")
        if qty_auth:  notes_parts.append(f"Auth qty: {qty_auth}")
        if status_raw and status_raw.lower() not in ("", "false", "true", "ok"):
            notes_parts.append(f"Status: {status_raw}")

        cur.execute("""
            INSERT INTO inventory_items
                (name, location, quantity, unit, min_stock, notes)
            VALUES (?,?,?,?,?,?)
        """, (item, loc, qty_hand, "ea", min_req,
              " | ".join(notes_parts) or None))
        item_id = cur.lastrowid

        if qty_hand > 0:
            cur.execute("""
                INSERT INTO inventory_transactions (item_id, action, quantity, reference)
                VALUES (?,?,?,?)
            """, (item_id, "add", qty_hand, "imported from Expendables Tracker"))
        imported += 1
    print(f"  → {imported} expendable items imported")

    con.commit()
    con.close()

    # ── summary ───────────────────────────────────────────────────────────────
    con2 = sqlite3.connect(DB_PATH)
    c2   = con2.cursor()
    print("\n── Import Summary ──────────────────────────────────────────")
    for tbl, label in [("equipment","Equipment"), ("maintenance_tasks","Maintenance tasks"),
                       ("calibration_records","Calibration records"), ("inventory_items","Inventory items")]:
        c2.execute(f"SELECT COUNT(*) FROM {tbl}")
        print(f"  {label}: {c2.fetchone()[0]}")
    con2.close()
    print("Done.")

if __name__ == "__main__":
    main()
