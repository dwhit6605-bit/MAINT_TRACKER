from fastapi import APIRouter, Depends
from backend.database import get_db

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("")
async def dashboard_summary(db=Depends(get_db)):
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

    return {
        "counts": {
            "total_equipment": total_equipment,
            "overdue_tasks": overdue_tasks,
            "pending_tasks": pending_tasks,
            "tasks_due_7d": tasks_due_7d,
            "cal_overdue": cal_overdue,
            "cal_due_30d": cal_due_30d,
            "low_stock": low_stock,
        },
        "upcoming_tasks": upcoming_tasks,
        "upcoming_cals": upcoming_cals,
        "low_items": low_items,
    }
