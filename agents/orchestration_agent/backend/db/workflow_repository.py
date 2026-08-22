"""
Workflow persistence
====================
R26-SE-008 | Bandara S M Y M | IT22277886

Every SQL statement the DIWO workflow needs: the workflow rows themselves, the
audit trail, and the feedback rows the ML Feedback Manager trains on. Route
handlers never touch SQL — they go through the services, which come here.

Moved out of db/database.py unchanged; the schema is untouched.
"""

import json

from db.database import get_db, now_iso

__all__ = [
    "create_workflow", "get_workflow", "update_workflow", "list_workflows",
    "log_event", "get_audit_logs", "save_feedback", "export_feedback_dataset",
    "get_impact_records", "save_impact_records", "delete_impact_records",
    "parse_json_field", "now_iso",
]


def parse_json_field(wf: dict, field: str):
    """Decode one of the workflow row's *_json columns.

    Returns None for an empty column, so callers can `or []` / `or {}` it.
    """
    val = wf.get(field)
    if not val:
        return None
    return json.loads(val) if isinstance(val, str) else val


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


# ---------- Selection Impact Records ----------

def get_impact_records(workflow_id, model_version=None):
    """Cached per-smell impact records for a workflow, newest model first.

    Filtering by model_version is what keeps a stale record from a previous
    model revision being served as if it were current.
    """
    db = get_db()
    if model_version:
        rows = db.execute(
            """SELECT record_json FROM smell_impacts
               WHERE workflow_id=? AND model_version=? ORDER BY id ASC""",
            (workflow_id, model_version),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT record_json FROM smell_impacts WHERE workflow_id=? ORDER BY id ASC",
            (workflow_id,),
        ).fetchall()

    return [json.loads(r["record_json"]) for r in rows]


def save_impact_records(workflow_id, records):
    """Persist a workflow's impact records. Idempotent per (workflow, smell, model)."""
    if not records:
        return 0

    db = get_db()
    stamp = now_iso()
    db.executemany(
        """INSERT OR REPLACE INTO smell_impacts
           (workflow_id, smell_id, model_version, record_json, computed_at)
           VALUES (?,?,?,?,?)""",
        [
            (workflow_id, r.get("smell_id"), r.get("model_version"),
             json.dumps(r), stamp)
            for r in records
        ],
    )
    db.commit()
    return len(records)


def delete_impact_records(workflow_id):
    """Drop a workflow's cached records — used when its smell list changes."""
    db = get_db()
    db.execute("DELETE FROM smell_impacts WHERE workflow_id=?", (workflow_id,))
    db.commit()
