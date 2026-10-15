"""
Workflow persistence — Supabase
===============================
R26-SE-008 | Bandara S M Y M | IT22277886

The same fourteen functions db/sqlite_repository.py provides, against Supabase
through PostgREST. Callers import db/workflow_repository.py and never learn
which of the two answered.

WHAT IS DIFFERENT FROM THE SQLITE VERSION, and why

  * NO GROUP BY. PostgREST does not aggregate, so plan_step_acceptance_stats()
    reads the step-level rows and counts them in Python. That is proportionate
    at this size — the table holds one row per plan step a developer decided
    on — and the docstring there names the Postgres function to add if it ever
    stops being.

  * UPSERT, not INSERT OR REPLACE. save_impact_records() upserts on the same
    (workflow_id, smell_id, model_version) triple the SQLite UNIQUE constraint
    names, so re-computing a workflow's impacts stays idempotent.

  * jsonb, not TEXT. The *_json columns are real jsonb, so PostgREST returns
    dicts where SQLite returned strings. Nothing outside db/ had to change for
    that: parse_json_field() already accepted both, which is what made this
    port a matter of adding a file rather than editing every caller.

  * NO CONNECTION LIFECYCLE. There is no get_db()/close_db() and nothing is
    bound to Flask's `g` — supabase-py holds one pooled HTTPS client, built on
    first use by db/supabase_client.py.

RUN db/schema_supabase.sql IN THE SUPABASE SQL EDITOR BEFORE POINTING A
DEPLOYMENT HERE. This module creates no tables: PostgREST cannot issue DDL, and
a service that silently created its own schema on startup would be the thing
that makes two environments drift.
"""

import json
from datetime import datetime, timezone

from db.supabase_client import get_supabase

__all__ = [
    "create_workflow", "get_workflow", "update_workflow", "list_workflows",
    "log_event", "get_audit_logs", "save_feedback", "export_feedback_dataset",
    "get_impact_records", "save_impact_records", "delete_impact_records",
    "recorded_plan_step_keys", "plan_step_acceptance_stats", "PLAN_STEP_ACTIONS",
    "parse_json_field", "now_iso",
]

WORKFLOWS = "workflows"
AUDIT_LOGS = "audit_logs"
FEEDBACK = "feedback_entries"
IMPACTS = "smell_impacts"

#: Same two step-level actions the SQLite side counts. Session-level rows
#: (plan_approved, plan_modified) stay excluded: one session-level approval is
#: not evidence about each of its twelve steps.
PLAN_STEP_ACTIONS = ("plan_step_accepted", "plan_step_rejected")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def parse_json_field(wf: dict, field: str):
    """Decode one of the workflow row's *_json columns.

    Identical contract to the SQLite version, and it has to be: jsonb comes
    back as a dict, but a row written by an older SQLite deployment and
    migrated across can still be a string.
    """
    val = wf.get(field)
    if not val:
        return None
    return json.loads(val) if isinstance(val, str) else val


def _rows(response):
    """The row list from a PostgREST response, never None."""
    return getattr(response, "data", None) or []


def _one(response):
    rows = _rows(response)
    return rows[0] if rows else None


# ---------- Workflow CRUD ----------

def create_workflow(wf_id, target, language, smells):
    stamp = now_iso()
    get_supabase().table(WORKFLOWS).insert({
        "id": wf_id,
        "target": target,
        "language": language,
        "status": "smell_review",
        "created_at": stamp,
        "updated_at": stamp,
        "smells_json": smells,
    }).execute()


def get_workflow(wf_id):
    response = (
        get_supabase().table(WORKFLOWS)
        .select("*").eq("id", wf_id).limit(1).execute()
    )
    return _one(response)


def update_workflow(wf_id, **kwargs):
    """Patch a workflow row.

    Callers pass already-serialised JSON strings for the *_json columns, because
    the SQLite side stored them as TEXT. Those are decoded back to objects here
    so the jsonb columns hold real JSON rather than a JSON string containing
    JSON — the difference between plan_json->>'plan_id' returning the id and
    returning null.
    """
    if not kwargs:
        return

    payload = {"updated_at": now_iso()}
    for key, value in kwargs.items():
        if key.endswith("_json") and isinstance(value, str):
            try:
                payload[key] = json.loads(value)
            except (ValueError, TypeError):
                payload[key] = value
        else:
            payload[key] = value

    get_supabase().table(WORKFLOWS).update(payload).eq("id", wf_id).execute()


def list_workflows():
    response = (
        get_supabase().table(WORKFLOWS)
        .select("*").order("created_at", desc=True).execute()
    )
    return _rows(response)


# ---------- Audit Log ----------

def log_event(workflow_id, stage, action, details=None, actor="developer"):
    get_supabase().table(AUDIT_LOGS).insert({
        "workflow_id": workflow_id,
        "stage": stage,
        "action": action,
        "actor": actor,
        "details_json": details or {},
        "timestamp": now_iso(),
    }).execute()


def get_audit_logs(workflow_id):
    response = (
        get_supabase().table(AUDIT_LOGS)
        .select("*").eq("workflow_id", workflow_id)
        .order("timestamp", desc=False).execute()
    )
    return _rows(response)


