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

            CREATE TABLE IF NOT EXISTS power_assets (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_type    TEXT NOT NULL DEFAULT 'generator',
                name          TEXT NOT NULL,
                make          TEXT,
                model         TEXT,
                serial_num    TEXT,
                rating        TEXT,
                fuel_type     TEXT,
                location      TEXT,
                portable      INTEGER NOT NULL DEFAULT 0,
                hour_meter    REAL NOT NULL DEFAULT 0,
                service_interval_hours REAL,
                last_service_hours     REAL NOT NULL DEFAULT 0,
                status        TEXT NOT NULL DEFAULT 'available',
                equipment_id  INTEGER REFERENCES equipment(id) ON DELETE SET NULL,
                notes         TEXT,
                created_at    TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS power_asset_logs (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_id       INTEGER NOT NULL REFERENCES power_assets(id) ON DELETE CASCADE,
                log_type       TEXT NOT NULL DEFAULT 'run',
                checklist_type TEXT NOT NULL DEFAULT 'gen_diesel',
                date_out       TEXT NOT NULL DEFAULT (date('now')),
                date_in        TEXT,
                hours_start    REAL,
                hours_end      REAL,
                operator_name  TEXT,
                operator_phone TEXT,
                dispatcher_name TEXT,
                destination    TEXT,
                results        TEXT NOT NULL DEFAULT '{}',
                remarks        TEXT NOT NULL DEFAULT '{}',
                notes          TEXT,
                status         TEXT NOT NULL DEFAULT 'open',
                created_at     TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_palog_asset ON power_asset_logs(asset_id);

            CREATE TABLE IF NOT EXISTS cylinder_tests (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                equipment_id INTEGER NOT NULL REFERENCES equipment(id) ON DELETE CASCADE,
                test_type    TEXT NOT NULL DEFAULT 'hydrostatic',
                tested_at    TEXT NOT NULL,
                next_due     TEXT,
                result       TEXT NOT NULL DEFAULT 'pass',
                facility     TEXT,
                rin          TEXT,
                notes        TEXT,
                created_at   TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_cyltest_eq ON cylinder_tests(equipment_id);

            CREATE TABLE IF NOT EXISTS supcen_catalog (
                id             INTEGER PRIMARY KEY,
                nom            TEXT NOT NULL,
                stock_number   TEXT,
                nsn            TEXT,
                mcn            TEXT,
                lin            TEXT,
                aesip          TEXT,
                unit_price     REAL,
                unit_issue     TEXT,
                unit_amt       TEXT,
                weight         TEXT,
                end_item       TEXT,
                orgs           TEXT,
                shelf_life     TEXT,
                classification TEXT,
                material_code  TEXT,
                certification  TEXT,
                remarks        TEXT,
                other          TEXT,
                cats           TEXT,
                image          TEXT,
                search_blob    TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_supcen_nom     ON supcen_catalog(nom);
            CREATE INDEX IF NOT EXISTS idx_supcen_enditem ON supcen_catalog(end_item);
            CREATE INDEX IF NOT EXISTS idx_supcen_nsn     ON supcen_catalog(nsn);
            CREATE INDEX IF NOT EXISTS idx_supcen_mcn     ON supcen_catalog(mcn);

            -- Multi-location inventory.
            -- inventory_stock is the source of truth per bin; inventory_items.quantity
            -- is a rollup of SUM(stock) kept current by the triggers below, so the six
            -- existing modules that read .quantity keep working unchanged.
            CREATE TABLE IF NOT EXISTS inventory_locations (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                code       TEXT NOT NULL UNIQUE,
                name       TEXT,
                zone       TEXT,
                active     INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS inventory_stock (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id     INTEGER NOT NULL REFERENCES inventory_items(id) ON DELETE CASCADE,
                location_id INTEGER NOT NULL REFERENCES inventory_locations(id) ON DELETE CASCADE,
                quantity    INTEGER NOT NULL DEFAULT 0,
                updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(item_id, location_id)
            );
            CREATE INDEX IF NOT EXISTS idx_invstock_item ON inventory_stock(item_id);
            CREATE INDEX IF NOT EXISTS idx_invstock_loc  ON inventory_stock(location_id);

            CREATE TRIGGER IF NOT EXISTS trg_invstock_ai AFTER INSERT ON inventory_stock
            BEGIN
                UPDATE inventory_items SET quantity =
                    (SELECT COALESCE(SUM(quantity),0) FROM inventory_stock WHERE item_id=NEW.item_id)
                WHERE id = NEW.item_id;
            END;

            CREATE TRIGGER IF NOT EXISTS trg_invstock_au AFTER UPDATE ON inventory_stock
            BEGIN
                UPDATE inventory_items SET quantity =
                    (SELECT COALESCE(SUM(quantity),0) FROM inventory_stock WHERE item_id=NEW.item_id)
                WHERE id = NEW.item_id;
                UPDATE inventory_items SET quantity =
                    (SELECT COALESCE(SUM(quantity),0) FROM inventory_stock WHERE item_id=OLD.item_id)
                WHERE id = OLD.item_id AND OLD.item_id <> NEW.item_id;
            END;

            CREATE TRIGGER IF NOT EXISTS trg_invstock_ad AFTER DELETE ON inventory_stock
            BEGIN
                UPDATE inventory_items SET quantity =
                    (SELECT COALESCE(SUM(quantity),0) FROM inventory_stock WHERE item_id=OLD.item_id)
                WHERE id = OLD.item_id;
            END;

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

            CREATE TABLE IF NOT EXISTS pmcs_template_equipment (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                template_id  INTEGER NOT NULL REFERENCES pmcs_templates(id) ON DELETE CASCADE,
                equipment_id INTEGER NOT NULL REFERENCES equipment(id) ON DELETE CASCADE,
                order_index  INTEGER DEFAULT 0,
                UNIQUE(template_id, equipment_id)
            );
            CREATE INDEX IF NOT EXISTS idx_pmcs_tmpl_eq ON pmcs_template_equipment(template_id);

            CREATE TABLE IF NOT EXISTS equipment_type_checklists (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                equipment_name TEXT NOT NULL UNIQUE,
                created_at     TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS equipment_type_checklist_steps (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                checklist_id INTEGER NOT NULL REFERENCES equipment_type_checklists(id) ON DELETE CASCADE,
                step_no      TEXT,
                interval     TEXT NOT NULL DEFAULT 'B',
                title        TEXT NOT NULL,
                procedure    TEXT,
                order_index  INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_eq_type_steps ON equipment_type_checklist_steps(checklist_id);
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
        if "out_for" not in eq_cols:
            await db.execute("ALTER TABLE equipment ADD COLUMN out_for TEXT")
        if "out_since" not in eq_cols:
            await db.execute("ALTER TABLE equipment ADD COLUMN out_since TEXT")
        if "expected_return" not in eq_cols:
            await db.execute("ALTER TABLE equipment ADD COLUMN expected_return TEXT")
        if "reference_url" not in eq_cols:
            await db.execute("ALTER TABLE equipment ADD COLUMN reference_url TEXT")
        # Pressure cylinders (SCBA / O2 bottles) — DOT requalification tracking
        if "mfg_date" not in eq_cols:
            await db.execute("ALTER TABLE equipment ADD COLUMN mfg_date TEXT")
        if "cylinder_type" not in eq_cols:
            await db.execute("ALTER TABLE equipment ADD COLUMN cylinder_type TEXT")
        if "hydro_interval_months" not in eq_cols:
            await db.execute("ALTER TABLE equipment ADD COLUMN hydro_interval_months INTEGER")
        if "service_life_years" not in eq_cols:
            await db.execute("ALTER TABLE equipment ADD COLUMN service_life_years INTEGER")
        # Read off the cylinder stamp rather than derived — requalification
        # intervals vary by spec, so the stamped date is the authority
        if "hydro_due_date" not in eq_cols:
            await db.execute("ALTER TABLE equipment ADD COLUMN hydro_due_date TEXT")

        pmcs_item_cols = {row[1] async for row in await db.execute("PRAGMA table_info(pmcs_items)")}
        if "equipment_id" not in pmcs_item_cols:
            await db.execute("ALTER TABLE pmcs_items ADD COLUMN equipment_id INTEGER REFERENCES equipment(id) ON DELETE SET NULL")
        if "creates_task" not in pmcs_item_cols:
            await db.execute("ALTER TABLE pmcs_items ADD COLUMN creates_task INTEGER NOT NULL DEFAULT 0")

        fault_cols = {row[1] async for row in await db.execute("PRAGMA table_info(fault_reports)")}
        if "linked_task_id" not in fault_cols:
            await db.execute("ALTER TABLE fault_reports ADD COLUMN linked_task_id INTEGER REFERENCES maintenance_tasks(id) ON DELETE SET NULL")

        tx_cols = {row[1] async for row in await db.execute("PRAGMA table_info(inventory_transactions)")}
        if "location_id" not in tx_cols:
            await db.execute("ALTER TABLE inventory_transactions ADD COLUMN location_id INTEGER REFERENCES inventory_locations(id) ON DELETE SET NULL")
        if "to_location_id" not in tx_cols:
            await db.execute("ALTER TABLE inventory_transactions ADD COLUMN to_location_id INTEGER REFERENCES inventory_locations(id) ON DELETE SET NULL")

        task_cols = {row[1] async for row in await db.execute("PRAGMA table_info(maintenance_tasks)")}
        if "source_fault_id" not in task_cols:
            await db.execute("ALTER TABLE maintenance_tasks ADD COLUMN source_fault_id INTEGER REFERENCES fault_reports(id) ON DELETE SET NULL")

        # Hazmat suit tracking
        await db.execute("""
            CREATE TABLE IF NOT EXISTS hazmat_suits (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                suit_type        TEXT NOT NULL,
                model            TEXT,
                size             TEXT NOT NULL,
                serial_num       TEXT,
                manufacture_date TEXT,
                shelf_life_years REAL,
                expiry_date      TEXT,
                status           TEXT NOT NULL DEFAULT 'serviceable',
                assigned_to      TEXT,
                assigned_date    TEXT,
                notes            TEXT,
                created_at       TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS hazmat_suit_tests (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                suit_id     INTEGER NOT NULL REFERENCES hazmat_suits(id) ON DELETE CASCADE,
                tested_date TEXT NOT NULL,
                tested_by   TEXT,
                result      TEXT NOT NULL DEFAULT 'pass',
                next_due    TEXT,
                notes       TEXT,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_hazmat_tests ON hazmat_suit_tests(suit_id)")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS hazmat_suit_assignments (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                suit_id       INTEGER NOT NULL REFERENCES hazmat_suits(id) ON DELETE CASCADE,
                assigned_to   TEXT NOT NULL,
                issued_date   TEXT NOT NULL,
                returned_date TEXT,
                notes         TEXT
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_hazmat_assign ON hazmat_suit_assignments(suit_id)")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS hazmat_paper_stock (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                size       TEXT NOT NULL UNIQUE,
                quantity   INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        # Seed default paper stock sizes
        for sz in ('XS', 'S', 'M', 'L', 'XL', 'XXL'):
            await db.execute(
                "INSERT OR IGNORE INTO hazmat_paper_stock (size, quantity) VALUES (?, 0)", (sz,)
            )

        # Level B and specialty suits held as bulk stock rather than serialized.
        # Unlike paper these carry a manufacturer and an expiry, and the same
        # model can sit on the shelf as several lots with different dates.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS hazmat_bulk_stock (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                category     TEXT NOT NULL DEFAULT 'level_b',
                manufacturer TEXT,
                model        TEXT,
                size         TEXT NOT NULL,
                quantity     INTEGER NOT NULL DEFAULT 0,
                mfg_date     TEXT,
                expiry_date  TEXT,
                lot_number   TEXT,
                location     TEXT,
                notes        TEXT,
                created_at   TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        # COALESCE rather than a plain UNIQUE: SQLite treats every NULL as
        # distinct, which would let duplicate lines through on nullable columns
        await db.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_hazbulk_uniq ON hazmat_bulk_stock(
                category, COALESCE(manufacturer,''), COALESCE(model,''),
                size, COALESCE(lot_number,''))
        """)

        await _seed_supcen_catalog(db)

        await db.commit()


async def _seed_supcen_catalog(db):
    """Load the CoMSupCen catalog once. Skipped when already populated, so a
    redeploy is cheap and any local edits survive."""
    import json
    from pathlib import Path
    async with db.execute("SELECT COUNT(*) FROM supcen_catalog") as cur:
        if (await cur.fetchone())[0]:
            return
    src = Path(__file__).resolve().parent / "data" / "supcen_catalog.json"
    if not src.exists():
        return
    items = json.loads(src.read_text())
    rows = []
    for it in items:
        cats = it.get("cats") or []
        # One denormalised column so a token search is a single LIKE per term
        blob = " ".join(str(x) for x in [
            it.get("nom"), it.get("nsn"), it.get("mcn"), it.get("stock_number"),
            it.get("lin"), it.get("end_item"), it.get("orgs"),
            it.get("classification"), it.get("remarks"), " ".join(cats),
        ] if x).upper()
        rows.append((
            it["id"], it.get("nom", ""), it.get("stock_number"), it.get("nsn"),
            it.get("mcn"), it.get("lin"), it.get("aesip"), it.get("unit_price"),
            it.get("unit_issue"), it.get("unit_amt"), it.get("weight"),
            it.get("end_item"), it.get("orgs"), it.get("shelf_life"),
            it.get("classification"), it.get("material_code"), it.get("certification"),
            it.get("remarks"), it.get("other"), json.dumps(cats), it.get("image"), blob,
        ))
    await db.executemany(
        "INSERT OR IGNORE INTO supcen_catalog VALUES (" + ",".join("?" * 22) + ")", rows
    )
