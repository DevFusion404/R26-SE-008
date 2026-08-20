"""
Database connection and schema
==============================
R26-SE-008 | Bandara S M Y M | IT22277886

SQLite for the prototype; replace the connection with a PostgreSQL DSN for
production. This module owns the connection, the schema and its migrations —
persistence of workflows, audit events and feedback lives next door in
workflow_repository.py.

The database file now sits under runtime/database/, resolved by config so
generated data is never mixed with source. An older backend/diwo_audit.db is
moved into place on first use, so no existing workflow history is lost.
"""

import sqlite3
from datetime import datetime, timezone
from flask import g

from config import database_path

#: Resolved once at import so every helper agrees on the file, and the
#: legacy-path migration runs exactly once per process.
DB_PATH = database_path()


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(str(DB_PATH), detect_types=sqlite3.PARSE_DECLTYPES)
        g.db.row_factory = sqlite3.Row
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(app):
    app.teardown_appcontext(close_db)
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS workflows (
            id          TEXT PRIMARY KEY,
            target      TEXT NOT NULL,
            language    TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'smell_review',
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL,
            smells_json TEXT,
            selected_smells_json TEXT,
            plan_json   TEXT,
            transformation_result_json TEXT,
            metrics_before_json TEXT,
            metrics_after_json  TEXT
        );

        CREATE TABLE IF NOT EXISTS audit_logs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            workflow_id TEXT NOT NULL,
            stage       TEXT NOT NULL,
            action      TEXT NOT NULL,
            actor       TEXT NOT NULL DEFAULT 'developer',
            details_json TEXT,
            timestamp   TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS feedback_entries (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            workflow_id         TEXT NOT NULL,
            stage               TEXT NOT NULL,
            action              TEXT NOT NULL,
            smell_type          TEXT,
            refactoring_type    TEXT,
            severity            TEXT,
            reason              TEXT,
            rating              INTEGER,
            accepted            INTEGER NOT NULL DEFAULT 0,
            timestamp           TEXT NOT NULL
        );
        """)

        # Lightweight schema migration for older prototype databases.
        # Keep this idempotent so startup is safe across restarts.
        existing_cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(workflows)").fetchall()
        }
        if "updated_smells_json" not in existing_cols:
            conn.execute("ALTER TABLE workflows ADD COLUMN updated_smells_json TEXT")
        if "planning_input_json" not in existing_cols:
            conn.execute("ALTER TABLE workflows ADD COLUMN planning_input_json TEXT")
        # The CUQA quality report as served by POST /api/cuqa/quality-report.
        # Kept verbatim so the updated report handed to the RDP agent can be a
        # filtered copy of it — same shape, same fields — instead of being
        # rebuilt from the flattened smell list, which loses per-file metrics,
        # quality_score and each smell's own fields (entity, start_line, ...).
        if "cuqa_report_json" not in existing_cols:
            conn.execute("ALTER TABLE workflows ADD COLUMN cuqa_report_json TEXT")
        # The plan exactly as the RDP agent produced it, before approval
        # reduced plan_json to the approved steps. Rolling back from the
        # transformation stage restores it, so a step rejected on the first
        # pass can be approved on the second — without it, going back would
        # show only the steps that were already approved.
        if "plan_full_json" not in existing_cols:
            conn.execute("ALTER TABLE workflows ADD COLUMN plan_full_json TEXT")

    print("[DIWO] Database initialized.")


def now_iso():
    return datetime.now(timezone.utc).isoformat()
