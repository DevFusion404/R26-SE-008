"""
Database connection and schema
==============================
R26-SE-008 | Bandara S M Y M | IT22277886

SQLite for the prototype. This module owns the SQLite connection, its schema
and its migrations — persistence of workflows, audit events and feedback lives
next door, behind db/workflow_repository.py.

SUPABASE DEPLOYMENTS SKIP ALL OF IT. When SUPABASE_URL and a key are set,
db/workflow_repository.py dispatches to db/supabase_repository.py and nothing
here is ever called; init_db() then creates no file, because a SQLite database
sitting unused beside a live Supabase project is the thing someone eventually
mistakes for the real data. The Supabase schema is db/schema_supabase.sql, run
by hand in the SQL editor — PostgREST cannot issue DDL.

The database file now sits under runtime/database/, resolved by config so
generated data is never mixed with source. An older backend/diwo_audit.db is
moved into place on first use, so no existing workflow history is lost.
"""

import sqlite3
from datetime import datetime, timezone
from flask import g

import config
from config import database_path

#: Resolved once at import so every helper agrees on the file, and the
#: legacy-path migration runs exactly once per process. Left unresolved on a
#: Supabase deployment: database_path() creates runtime/database/ as a side
#: effect, and a directory that exists implies a database that does not.
DB_PATH = None if config.uses_supabase() else database_path()


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
    """Create the SQLite schema, unless this deployment persists to Supabase."""
    if config.uses_supabase():
        # One cheap read at boot. Without it a bad URL or a publishable key
        # surfaces as a 500 on every request, or as reads that quietly return
        # nothing — which reaches the developer as "the button does nothing",
        # several screens from the cause.
        from db.supabase_client import startup_check
        print(f"[DIWO] Persistence: Supabase — SQLite schema not created.")
        print(f"[DIWO] {startup_check()}")
        return

    reason = config.supabase_misconfigured()
    if reason:
        print(f"[DIWO] {reason}")

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

        -- Per-smell Selection Impact Records (domain/impact_model.py).
        -- Keyed by model_version so records from different model revisions stay
        -- distinguishable rather than overwriting each other - that is what
        -- makes a later before/after comparison of model accuracy possible.
        CREATE TABLE IF NOT EXISTS smell_impacts (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            workflow_id   TEXT NOT NULL,
            smell_id      TEXT NOT NULL,
            model_version TEXT NOT NULL,
            record_json   TEXT NOT NULL,
            computed_at   TEXT NOT NULL,
            UNIQUE(workflow_id, smell_id, model_version)
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

        # Stage 2 writes one feedback row per plan step the developer decided
        # on. The frontend can send `modify` and then `approve` for the same
        # review, so the same rejection would otherwise be counted twice and
        # skew the acceptance statistics the recommendation engine reads back.
        # step_key is the step's identity (smell_id|refactoring|file), which
        # makes "have we already recorded this decision?" one indexed lookup.
        feedback_cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(feedback_entries)").fetchall()
        }
        if "step_key" not in feedback_cols:
            conn.execute("ALTER TABLE feedback_entries ADD COLUMN step_key TEXT")
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_feedback_step
               ON feedback_entries (workflow_id, action, step_key)"""
        )

    print(f"[DIWO] Database initialized (SQLite: {DB_PATH}).")


def now_iso():
    return datetime.now(timezone.utc).isoformat()
