from typing import Optional
from fastapi import APIRouter, Depends, Query
from backend.database import get_db

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("")
async def dashboard_summary(db=Depends(get_db), me: Optional[str] = Query(None)):
    async def scalar(sql, params=()):
        async with db.execute(sql, params) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

    total_equipment = await scalar("SELECT COUNT(*) FROM equipment WHERE status='active'")
    overdue_tasks   = await scalar("SELECT COUNT(*) FROM maintenance_tasks WHERE status='overdue'")
    pending_tasks   = await scalar("SELECT COUNT(*) FROM maintenance_tasks WHERE status='pending'")
    tasks_due_7d    = await scalar("""
        SELECT COUNT(*) FROM maintenance_tasks
        WHERE status='pending' AND next_due IS NOT NULL
        AND DATE(next_due) <= DATE('now', '+7 days')
    """)
    cal_overdue     = await scalar("""
        SELECT COUNT(*) FROM (
            SELECT MAX(id) as id FROM calibration_records GROUP BY equipment_id
        ) latest
        JOIN calibration_records c ON c.id = latest.id
        WHERE c.next_due IS NOT NULL AND DATE(c.next_due) < DATE('now')
    """)
    cal_due_30d     = await scalar("""
        SELECT COUNT(*) FROM (
            SELECT MAX(id) as id FROM calibration_records GROUP BY equipment_id
        ) latest
        JOIN calibration_records c ON c.id = latest.id
        WHERE c.next_due IS NOT NULL
        AND DATE(c.next_due) BETWEEN DATE('now') AND DATE('now', '+30 days')
    """)
    low_stock       = await scalar("SELECT COUNT(*) FROM inventory_items WHERE quantity <= min_stock AND min_stock > 0")
    warranty_soon   = await scalar("""
        SELECT COUNT(*) FROM equipment
        WHERE warranty_expiry IS NOT NULL AND status='active'
        AND DATE(warranty_expiry) BETWEEN DATE('now') AND DATE('now', '+90 days')
    """)
    eol_soon        = await scalar("""
        SELECT COUNT(*) FROM equipment
        WHERE end_of_life_date IS NOT NULL AND status='active'
        AND DATE(end_of_life_date) <= DATE('now', '+180 days')
    """)

    async with db.execute("""
        SELECT m.id, m.title, m.next_due, m.status, e.name as equipment_name
        FROM maintenance_tasks m
        JOIN equipment e ON e.id = m.equipment_id
        WHERE m.status IN ('overdue','pending')
        AND (m.next_due IS NULL OR DATE(m.next_due) <= DATE('now', '+14 days'))
        ORDER BY CASE m.status WHEN 'overdue' THEN 0 ELSE 1 END, m.next_due ASC
        LIMIT 10
    """) as cur:
        upcoming_tasks = [dict(r) for r in await cur.fetchall()]

    # My tasks (only when caller passes ?me=username)
    my_tasks_count   = 0
    my_upcoming      = []
    if me:
        my_tasks_count = await scalar(
            "SELECT COUNT(*) FROM maintenance_tasks WHERE status IN ('pending','overdue') AND assigned_to=?",
            (me,)
        )
        async with db.execute("""
            SELECT m.id, m.title, m.next_due, m.status, m.equipment_id, e.name as equipment_name
            FROM maintenance_tasks m
            JOIN equipment e ON e.id = m.equipment_id
            WHERE m.status IN ('overdue','pending') AND m.assigned_to=?
            ORDER BY CASE m.status WHEN 'overdue' THEN 0 ELSE 1 END, m.next_due ASC
            LIMIT 10
        """, (me,)) as cur:
            my_upcoming = [dict(r) for r in await cur.fetchall()]

    async with db.execute("""
        SELECT c.id, c.next_due, c.result, e.name as equipment_name, e.serial_num
        FROM calibration_records c
        JOIN equipment e ON e.id = c.equipment_id
        WHERE c.id IN (SELECT MAX(id) FROM calibration_records GROUP BY equipment_id)
        AND c.next_due IS NOT NULL
        AND DATE(c.next_due) <= DATE('now', '+30 days')
        ORDER BY c.next_due ASC
        LIMIT 10
    """) as cur:
        upcoming_cals = [dict(r) for r in await cur.fetchall()]

    async with db.execute("""
        SELECT * FROM inventory_items
        WHERE quantity <= min_stock AND min_stock > 0
        ORDER BY (min_stock - quantity) DESC
        LIMIT 10
    """) as cur:
        low_items = [dict(r) for r in await cur.fetchall()]

    rs_total      = await scalar("SELECT COUNT(*) FROM rolling_stock WHERE status != 'retired'")
    rs_dispatched = await scalar("SELECT COUNT(*) FROM rolling_stock WHERE status = 'dispatched'")
    rs_maint      = await scalar("SELECT COUNT(*) FROM rolling_stock WHERE status = 'maintenance'")

    async with db.execute("""
        SELECT id, name, serial_num, warranty_expiry, end_of_life_date,
               CASE
                 WHEN end_of_life_date IS NOT NULL AND DATE(end_of_life_date) <= DATE('now', '+180 days')
                   THEN 'eol'
                 ELSE 'warranty'
               END as alert_type
        FROM equipment
        WHERE status='active' AND (
            (warranty_expiry IS NOT NULL AND DATE(warranty_expiry) BETWEEN DATE('now') AND DATE('now', '+90 days'))
            OR
            (end_of_life_date IS NOT NULL AND DATE(end_of_life_date) <= DATE('now', '+180 days'))
        )
        ORDER BY COALESCE(end_of_life_date, warranty_expiry) ASC
        LIMIT 10
    """) as cur:
        lifecycle_alerts = [dict(r) for r in await cur.fetchall()]

    async with db.execute("""
        SELECT t.id, t.title, e.category as equipment_category,
               MAX(s.completed_at) as last_run,
               CAST(julianday('now') - julianday(MAX(s.completed_at)) AS INTEGER) as days_since,
               COUNT(s.id) as total_runs
        FROM pmcs_templates t
        LEFT JOIN equipment e ON e.id = t.equipment_id
        LEFT JOIN pmcs_sessions s ON s.template_id = t.id AND s.status = 'completed'
        GROUP BY t.id, t.title, e.category
        ORDER BY last_run ASC NULLS FIRST
        LIMIT 15
    """) as cur:
        pmcs_compliance = [dict(r) for r in await cur.fetchall()]

    stale_pmcs = sum(1 for p in pmcs_compliance if p["days_since"] is None or p["days_since"] > 30)

    return {
        "counts": {
            "total_equipment": total_equipment,
            "overdue_tasks": overdue_tasks,
            "pending_tasks": pending_tasks,
            "tasks_due_7d": tasks_due_7d,
            "cal_overdue": cal_overdue,
            "cal_due_30d": cal_due_30d,
            "low_stock": low_stock,
            "warranty_soon": warranty_soon,
            "eol_soon": eol_soon,
            "rs_total": rs_total,
            "rs_dispatched": rs_dispatched,
            "rs_maint": rs_maint,
        },
        "stale_pmcs": stale_pmcs,
        "pmcs_compliance": pmcs_compliance,
        "my_tasks_count": my_tasks_count,
        "my_upcoming": my_upcoming,
        "upcoming_tasks": upcoming_tasks,
        "upcoming_cals": upcoming_cals,
        "low_items": low_items,
        "lifecycle_alerts": lifecycle_alerts,
    }