# ---------- Feedback ----------

def save_feedback(workflow_id, stage, action, smell_type=None, refactoring_type=None,
                  severity=None, reason=None, rating=None, accepted=False,
                  step_key=None):
    get_supabase().table(FEEDBACK).insert({
        "workflow_id": workflow_id,
        "stage": stage,
        "action": action,
        "smell_type": smell_type,
        "refactoring_type": refactoring_type,
        "severity": severity,
        "reason": reason,
        "rating": rating,
        # smallint 0/1, matching SQLite: domain/planning_recommendation.py sums
        # this column, and a boolean would change what that sum means.
        "accepted": 1 if accepted else 0,
        "step_key": step_key,
        "timestamp": now_iso(),
    }).execute()


def export_feedback_dataset():
    response = (
        get_supabase().table(FEEDBACK)
        .select("*").order("timestamp", desc=False).execute()
    )
    return _rows(response)


# ---------- Step-level plan feedback ----------

def recorded_plan_step_keys(workflow_id) -> set:
    """Step identities this workflow has already written a step-level row for.

    The frontend sends `modify` and then `approve` for the same review, so
    without this the rejections recorded during modify would be written again
    during approval and every rejected step would count twice.
    """
    response = (
        get_supabase().table(FEEDBACK)
        .select("step_key")
        .eq("workflow_id", workflow_id)
        .in_("action", list(PLAN_STEP_ACTIONS))
        .not_.is_("step_key", "null")
        .execute()
    )
    return {row["step_key"] for row in _rows(response) if row.get("step_key")}


def plan_step_acceptance_stats():
    """Real acceptance counts per (smell_type, refactoring_type), all workflows.

    AGGREGATED IN PYTHON. PostgREST has no GROUP BY, so the step-level rows are
    fetched and counted here. That is proportionate: the table holds one row
    per plan step a developer actually decided on — tens to low thousands over
    a project's life — and it is read once per plan render.

    If it ever stops being proportionate, the fix is a Postgres function doing
    the GROUP BY server-side, called through .rpc("plan_step_acceptance").
    The return shape below is what that function would have to produce.

    Only genuine step-level decisions are counted. Synthetic rows produced by
    feedback_model/train_feedback_model.py never enter this table, and the
    session-level actions are filtered out above, so what comes back is
    developer behaviour and nothing else.

    Returns {"pairs": {(smell_type, refactoring): {...}}, "prior": float|None,
             "observations": int, "accepted": int}.
    """
    response = (
        get_supabase().table(FEEDBACK)
        .select("smell_type,refactoring_type,accepted")
        .in_("action", list(PLAN_STEP_ACTIONS))
        .execute()
    )

    pairs = {}
    total_observations = 0
    total_accepted = 0

    for row in _rows(response):
        key = (row.get("smell_type"), row.get("refactoring_type"))
        accepted = 1 if row.get("accepted") in (1, True, "1", "true") else 0
        bucket = pairs.setdefault(key, {"observations": 0, "accepted": 0})
        bucket["observations"] += 1
        bucket["accepted"] += accepted
        total_observations += 1
        total_accepted += accepted

    return {
        "pairs": pairs,
        # The global rate is the prior each pair is smoothed toward, so a
        # sparse pair leans on this developer's overall behaviour rather than
        # on a hard-coded 50%.
        "prior": (total_accepted / total_observations) if total_observations else None,
        "observations": total_observations,
        "accepted": total_accepted,
    }


# ---------- Selection Impact Records ----------

def get_impact_records(workflow_id, model_version=None):
    """Cached per-smell impact records for a workflow.

    Filtering by model_version is what keeps a stale record from a previous
    model revision being served as if it were current.
    """
    query = (
        get_supabase().table(IMPACTS)
        .select("record_json").eq("workflow_id", workflow_id)
    )
    if model_version:
        query = query.eq("model_version", model_version)

    rows = _rows(query.order("id", desc=False).execute())
    return [
        json.loads(r["record_json"]) if isinstance(r["record_json"], str) else r["record_json"]
        for r in rows
        if r.get("record_json")
    ]


def save_impact_records(workflow_id, records):
    """Persist a workflow's impact records. Idempotent per (workflow, smell, model).

    `on_conflict` names the same triple the table's UNIQUE constraint does, so
    re-computing a workflow's impacts overwrites its own rows instead of
    accumulating a second copy of every one of them.
    """
    if not records:
        return 0

    stamp = now_iso()
    get_supabase().table(IMPACTS).upsert(
        [
            {
                "workflow_id": workflow_id,
                "smell_id": r.get("smell_id"),
                "model_version": r.get("model_version"),
                "record_json": r,
                "computed_at": stamp,
            }
            for r in records
        ],
        on_conflict="workflow_id,smell_id,model_version",
    ).execute()
    return len(records)


def delete_impact_records(workflow_id):
    """Drop a workflow's cached records — used when its smell list changes."""
    get_supabase().table(IMPACTS).delete().eq("workflow_id", workflow_id).execute()
