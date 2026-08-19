#!/usr/bin/env python3
"""Remove rows orphaned while ON DELETE CASCADE was inert.

SQLite disables foreign keys per connection by default, so every CASCADE in the
schema did nothing: deleting equipment left its maintenance tasks and SKO links
behind. The dashboard counts tasks by status without joining equipment, so those
leftovers showed up as overdue work against kit that no longer exists.

The pragma is now enabled, which stops new orphans, but does not retroactively
remove old ones. This does, deleting exactly what CASCADE would have.

    venv/bin/python scripts/clean_orphaned_rows.py            # dry run
    venv/bin/python scripts/clean_orphaned_rows.py --apply
"""
import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="delete them (default is a dry run)")
    args = ap.parse_args()

    db = resolve_db()
    if not db.exists():
        sys.exit(f"database not found: {db}")
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row

    violations = list(con.execute("PRAGMA foreign_key_check"))
    if not violations:
        print(f"{db}\nNo orphaned rows. Nothing to do.")
        return

    # group as {table: {parent: [rowid, ...]}}
    grouped = {}
    for tbl, rowid, parent, _fkid in violations:
        grouped.setdefault(tbl, {}).setdefault(parent, []).append(rowid)

    print(f"{'APPLY' if args.apply else 'DRY RUN'} — {db}\n")
    total = 0
    for tbl, parents in sorted(grouped.items()):
        for parent, rowids in sorted(parents.items()):
            total += len(rowids)
            print(f"  {tbl} -> {parent}: {len(rowids)} orphaned row(s)")
            cols = [r[1] for r in con.execute(f"PRAGMA table_info({tbl})")]
            label = next((c for c in ("title", "name", "nom") if c in cols), None)
            marks = ",".join("?" * len(rowids))
            sel = (f"SELECT rowid AS rid, {label or 'rowid'} AS lbl "
                   f"FROM {tbl} WHERE rowid IN ({marks})")
            for r in list(con.execute(sel, rowids))[:6]:
                print(f"      rowid {r['rid']}: {r['lbl']}")
            if len(rowids) > 6:
                print(f"      … and {len(rowids) - 6} more")

    if not args.apply:
        print(f"\n{total} row(s) would be deleted. Re-run with --apply to commit.")
        return

    backup = db.with_suffix(f".pre-orphan-clean-{datetime.now():%Y%m%d-%H%M%S}.db")
    shutil.copy2(db, backup)
    print(f"\nBackup written: {backup.name}")

    deleted = 0
    for tbl, parents in grouped.items():
        ids = sorted({r for rowids in parents.values() for r in rowids})
        marks = ",".join("?" * len(ids))
        deleted += con.execute(f"DELETE FROM {tbl} WHERE rowid IN ({marks})", ids).rowcount
    con.commit()

    remaining = len(list(con.execute("PRAGMA foreign_key_check")))
    print(f"Deleted {deleted} row(s).")
    print(f"Remaining violations (must be 0): {remaining}")
    con.close()


if __name__ == "__main__":
    main()
