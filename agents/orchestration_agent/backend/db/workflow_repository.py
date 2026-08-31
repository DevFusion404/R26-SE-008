"""
Workflow persistence — the seam
===============================
R26-SE-008 | Bandara S M Y M | IT22277886

Every persistence call in this service comes through here, and this module
decides which backend answers it:

    SUPABASE_URL + SUPABASE_KEY set  ->  db/supabase_repository.py
    otherwise                        ->  db/sqlite_repository.py

Nothing outside db/ knows which one is running. Services and routes import the
same fourteen names they always have, so switching a deployment to Supabase is
two environment variables and a schema, not a change to any caller.

DISPATCHED PER CALL, not bound at import. config reads os.environ at import
time and db/supabase_client.py calls load_dotenv() when it is first touched, so
a decision frozen at import would be made before the .env file had been read.
Resolving on each call also means a test can point the process at Supabase and
back without reimporting the module tree. The cost is one dict lookup per query,
against an HTTPS round trip or a SQLite write.

FALLBACK IS DELIBERATE AND LOUD. If Supabase is asked for but unreachable —
missing package, missing credentials — the first call raises rather than
quietly writing to a local SQLite file that nobody will think to look in. A
half-persisted audit trail is worse than a failure, because it looks complete.
"""

import logging

import config
from db.supabase_client import SupabaseUnavailable

logger = logging.getLogger("diwo.repository")

__all__ = [
    "create_workflow", "get_workflow", "update_workflow", "list_workflows",
    "log_event", "get_audit_logs", "save_feedback", "export_feedback_dataset",
    "get_impact_records", "save_impact_records", "delete_impact_records",
    "recorded_plan_step_keys", "plan_step_acceptance_stats", "PLAN_STEP_ACTIONS",
    "parse_json_field", "now_iso", "active_backend",
]

_warned_misconfigured = False


def _backend():
    """The module that will answer this call.

    The import is inside the branch on purpose: a SQLite deployment must not
    need the supabase package installed to start, and this is the only place
    that can hold that guarantee.
    """
    global _warned_misconfigured

    if config.uses_supabase():
        from db import supabase_repository
        return supabase_repository

    reason = config.supabase_misconfigured()
    if reason and not _warned_misconfigured:
        _warned_misconfigured = True
        logger.warning("[DIWO] %s", reason)

    from db import sqlite_repository
    return sqlite_repository


def active_backend() -> str:
    """"supabase" or "sqlite" — for /api/health and the startup banner."""
    return config.database_backend()


# ---------- Workflow CRUD ----------

def create_workflow(wf_id, target, language, smells):
    return _backend().create_workflow(wf_id, target, language, smells)


def get_workflow(wf_id):
    return _backend().get_workflow(wf_id)


def update_workflow(wf_id, **kwargs):
    return _backend().update_workflow(wf_id, **kwargs)


def list_workflows():
    return _backend().list_workflows()


# ---------- Audit Log ----------

def log_event(workflow_id, stage, action, details=None, actor="developer"):
    return _backend().log_event(workflow_id, stage, action, details=details, actor=actor)


def get_audit_logs(workflow_id):
    return _backend().get_audit_logs(workflow_id)


# ---------- Feedback ----------

def save_feedback(workflow_id, stage, action, smell_type=None, refactoring_type=None,
                  severity=None, reason=None, rating=None, accepted=False,
                  step_key=None):
    return _backend().save_feedback(
        workflow_id, stage, action,
        smell_type=smell_type, refactoring_type=refactoring_type,
        severity=severity, reason=reason, rating=rating,
        accepted=accepted, step_key=step_key,
    )


def export_feedback_dataset():
    return _backend().export_feedback_dataset()


# ---------- Step-level plan feedback ----------

#: Re-exported so callers keep importing it from here. Both backends define the
#: same tuple; this module is the one place that is allowed to name it.
from db.sqlite_repository import PLAN_STEP_ACTIONS  # noqa: E402


def recorded_plan_step_keys(workflow_id) -> set:
    return _backend().recorded_plan_step_keys(workflow_id)


def plan_step_acceptance_stats():
    return _backend().plan_step_acceptance_stats()


# ---------- Selection Impact Records ----------

def get_impact_records(workflow_id, model_version=None):
    return _backend().get_impact_records(workflow_id, model_version=model_version)


def save_impact_records(workflow_id, records):
    return _backend().save_impact_records(workflow_id, records)


def delete_impact_records(workflow_id):
    return _backend().delete_impact_records(workflow_id)


# ---------- Shared helpers ----------
#
# Pure functions with no backend behind them. parse_json_field decodes a value
# that is a string under SQLite and a dict under Supabase, and already handled
# both before either backend existed — which is why no caller had to change.

def parse_json_field(wf: dict, field: str):
    return _backend().parse_json_field(wf, field)


def now_iso():
    from db.sqlite_repository import now_iso as _now_iso
    return _now_iso()
