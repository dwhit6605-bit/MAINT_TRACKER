"""Readiness dashboard — aggregated status across all entities."""
from fastapi import APIRouter, Depends
from backend.database import get_db
from datetime import datetime

router = APIRouter(prefix="/api/readiness", tags=["readiness"])


@router.get("")
async def get_readiness(db=Depends(get_db)):
    today = datetime.utcnow().date().isoformat()

    # Equipment breakdown by status
    async with db.execute("""
        SELECT status, COUNT(*) as cnt FROM equipment GROUP BY status
    """) as cur:
        eq_status = {r["status"]: r["cnt"] for r in await cur.fetchall()}

    total_eq = sum(eq_status.values())
    nmc = eq_status.get("maintenance", 0) + eq_status.get("inactive", 0) + eq_status.get("retired", 0)
    mc  = eq_status.get("active", 0)

    # Maintenance task breakdown
    async with db.execute("""
        SELECT status, COUNT(*) as cnt FROM maintenance_tasks
        WHERE status != 'completed' GROUP BY status
    """) as cur:
        task_counts = {r["status"]: r["cnt"] for r in await cur.fetchall()}

    # Overdue tasks with equipment info
    async with db.execute("""
        SELECT m.id, m.title, m.next_due, m.status, e.name as equipment_name,
               e.location, e.category
        FROM maintenance_tasks m
        JOIN equipment e ON e.id = m.equipment_id
        WHERE m.status IN ('overdue','pending') AND m.next_due < ?
        ORDER BY m.next_due ASC LIMIT 50
    """, (today,)) as cur:
        overdue_tasks = [dict(r) for r in await cur.fetchall()]

    # Calibration compliance — latest cal per equipment
    async with db.execute("""
        SELECT e.id, e.name, e.category, e.location,
               MAX(c.calibrated_at) as last_cal,
               (SELECT next_due FROM calibration_records
                WHERE equipment_id=e.id ORDER BY calibrated_at DESC LIMIT 1) as next_due
        FROM equipment e
        LEFT JOIN calibration_records c ON c.equipment_id = e.id
        WHERE e.status = 'active'
        GROUP BY e.id
        ORDER BY next_due ASC NULLS LAST
    """) as cur:
        cal_rows = [dict(r) for r in await cur.fetchall()]

    cal_overdue  = [r for r in cal_rows if r["next_due"] and r["next_due"] < today]
    cal_due_soon = [r for r in cal_rows if r["next_due"] and today <= r["next_due"]]
    cal_no_record = [r for r in cal_rows if not r["last_cal"]]

    # Low stock inventory
    async with db.execute("""
        SELECT id, name, part_number, quantity, min_stock, unit, location
        FROM inventory_items WHERE quantity <= min_stock
        ORDER BY (quantity - min_stock) ASC, name ASC
    """) as cur:
        low_stock = [dict(r) for r in await cur.fetchall()]

    # PMCS completion rate (last 30 days)
    async with db.execute("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN fault_count = 0 THEN 1 ELSE 0 END) as clean
        FROM pmcs_sessions
        WHERE status='completed'
          AND started_at >= date('now','-30 days')
    """) as cur:
        pmcs_row = dict(await cur.fetchone())

    return {
        "equipment": {
            "total": total_eq, "mc": mc, "nmc": nmc,
            "by_status": eq_status,
            "mc_pct": round(mc / total_eq * 100) if total_eq else 0,
        },
        "maintenance": {
            "overdue": task_counts.get("overdue", 0),
            "pending": task_counts.get("pending", 0),
            "overdue_items": overdue_tasks,
        },
        "calibration": {
            "overdue": len(cal_overdue),
            "due_soon": len(cal_due_soon),
            "no_record": len(cal_no_record),
            "overdue_items": cal_overdue,
            "no_record_items": cal_no_record,
        },
        "inventory": {
            "low_stock_count": len(low_stock),
            "low_stock_items": low_stock,
        },
        "pmcs_30d": {
            "total": pmcs_row["total"] or 0,
            "clean": pmcs_row["clean"] or 0,
            "pct": round((pmcs_row["clean"] or 0) / pmcs_row["total"] * 100) if pmcs_row["total"] else None,
        },
    }
