"""
Database layer – SQLite for prototype.
Replace ENGINE_URL with PostgreSQL DSN for production.
"""

import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path
from flask import g


DB_PATH = Path(__file__).parent.parent / "diwo_audit.db"


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


# ---------- Workflow CRUD ----------

def create_workflow(wf_id, target, language, smells):
    db = get_db()
    db.execute(
        """INSERT INTO workflows
           (id, target, language, status, created_at, updated_at, smells_json)
           VALUES (?,?,?,?,?,?,?)""",
        (wf_id, target, language, "smell_review", now_iso(), now_iso(), json.dumps(smells))
    )
    db.commit()


def get_workflow(wf_id):
    db = get_db()
    row = db.execute("SELECT * FROM workflows WHERE id=?", (wf_id,)).fetchone()
    if row is None:
        return None
    return dict(row)


def update_workflow(wf_id, **kwargs):
    db = get_db()
    kwargs["updated_at"] = now_iso()
    set_clause = ", ".join(f"{k}=?" for k in kwargs)
    values = list(kwargs.values()) + [wf_id]
    db.execute(f"UPDATE workflows SET {set_clause} WHERE id=?", values)
    db.commit()


def list_workflows():
    db = get_db()
    rows = db.execute("SELECT * FROM workflows ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


# ---------- Audit Log ----------

def log_event(workflow_id, stage, action, details=None, actor="developer"):
    db = get_db()
    db.execute(
        """INSERT INTO audit_logs (workflow_id, stage, action, actor, details_json, timestamp)
           VALUES (?,?,?,?,?,?)""",
        (workflow_id, stage, action, actor, json.dumps(details or {}), now_iso())
    )
    db.commit()


def get_audit_logs(workflow_id):
    db = get_db()
    rows = db.execute(
        "SELECT * FROM audit_logs WHERE workflow_id=? ORDER BY timestamp ASC",
        (workflow_id,)
    ).fetchall()
    return [dict(r) for r in rows]


# ---------- Feedback ----------

def save_feedback(workflow_id, stage, action, smell_type=None, refactoring_type=None,
                  severity=None, reason=None, rating=None, accepted=False):
    db = get_db()
    db.execute(
        """INSERT INTO feedback_entries
           (workflow_id, stage, action, smell_type, refactoring_type, severity, reason, rating, accepted, timestamp)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (workflow_id, stage, action, smell_type, refactoring_type, severity,
         reason, rating, 1 if accepted else 0, now_iso())
    )
    db.commit()


def export_feedback_dataset():
    db = get_db()
    rows = db.execute("SELECT * FROM feedback_entries ORDER BY timestamp ASC").fetchall()
    return [dict(r) for r in rows]
