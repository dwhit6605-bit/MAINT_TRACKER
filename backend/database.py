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

            CREATE TABLE IF NOT EXISTS users (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                username        TEXT NOT NULL UNIQUE,
                hashed_password TEXT NOT NULL,
                role            TEXT NOT NULL DEFAULT 'operator',
                created_at      TEXT NOT NULL DEFAULT (datetime('now')),
                last_login      TEXT
            );

            CREATE TABLE IF NOT EXISTS task_parts_used (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id       INTEGER NOT NULL REFERENCES maintenance_tasks(id) ON DELETE CASCADE,
                item_id       INTEGER NOT NULL REFERENCES inventory_items(id),
                quantity_used REAL NOT NULL DEFAULT 1,
                notes         TEXT,
                created_at    TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_task_parts ON task_parts_used(task_id);

            CREATE TABLE IF NOT EXISTS skos (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                nsn         TEXT,
                description TEXT,
                status      TEXT NOT NULL DEFAULT 'complete',
                notes       TEXT,
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS sko_equipment (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                sko_id       INTEGER NOT NULL REFERENCES skos(id) ON DELETE CASCADE,
                equipment_id INTEGER NOT NULL REFERENCES equipment(id) ON DELETE CASCADE,
                UNIQUE(sko_id, equipment_id)
            );
            CREATE INDEX IF NOT EXISTS idx_sko_equipment ON sko_equipment(sko_id);

            CREATE TABLE IF NOT EXISTS sko_parts_used (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                sko_id     INTEGER NOT NULL REFERENCES skos(id) ON DELETE CASCADE,
                item_id    INTEGER NOT NULL REFERENCES inventory_items(id),
                quantity   REAL NOT NULL DEFAULT 1,
                used_by    TEXT,
                notes      TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_sko_parts ON sko_parts_used(sko_id);

            CREATE TABLE IF NOT EXISTS sko_checkouts (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                sko_id          INTEGER NOT NULL REFERENCES skos(id) ON DELETE CASCADE,
                checked_out_by  TEXT NOT NULL,
                checkout_date   TEXT NOT NULL DEFAULT (datetime('now')),
                expected_return TEXT,
                returned_at     TEXT,
                notes           TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_sko_checkouts ON sko_checkouts(sko_id);

            CREATE TABLE IF NOT EXISTS app_settings (
                key        TEXT PRIMARY KEY,
                value      TEXT,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS rolling_stock (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                year         TEXT,
                make         TEXT NOT NULL,
                model        TEXT NOT NULL,
                tag_number   TEXT,
                key_number   TEXT,
                license_plate TEXT,
                vin          TEXT,
                color        TEXT,
                status       TEXT NOT NULL DEFAULT 'available',
                notes        TEXT,
                created_at   TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS vehicle_inspections (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                vehicle_id        INTEGER NOT NULL REFERENCES rolling_stock(id) ON DELETE CASCADE,
                date_out          TEXT NOT NULL DEFAULT (date('now')),
                date_in           TEXT,
                beginning_mileage INTEGER,
                ending_mileage    INTEGER,
                operator_name     TEXT,
                operator_phone    TEXT,
                dispatcher_name   TEXT,
                accident_card     INTEGER NOT NULL DEFAULT 0,
                results           TEXT NOT NULL DEFAULT '{}',
                remarks           TEXT NOT NULL DEFAULT '{}',
                notes             TEXT,
                status            TEXT NOT NULL DEFAULT 'open',
                created_at        TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_insp_vehicle ON vehicle_inspections(vehicle_id);

            CREATE TABLE IF NOT EXISTS reorder_requests (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id      INTEGER NOT NULL REFERENCES inventory_items(id) ON DELETE CASCADE,
                qty_requested INTEGER NOT NULL DEFAULT 1,
                requested_by TEXT,
                supplier     TEXT,
                notes        TEXT,
                status       TEXT NOT NULL DEFAULT 'pending',
                created_at   TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_reorder_item ON reorder_requests(item_id);

            CREATE TABLE IF NOT EXISTS task_attachments (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id       INTEGER NOT NULL REFERENCES maintenance_tasks(id) ON DELETE CASCADE,
                filename      TEXT NOT NULL,
                original_name TEXT NOT NULL,
                file_type     TEXT,
                file_size     INTEGER,
                created_at    TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_task_att_task ON task_attachments(task_id);

            CREATE TABLE IF NOT EXISTS fault_reports (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                equipment_id INTEGER NOT NULL REFERENCES equipment(id) ON DELETE CASCADE,
                reported_by  TEXT NOT NULL,
                severity     TEXT NOT NULL DEFAULT 'routine',
                title        TEXT NOT NULL,
                description  TEXT,
                status       TEXT NOT NULL DEFAULT 'open',
                resolved_by  TEXT,
                resolved_at  TEXT,
                resolution   TEXT,
                created_at   TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_fault_equipment ON fault_reports(equipment_id);
            CREATE INDEX IF NOT EXISTS idx_fault_status ON fault_reports(status);
        """)
        # Migrations — add columns that may not exist in older DBs
        eq_cols = {row[1] async for row in await db.execute("PRAGMA table_info(equipment)")}
        if "assigned_to" not in eq_cols:
            await db.execute("ALTER TABLE equipment ADD COLUMN assigned_to TEXT")
        if "purchase_date" not in eq_cols:
            await db.execute("ALTER TABLE equipment ADD COLUMN purchase_date TEXT")
        if "warranty_expiry" not in eq_cols:
            await db.execute("ALTER TABLE equipment ADD COLUMN warranty_expiry TEXT")
        if "end_of_life_date" not in eq_cols:
            await db.execute("ALTER TABLE equipment ADD COLUMN end_of_life_date TEXT")

        await db.commit()
