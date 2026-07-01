from fastapi import APIRouter, Depends
from backend.database import get_db
import calendar as cal_module

router = APIRouter(prefix="/api/calendar", tags=["calendar"])


@router.get("")
async def get_calendar(year: int, month: int, db=Depends(get_db)):
    _, last_day = cal_module.monthrange(year, month)
    first = f"{year}-{month:02d}-01"
    last  = f"{year}-{month:02d}-{last_day:02d}"

    async with db.execute("""
        SELECT t.id, t.title, t.next_due, t.status, e.name as equipment_name
        FROM maintenance_tasks t
        LEFT JOIN equipment e ON e.id = t.equipment_id
        WHERE t.next_due IS NOT NULL
          AND t.status NOT IN ('completed', 'cancelled')
          AND t.next_due BETWEEN ? AND ?
        ORDER BY t.next_due
    """, (first, last)) as cur:
        maintenance = [dict(r) for r in await cur.fetchall()]

    async with db.execute("""
        SELECT c.id, c.next_due, e.name as equipment_name
        FROM calibration_records c
        JOIN equipment e ON e.id = c.equipment_id
        WHERE c.id IN (SELECT MAX(id) FROM calibration_records GROUP BY equipment_id)
          AND c.next_due IS NOT NULL
          AND c.next_due BETWEEN ? AND ?
        ORDER BY c.next_due
    """, (first, last)) as cur:
        calibrations = [dict(r) for r in await cur.fetchall()]

    return {"maintenance": maintenance, "calibrations": calibrations}
