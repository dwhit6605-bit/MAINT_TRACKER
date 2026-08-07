#!/usr/bin/env python3
"""One-time migration: free-text inventory locations -> canonical location rows + per-bin stock.

Collapses the 46 distinct ``inventory_items.location`` strings into a canonical
set, then creates one ``inventory_stock`` row per item holding its current
quantity at that location. Items with no location land in ``UNASSIGNED``.

Run the dry run first; it writes nothing and prints the full plan:

    venv/bin/python scripts/migrate_inventory_locations.py
    venv/bin/python scripts/migrate_inventory_locations.py --apply

Safe to re-run: items that already have stock rows are skipped.
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


def resolve_db() -> Path:
    """Mirror the app's lookup: $DB_PATH, else DB_PATH from .env, else maint.db.

    systemd hands the service its EnvironmentFile, but a shell running this
    script by hand does not, so read .env directly rather than guessing.
    """
    val = os.getenv("DB_PATH")
    if not val:
        env = ROOT / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                line = line.strip()
                if line.startswith("DB_PATH=") and not line.startswith("#"):
                    val = line.split("=", 1)[1].strip().strip("'\"")
                    break
    val = val or "maint.db"
    p = Path(val)
    return p if p.is_absolute() else (ROOT / p)


DB = resolve_db()

# Reviewed and approved 2026-08-07: these normalize to different strings but are
# the same physical place. Everything else merges only on punctuation/case.
MANUAL_MERGES = {
    "A-FRONT": "A-FRONT-TABLE",
}
UNASSIGNED = "UNASSIGNED"


def norm(s):
    """Uppercase, treat any run of non-alphanumerics as one separator, and split
    letter<->digit boundaries so 'C-SHELF1' lands with 'C-SHELF-1'."""
    if not s or not s.strip():
        return None
    t = re.sub(r"[^A-Z0-9]+", " ", s.upper())
    t = re.sub(r"(?<=[A-Z])(?=[0-9])|(?<=[0-9])(?=[A-Z])", " ", t)
    canon = "-".join(t.split())
    return MANUAL_MERGES.get(canon, canon)


def zone_of(code):
    """Leading single letter is the storage zone ('C-SHELF-1' -> 'C')."""
    head = code.split("-")[0]
    return head if len(head) == 1 and head.isalpha() else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write changes (default is a dry run)")
    args = ap.parse_args()

    if not DB.exists():
        sys.exit(f"database not found: {DB}")

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    have = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
        "('inventory_locations','inventory_stock')")}
    if len(have) < 2:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        sys.exit(
            f"inventory_locations / inventory_stock not found in {DB}\n"
            f"  this database has {len(tables)} tables"
            f"{' (looks empty — wrong file?)' if len(tables) < 5 else ''}\n"
            f"  missing: {', '.join(sorted({'inventory_locations','inventory_stock'} - have))}\n\n"
            "Either the service has not restarted onto the new code (init_db creates them\n"
            "at startup), or DB_PATH points somewhere else. Check with:\n"
            "  systemctl status maint-super\n"
            "  grep DB_PATH /opt/maint-super/.env")

    items = [dict(r) for r in con.execute(
        "SELECT id, name, location, quantity FROM inventory_items ORDER BY id")]

    groups = defaultdict(list)
    for it in items:
        groups[norm(it["location"]) or UNASSIGNED].append(it)

    already = {r[0] for r in con.execute("SELECT DISTINCT item_id FROM inventory_stock")}
    todo = [it for it in items if it["id"] not in already]

    print(f"{'APPLY' if args.apply else 'DRY RUN'} — {DB}")
    print(f"{len(items)} items, {len({i['location'] for i in items})} distinct location strings "
          f"-> {len(groups)} canonical locations")
    print(f"{len(todo)} items to place, {len(already)} already have stock rows\n")

    for code in sorted(groups):
        rows = groups[code]
        spellings = sorted({(r["location"] or "(blank)") for r in rows})
        qty = sum(r["quantity"] or 0 for r in rows)
        flag = "  <-- MERGED" if len(spellings) > 1 else ""
        print(f"  {code:22} {len(rows):3} items, {qty:5} units{flag}")
        if len(spellings) > 1:
            print(f"       from: {', '.join(repr(s) for s in spellings)}")

    if not args.apply:
        print("\nNothing written. Re-run with --apply to commit.")
        return

    backup = DB.with_suffix(f".pre-loc-migration-{datetime.now():%Y%m%d-%H%M%S}.db")
    shutil.copy2(DB, backup)
    print(f"\nBackup written: {backup.name}")

    placed = 0
    for order, code in enumerate(sorted(groups), start=1):
        con.execute(
            "INSERT OR IGNORE INTO inventory_locations (code,name,zone,sort_order) VALUES (?,?,?,?)",
            (code, code.replace("-", " ").title(), zone_of(code), order))
        loc_id = con.execute(
            "SELECT id FROM inventory_locations WHERE code=?", (code,)).fetchone()[0]
        for it in groups[code]:
            if it["id"] in already:
                continue
            con.execute(
                "INSERT OR IGNORE INTO inventory_stock (item_id,location_id,quantity) VALUES (?,?,?)",
                (it["id"], loc_id, it["quantity"] or 0))
            placed += 1
    con.commit()

    # The triggers recompute inventory_items.quantity from stock; confirm nothing moved.
    drift = con.execute("""
        SELECT COUNT(*) FROM inventory_items i
        WHERE i.quantity <> (SELECT COALESCE(SUM(quantity),0) FROM inventory_stock
                              WHERE item_id=i.id)
    """).fetchone()[0]
    print(f"Placed {placed} stock rows across {len(groups)} locations.")
    print(f"Rollup drift (must be 0): {drift}")
    con.close()


if __name__ == "__main__":
    main()
