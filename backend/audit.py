"""Thin helper to write audit log entries."""
import json


async def log(db, entity_type: str, entity_id: int, action: str,
              equipment_id: int = None, actor: str = None, detail: dict = None):
    await db.execute("""
        INSERT INTO audit_log (entity_type, entity_id, equipment_id, action, actor, detail)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (entity_type, entity_id, equipment_id, action, actor,
          json.dumps(detail) if detail else None))
