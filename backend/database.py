import aiosqlite
import os

DB_PATH = os.getenv("DB_PATH", "maint.db")


async def get_db():
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    try:
        yield db
    finally:
        await db.close()


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS equipment (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                category    TEXT NOT NULL,
                serial_num  TEXT,
                model       TEXT,
                manufacturer TEXT,
                location    TEXT,
                assigned_to TEXT,
                status      TEXT NOT NULL DEFAULT 'active',
                notes       TEXT,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS maintenance_tasks (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                equipment_id    INTEGER NOT NULL REFERENCES equipment(id) ON DELETE CASCADE,
                title           TEXT NOT NULL,
                description     TEXT,
                task_type       TEXT NOT NULL DEFAULT 'scheduled',
                interval_days   INTEGER,
                last_done       TEXT,
                next_due        TEXT,
                status          TEXT NOT NULL DEFAULT 'pending',
                assigned_to     TEXT,
                completed_at    TEXT,
                completed_by    TEXT,
                notes           TEXT,
                created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS calibration_records (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                equipment_id    INTEGER NOT NULL REFERENCES equipment(id) ON DELETE CASCADE,
                calibrated_by   TEXT,
                calibrated_at   TEXT NOT NULL,
                next_due        TEXT,
                certificate_num TEXT,
                cert_file       TEXT,
                result          TEXT NOT NULL DEFAULT 'pass',
                notes           TEXT,
                created_at      TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS inventory_items (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                part_number TEXT,
                category    TEXT,
                location    TEXT,
                quantity    INTEGER NOT NULL DEFAULT 0,
                unit        TEXT NOT NULL DEFAULT 'ea',
                min_stock   INTEGER NOT NULL DEFAULT 0,
                unit_cost   REAL,
                supplier    TEXT,
                notes       TEXT,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS inventory_transactions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id     INTEGER NOT NULL REFERENCES inventory_items(id) ON DELETE CASCADE,
                action      TEXT NOT NULL,
                quantity    INTEGER NOT NULL,
                reference   TEXT,
                performed_by TEXT,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS equipment_attachments (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                equipment_id  INTEGER NOT NULL REFERENCES equipment(id) ON DELETE CASCADE,
                filename      TEXT NOT NULL,
                original_name TEXT NOT NULL,
                file_type     TEXT,
                file_size     INTEGER,
                created_at    TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS pmcs_templates (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                title        TEXT NOT NULL,
                description  TEXT,
                equipment_id INTEGER REFERENCES equipment(id) ON DELETE SET NULL,
                created_at   TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS pmcs_items (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                template_id  INTEGER NOT NULL REFERENCES pmcs_templates(id) ON DELETE CASCADE,
                item_no      TEXT,
                interval     TEXT NOT NULL DEFAULT 'B',
                check_item   TEXT NOT NULL,
                procedure    TEXT,
                not_ready_if TEXT,
                order_index  INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS pmcs_sessions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                template_id     INTEGER NOT NULL REFERENCES pmcs_templates(id) ON DELETE CASCADE,
                operator_name   TEXT,
                operator_rank   TEXT,
                status          TEXT NOT NULL DEFAULT 'in_progress',
                fault_count     INTEGER NOT NULL DEFAULT 0,
                archive_path    TEXT,
                notes           TEXT,
                started_at      TEXT NOT NULL DEFAULT (datetime('now')),
                completed_at    TEXT
            );

            CREATE TABLE IF NOT EXISTS pmcs_results (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  INTEGER NOT NULL REFERENCES pmcs_sessions(id) ON DELETE CASCADE,
                item_id     INTEGER NOT NULL REFERENCES pmcs_items(id) ON DELETE CASCADE,
                status      TEXT NOT NULL DEFAULT 'ok',
                notes       TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_pmcs_session ON pmcs_results(session_id);

            CREATE TABLE IF NOT EXISTS audit_log (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type  TEXT NOT NULL,
                entity_id    INTEGER NOT NULL,
                equipment_id INTEGER,
                action       TEXT NOT NULL,
                actor        TEXT,
                detail       TEXT,
                created_at   TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_audit_equipment ON audit_log(equipment_id);
            CREATE INDEX IF NOT EXISTS idx_audit_entity    ON audit_log(entity_type, entity_id);
        """)
        # Migrations — add columns that may not exist in older DBs
        existing = {row[1] async for row in await db.execute("PRAGMA table_info(equipment)")}
        if "assigned_to" not in existing:
            await db.execute("ALTER TABLE equipment ADD COLUMN assigned_to TEXT")

        await db.commit()
