#!/usr/bin/env python3
"""Point equipment at the canonical location list consumables already use.

Equipment carried its own free-text `location`, so an area could hold stock and
end items that the app had no way to show together. This maps each distinct
equipment location string onto an inventory_locations row, creating any that do
not exist yet, and sets equipment.location_id.

The original text column is left untouched.

    venv/bin/python scripts/migrate_equipment_locations.py            # dry run
    venv/bin/python scripts/migrate_equipment_locations.py --apply
"""
import argparse
import os
import re
import shutil
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Strings that mean "no location" rather than naming one
# "NULL" is here because some rows hold the literal string, not SQL NULL —
# without it the migration would create a location called NULL.
BLANKS = {"", "N/A", "NA", "N\\A", "NONE", "NULL", "NIL", "UNKNOWN", "UNK", "-", "--", "TBD", "?"}


def resolve_db() -> Path:
    val = os.getenv("DB_PATH")
    if not val:
        env = ROOT / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                line = line.strip()
                if line.startswith("DB_PATH=") and not line.startswith("#"):
                    val = line.split("=", 1)[1].strip().strip("'\"")
                    break
    p = Path(val or "maint.db")
    return p if p.is_absolute() else (ROOT / p)


def norm(s):
    """Same rule the inventory location migration used, so both land together."""
    if not s or s.strip().upper() in BLANKS:
        return None
    t = re.sub(r"[^A-Z0-9]+", " ", s.upper())
    t = re.sub(r"(?<=[A-Z])(?=[0-9])|(?<=[0-9])(?=[A-Z])", " ", t)
    return "-".join(t.split()) or None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    db = resolve_db()
    if not db.exists():
        sys.exit(f"database not found: {db}")
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row

    cols = {r[1] for r in con.execute("PRAGMA table_info(equipment)")}
    if "location_id" not in cols:
        sys.exit("equipment.location_id missing — restart the app once so init_db() adds it.")

    rows = [dict(r) for r in con.execute(
        "SELECT id, name, location FROM equipment WHERE location_id IS NULL")]
    existing = {r["code"]: r["id"] for r in con.execute(
        "SELECT id, code FROM inventory_locations")}

    groups = defaultdict(list)
    for r in rows:
        groups[norm(r["location"])].append(r)

    print(f"{'APPLY' if args.apply else 'DRY RUN'} — {db}")
    print(f"{len(rows)} equipment record(s) without a location_id\n")

    unplaced = len(groups.get(None, []))
    to_create = [c for c in groups if c and c not in existing]
    for code in sorted(c for c in groups if c):
        n = len(groups[code])
        tag = "  <-- NEW location" if code not in existing else ""
        print(f"  {code:26} {n:4} item(s){tag}")
        for r in groups[code][:3]:
            print(f"       {r['name'][:52]}   (was {r['location']!r})")
        if n > 3:
            print(f"       … and {n - 3} more")
    if unplaced:
        print(f"\n  {unplaced} record(s) have no usable location and stay unassigned.")
        for r in groups[None][:5]:
            print(f"       {r['name'][:52]}   (was {r['location']!r})")

    placed = sum(len(v) for k, v in groups.items() if k)
    print(f"\n{placed} would be placed · {len(to_create)} new location(s) created · {unplaced} left unassigned")
    if not args.apply:
        print("Nothing written. Re-run with --apply to commit.")
        return

    backup = db.with_suffix(f".pre-eqloc-{datetime.now():%Y%m%d-%H%M%S}.db")
    shutil.copy2(db, backup)
    print(f"\nBackup written: {backup.name}")

    con.execute("PRAGMA foreign_keys=ON")
    done = 0
    for code, items in groups.items():
        if not code:
            continue
        if code not in existing:
            con.execute(
                "INSERT OR IGNORE INTO inventory_locations (code,name,sort_order) VALUES (?,?,500)",
                (code, code.replace("-", " ").title()))
            existing[code] = con.execute(
                "SELECT id FROM inventory_locations WHERE code=?", (code,)).fetchone()[0]
        lid = existing[code]
        con.executemany("UPDATE equipment SET location_id=? WHERE id=?",
                        [(lid, r["id"]) for r in items])
        done += len(items)
    con.commit()
    print(f"Placed {done} equipment record(s).")
    print("Unresolved FK violations (must be 0):",
          len(list(con.execute("PRAGMA foreign_key_check"))))
    con.close()


if __name__ == "__main__":
    main()
