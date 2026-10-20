"""
GitHub review persistence — transformations and write-backs
===========================================================
R26-SE-008 | Bandara S M Y M | IT22277886

    github_pr_transformations   one proposed change per file per SCTVA run
    github_pr_writebacks        one commit DIWO makes (or refuses) on a PR branch

Both are the SINGLE source of truth for their decisions: a GitHub command and
the DIWO frontend read and write the same rows, so the two interfaces can never
hold different answers to "is T002 accepted?".

Transformation lifecycle:

    proposed ──accept──> accepted ──write-back──> applied
        │                   │
        └──reject──> rejected <──reject──┘
    failed      SCTVA could not produce a validated change; never acceptable
"""

import json
import uuid

from db.github_job_repository import stamp
from db.table_store import UniqueViolation, store

TRANSFORMATIONS = "github_pr_transformations"
WRITEBACKS = "github_pr_writebacks"

PROPOSED = "proposed"
ACCEPTED = "accepted"
REJECTED = "rejected"
APPLIED = "applied"
FAILED = "failed"

WB_PENDING = "pending"
WB_COMMITTING = "committing"
WB_COMMITTED = "committed"
WB_FAILED = "failed"
WB_REFUSED = "refused"


def _decode(row: dict | None) -> dict | None:
    if row is None:
        return None
    row = dict(row)
    for key in ("refactorings_json", "validation_json", "transformation_ids_json"):
        if isinstance(row.get(key), str):
            try:
                row[key] = json.loads(row[key])
            except ValueError:
                pass
    for key in ("validation_passed", "coupled"):
        if key in row:
            row[key] = bool(row[key])
    return row


# ─── Transformations ────────────────────────────────────────────────────────

def insert_transformations(rows: list) -> None:
    """Insert one run's records. A retry of the same run is a no-op per file."""
    now = stamp()
    for row in rows:
        record = {"id": f"ghtx_{uuid.uuid4().hex[:16]}", "created_at": now, "updated_at": now,
                  **row}
        for key in ("refactorings_json", "validation_json"):
            if not isinstance(record.get(key), (str, type(None))):
                record[key] = json.dumps(record[key])
        for key in ("validation_passed", "coupled"):
            record[key] = 1 if record.get(key) else 0
        try:
            store().insert(TRANSFORMATIONS, record)
        except UniqueViolation:
            continue


def latest_run_id(workflow_id: str) -> str | None:
    rows = store().select(TRANSFORMATIONS, {"workflow_id": workflow_id}, order="created_at",
                          desc=True, limit=1)
    return rows[0]["run_id"] if rows else None


def list_transformations(workflow_id: str, run_id: str | None = None) -> list:
    """The records of a run (the latest run by default), in display order."""
    run_id = run_id or latest_run_id(workflow_id)
    if not run_id:
        return []
    rows = store().select(TRANSFORMATIONS, {"workflow_id": workflow_id, "run_id": run_id},
                          order="display_id")
    return [_decode(r) for r in rows]


def get_transformation(workflow_id: str, display_id: str) -> dict | None:
    for row in list_transformations(workflow_id):
        if row["display_id"].upper() == display_id.upper():
            return row
    return None


def update_transformation(row_id: str, expect_status: str | None = None, **fields) -> bool:
    """Patch a record; with expect_status, only if it is still in that status."""
    fields["updated_at"] = stamp()
    where = {"id": row_id}
    if expect_status:
        where["status"] = expect_status
    return store().update(TRANSFORMATIONS, where, fields) > 0


# ─── Write-backs ────────────────────────────────────────────────────────────

def get_or_create_writeback(row: dict) -> tuple[dict, bool]:
    """The write-back for this idempotency key; created on first request."""
    existing = get_writeback_by_key(row["idempotency_key"])
    if existing:
        return existing, False
    now = stamp()
    record = {"id": f"ghwb_{uuid.uuid4().hex[:16]}", "status": WB_PENDING,
              "created_at": now, "updated_at": now, **row}
    if not isinstance(record.get("transformation_ids_json"), str):
        record["transformation_ids_json"] = json.dumps(record["transformation_ids_json"])
    try:
        store().insert(WRITEBACKS, record)
    except UniqueViolation:
        return get_writeback_by_key(row["idempotency_key"]), False
    return _decode(record), True


def get_writeback_by_key(key: str) -> dict | None:
    rows = store().select(WRITEBACKS, {"idempotency_key": key}, limit=1)
    return _decode(rows[0]) if rows else None


def get_writeback(writeback_id: str) -> dict | None:
    rows = store().select(WRITEBACKS, {"id": writeback_id}, limit=1)
    return _decode(rows[0]) if rows else None


def latest_writeback(workflow_id: str) -> dict | None:
    rows = store().select(WRITEBACKS, {"workflow_id": workflow_id}, order="created_at",
                          desc=True, limit=1)
    return _decode(rows[0]) if rows else None


def writeback_for_commit(commit_sha: str) -> dict | None:
    rows = store().select(WRITEBACKS, {"commit_sha": commit_sha}, limit=1)
    return _decode(rows[0]) if rows else None


def update_writeback(writeback_id: str, expect_status: str | None = None, **fields) -> bool:
    fields["updated_at"] = stamp()
    where = {"id": writeback_id}
    if expect_status:
        where["status"] = expect_status
    return store().update(WRITEBACKS, where, fields) > 0
