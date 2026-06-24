from fastapi import APIRouter, Depends, Request
from backend.database import get_db

router = APIRouter(prefix="/api/commander", tags=["commander"])


@router.get("")
async def commander_summary(request: Request, db=Depends(get_db)):

    async def scalar(sql, params=()):
        async with db.execute(sql, params) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

    # Equipment MC%
    total_eq     = await scalar("SELECT COUNT(*) FROM equipment WHERE status='active'")
    deadline_eq  = await scalar("SELECT COUNT(*) FROM equipment WHERE status='deadline'")
    mc_count     = total_eq - deadline_eq
    mc_pct       = round(mc_count / total_eq * 100) if total_eq else 0

    # Open faults
    open_faults  = await scalar("SELECT COUNT(*) FROM fault_reports WHERE status IN ('open','in_progress')")
    urgent_faults = await scalar("""
        SELECT COUNT(*) FROM fault_reports
        WHERE status IN ('open','in_progress')
        AND julianday('now') - julianday(created_at) > 2
    """)

    # Calibration overdue
    cal_overdue  = await scalar("""
        SELECT COUNT(*) FROM (
            SELECT MAX(id) as id FROM calibration_records GROUP BY equipment_id
        ) latest JOIN calibration_records c ON c.id = latest.id
        WHERE c.next_due IS NOT NULL AND DATE(c.next_due) < DATE('now')
    """)
    cal_due_14d  = await scalar("""
        SELECT COUNT(*) FROM (
            SELECT MAX(id) as id FROM calibration_records GROUP BY equipment_id
        ) latest JOIN calibration_records c ON c.id = latest.id
        WHERE c.next_due IS NOT NULL
        AND DATE(c.next_due) BETWEEN DATE('now') AND DATE('now', '+14 days')
    """)

    # Maintenance overdue
    maint_overdue = await scalar("SELECT COUNT(*) FROM maintenance_tasks WHERE status='overdue'")

    # Rolling stock
    async with db.execute("""
        SELECT id, name, serial_num, status, vehicle_type
        FROM rolling_stock WHERE status != 'retired'
        ORDER BY name
    """) as cur:
        vehicles = [dict(r) for r in await cur.fetchall()]

    rs_available  = sum(1 for v in vehicles if v["status"] == "available")
    rs_dispatched = sum(1 for v in vehicles if v["status"] == "dispatched")
    rs_maint      = sum(1 for v in vehicles if v["status"] == "maintenance")
    rs_deadline   = sum(1 for v in vehicles if v["status"] == "deadline")

    # Top open faults (age in days)
    async with db.execute("""
        SELECT f.id, f.title, f.severity, f.status,
               e.name as equipment_name,
               CAST(julianday('now') - julianday(f.created_at) AS INTEGER) as age_days
        FROM fault_reports f
        LEFT JOIN equipment e ON e.id = f.equipment_id
        WHERE f.status IN ('open','in_progress')
        ORDER BY age_days DESC, f.severity DESC
        LIMIT 6
    """) as cur:
        top_faults = [dict(r) for r in await cur.fetchall()]

    # Calibration overdue items
    async with db.execute("""
        SELECT c.next_due, e.name as equipment_name,
               CAST(julianday('now') - julianday(c.next_due) AS INTEGER) as days_over
        FROM calibration_records c
        JOIN equipment e ON e.id = c.equipment_id
        WHERE c.id IN (SELECT MAX(id) FROM calibration_records GROUP BY equipment_id)
        AND c.next_due IS NOT NULL
        AND DATE(c.next_due) <= DATE('now', '+14 days')
        ORDER BY c.next_due ASC
        LIMIT 6
    """) as cur:
        cal_items = [dict(r) for r in await cur.fetchall()]

    # Readiness by category
    async with db.execute("""
        SELECT category,
               COUNT(*) as total,
               SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) as active,
               SUM(CASE WHEN status='deadline' THEN 1 ELSE 0 END) as deadline
        FROM equipment
        WHERE status IN ('active','deadline')
        GROUP BY category
        ORDER BY total DESC
    """) as cur:
        by_category = [dict(r) for r in await cur.fetchall()]

    # PMCS compliance (stale = no run in 30d)
    async with db.execute("""
        SELECT t.title, MAX(s.completed_at) as last_run,
               CAST(julianday('now') - julianday(MAX(s.completed_at)) AS INTEGER) as days_since
        FROM pmcs_templates t
        LEFT JOIN pmcs_sessions s ON s.template_id = t.id AND s.status='completed'
        GROUP BY t.id, t.title
        ORDER BY CASE WHEN MAX(s.completed_at) IS NULL THEN 0 ELSE 1 END ASC, last_run ASC
        LIMIT 5
    """) as cur:
        pmcs_status = [dict(r) for r in await cur.fetchall()]

    return {
        "mc_pct": mc_pct,
        "mc_count": mc_count,
        "total_eq": total_eq,
        "deadline_eq": deadline_eq,
        "open_faults": open_faults,
        "urgent_faults": urgent_faults,
        "cal_overdue": cal_overdue,
        "cal_due_14d": cal_due_14d,
        "maint_overdue": maint_overdue,
        "rs_available": rs_available,
        "rs_dispatched": rs_dispatched,
        "rs_maint": rs_maint,
        "rs_deadline": rs_deadline,
        "vehicles": vehicles,
        "top_faults": top_faults,
        "cal_items": cal_items,
        "by_category": by_category,
        "pmcs_status": pmcs_status,
    }
