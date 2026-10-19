"""
GitHub job persistence
======================
R26-SE-008 | Bandara S M Y M | IT22277886

The durable queue behind the GitHub webhook. A webhook request only ever
writes a row here and returns; github_worker.py claims rows and does the work.
Because the queue is a database table, not process memory, a worker restart
loses nothing: a claim that is never finished simply expires and the job is
claimed again.

Same seam as db/workflow_repository.py — SQLite by default, Supabase when it is
configured, decided per call — so the queue lives wherever the workflows do.

LIFECYCLE

    queued ──claim──> in_progress ──> completed
       ^                   │
       └── retry (backoff) ┤
                           ├──> failed       (non-retryable, or attempts used up)
                           └──> superseded   (a newer commit arrived for the PR)

IDEMPOTENCY. delivery_id is UNIQUE. GitHub reuses the delivery GUID when it
redelivers, so a second insert for the same delivery is refused by the
database itself rather than by a check-then-insert race in Python.

ATTEMPTS are counted at CLAIM time, not at failure time, so a job that crashes
its worker every time still runs out of attempts instead of looping forever.

Timestamps are fixed-width UTC ISO strings (microseconds always present), so
"earlier than" is a plain string comparison in both SQLite and PostgREST.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone

import config

logger = logging.getLogger("diwo.github.jobs")

TABLE = "github_analysis_jobs"

#: Only pull_request jobs describe a commit; comment, transformation and
#: write-back jobs never supersede an analysis or count as "a newer commit".
PR_EVENT = "pull_request"

QUEUED = "queued"
IN_PROGRESS = "in_progress"
COMPLETED = "completed"
FAILED = "failed"
SUPERSEDED = "superseded"

__all__ = [
    "enqueue_pr_job", "claim_next_job", "get_job", "get_job_by_delivery",
    "update_job", "complete_job", "retry_or_fail_job", "fail_job",
    "supersede_job", "newer_job_for_pr", "stamp", "enqueue_task",
    "QUEUED", "IN_PROGRESS", "COMPLETED", "FAILED", "SUPERSEDED",
]


class DuplicateDelivery(Exception):
    """This delivery_id is already queued or processed."""


def stamp(moment: datetime | None = None) -> str:
    """Fixed-width UTC timestamp: comparable as a string."""
    moment = (moment or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


# ─── SQLite ──────────────────────────────────────────────────────────────────

class _SqliteJobs:

    @staticmethod
    def _db():
        from db.database import get_db
        return get_db()

    def insert(self, row):
        import sqlite3
        db = self._db()
        columns = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        try:
            db.execute(f"INSERT INTO {TABLE} ({columns}) VALUES ({marks})",
                       list(row.values()))
        except sqlite3.IntegrityError as exc:
            db.rollback()
            raise DuplicateDelivery(row["delivery_id"]) from exc
        db.commit()

    def get(self, column, value):
        row = self._db().execute(
            f"SELECT * FROM {TABLE} WHERE {column}=?", (value,)).fetchone()
        return dict(row) if row else None

    def update(self, job_id, fields, expect=None):
        """Patch a row; with `expect`, only if those columns still match.

        Returns True when a row was changed — the compare-and-set that makes a
        claim safe against a second worker.
        """
        db = self._db()
        sets = ", ".join(f"{k}=?" for k in fields)
        where = "id=?" + "".join(f" AND {k}=?" for k in (expect or {}))
        cursor = db.execute(
            f"UPDATE {TABLE} SET {sets} WHERE {where}",
            list(fields.values()) + [job_id] + list((expect or {}).values()),
        )
        db.commit()
        return cursor.rowcount > 0

    def claimable(self, now, stale_before):
        rows = self._db().execute(
            f"""SELECT * FROM {TABLE}
                WHERE (status=? AND next_attempt_at<=?)
                   OR (status=? AND locked_at<?)
                ORDER BY created_at ASC LIMIT 10""",
            (QUEUED, now, IN_PROGRESS, stale_before),
        ).fetchall()
        return [dict(r) for r in rows]

    def supersede_older(self, job):
        db = self._db()
        db.execute(
            f"""UPDATE {TABLE}
                SET status=?, superseded_by=?, updated_at=?, completed_at=?
                WHERE repo_full_name=? AND pr_number=? AND status=?
                  AND id<>? AND created_at<=? AND event=?""",
            (SUPERSEDED, job["id"], stamp(), stamp(), job["repo_full_name"],
             job["pr_number"], QUEUED, job["id"], job["created_at"], PR_EVENT),
        )
        db.commit()

    def newer_for_pr(self, job):
        row = self._db().execute(
            f"""SELECT id FROM {TABLE}
                WHERE repo_full_name=? AND pr_number=? AND id<>?
                  AND created_at>? AND head_sha<>? AND event=?
                ORDER BY created_at DESC LIMIT 1""",
            (job["repo_full_name"], job["pr_number"], job["id"],
             job["created_at"], job["head_sha"], PR_EVENT),
        ).fetchone()
        return row["id"] if row else None


# ─── Supabase ────────────────────────────────────────────────────────────────

class _SupabaseJobs:

    @staticmethod
    def _table():
        from db.supabase_client import get_supabase
        return get_supabase().table(TABLE)

    def insert(self, row):
        try:
            self._table().insert(row).execute()
        except Exception as exc:  # postgrest APIError; imported lazily
            if getattr(exc, "code", None) == "23505":   # unique_violation
                raise DuplicateDelivery(row["delivery_id"]) from exc
            raise

    def get(self, column, value):
        rows = self._table().select("*").eq(column, value).limit(1).execute().data
        return rows[0] if rows else None

    def update(self, job_id, fields, expect=None):
        query = self._table().update(fields).eq("id", job_id)
        for key, value in (expect or {}).items():
            query = query.is_(key, "null") if value is None else query.eq(key, value)
        return bool(query.execute().data)

    def claimable(self, now, stale_before):
        queued = (self._table().select("*").eq("status", QUEUED)
                  .lte("next_attempt_at", now).order("created_at")
                  .limit(10).execute().data or [])
        stale = (self._table().select("*").eq("status", IN_PROGRESS)
                 .lt("locked_at", stale_before).order("created_at")
                 .limit(10).execute().data or [])
        return sorted(queued + stale, key=lambda r: r["created_at"])[:10]

    def supersede_older(self, job):
        (self._table()
         .update({"status": SUPERSEDED, "superseded_by": job["id"],
                  "updated_at": stamp(), "completed_at": stamp()})
         .eq("repo_full_name", job["repo_full_name"])
         .eq("pr_number", job["pr_number"]).eq("status", QUEUED)
         .neq("id", job["id"]).lte("created_at", job["created_at"])
         .eq("event", PR_EVENT)
         .execute())

    def newer_for_pr(self, job):
        rows = (self._table().select("id")
                .eq("repo_full_name", job["repo_full_name"])
                .eq("pr_number", job["pr_number"]).neq("id", job["id"])
                .gt("created_at", job["created_at"]).neq("head_sha", job["head_sha"])
                .eq("event", PR_EVENT)
                .order("created_at", desc=True).limit(1).execute().data)
        return rows[0]["id"] if rows else None


def _backend():
    return _SupabaseJobs() if config.uses_supabase() else _SqliteJobs()


# ─── Public API ──────────────────────────────────────────────────────────────

def enqueue_pr_job(event: dict) -> tuple[dict, bool]:
    """Durably accept one pull-request event. Returns (job, created).

    `created` is False when this delivery was already accepted; the existing
    job is returned and nothing new is queued. Any other storage failure
    propagates, so the webhook can refuse rather than claim it accepted a job
    it never stored.
    """
    now = stamp()
    row = {
        "id": f"ghjob_{uuid.uuid4().hex[:16]}",
        "delivery_id": event["delivery_id"],
        "event": event["event"],
        "action": event.get("action"),
        "installation_id": event["installation_id"],
        "repo_owner": event["repo_owner"],
        "repo_name": event["repo_name"],
        "repo_full_name": event["repo_full_name"],
        "pr_number": event["pr_number"],
        "head_sha": event["head_sha"],
        "head_repo_full_name": event.get("head_repo_full_name"),
        "is_fork": 1 if event.get("is_fork") else 0,
        "status": QUEUED,
        "attempts": 0,
        "max_attempts": config.github_job_max_attempts(),
        "next_attempt_at": now,
        "created_at": now,
        "updated_at": now,
    }

    backend = _backend()
    try:
        backend.insert(row)
    except DuplicateDelivery:
        return backend.get("delivery_id", event["delivery_id"]), False

    # An older commit of this PR that has not started yet will never be worth
    # analysing now — the PR head has moved past it.
    backend.supersede_older(row)
    return row, True


def enqueue_task(event: str, delivery_id: str, context: dict, command: dict) -> tuple[dict, bool]:
    """Queue a non-analysis job (PR command, transformation, write-back).

    `delivery_id` is the idempotency key: a GitHub delivery GUID for commands,
    a deterministic key for internal tasks — so a retried request or a
    redelivered comment queues nothing twice. `context` carries the PR fields
    (repo, PR number, installation); head_sha may be "" when not yet known.
    Returns (job, created).
    """
    import json
    now = stamp()
    row = {
        "id": f"ghjob_{uuid.uuid4().hex[:16]}",
        "delivery_id": delivery_id,
        "event": event,
        "action": command.get("name"),
        "installation_id": context["installation_id"],
        "repo_owner": context["repo_owner"],
        "repo_name": context["repo_name"],
        "repo_full_name": context["repo_full_name"],
        "pr_number": context["pr_number"],
        "head_sha": context.get("head_sha") or "",
        "head_repo_full_name": context.get("head_repo_full_name"),
        "is_fork": 1 if context.get("is_fork") else 0,
        "status": QUEUED,
        "attempts": 0,
        "max_attempts": config.github_job_max_attempts(),
        "next_attempt_at": now,
        "created_at": now,
        "updated_at": now,
        "command_json": json.dumps(command),
    }
    backend = _backend()
    try:
        backend.insert(row)
    except DuplicateDelivery:
        return backend.get("delivery_id", delivery_id), False
    return row, True


def claim_next_job(worker_id: str = "worker") -> dict | None:
    """Claim the oldest runnable job, or return None when there is none.

    Runnable means queued and due, or in progress under a claim older than the
    lock timeout (its worker died). A stale job with no attempts left is
    recorded as failed instead of being run again.
    """
    backend = _backend()
    now_dt = datetime.now(timezone.utc)
    now = stamp(now_dt)
    stale_before = stamp(now_dt - timedelta(seconds=config.github_job_lock_timeout_seconds()))

    for job in backend.claimable(now, stale_before):
        expect = {"status": job["status"], "attempts": job["attempts"]}

        if job["attempts"] >= job["max_attempts"]:
            backend.update(job["id"], {
                "status": FAILED, "updated_at": now, "completed_at": now,
                "locked_by": None, "locked_at": None,
                "last_error": (job.get("last_error") or "")
                + " | worker did not finish within the lock timeout; attempts exhausted",
            }, expect=expect)
            continue

        token = f"{worker_id}:{uuid.uuid4().hex[:8]}"
        claimed = backend.update(job["id"], {
            "status": IN_PROGRESS, "attempts": job["attempts"] + 1,
            "locked_by": token, "locked_at": now, "updated_at": now,
        }, expect=expect)
        if claimed:
            return backend.get("id", job["id"])

    return None


def get_job(job_id: str) -> dict | None:
    return _backend().get("id", job_id)


def get_job_by_delivery(delivery_id: str) -> dict | None:
    return _backend().get("delivery_id", delivery_id)


def update_job(job_id: str, **fields) -> None:
    fields["updated_at"] = stamp()
    _backend().update(job_id, fields)


def complete_job(job_id: str) -> None:
    now = stamp()
    _backend().update(job_id, {"status": COMPLETED, "updated_at": now,
                               "completed_at": now, "locked_by": None,
                               "locked_at": None, "last_error": None})


def fail_job(job_id: str, error: str) -> None:
    now = stamp()
    _backend().update(job_id, {"status": FAILED, "updated_at": now,
                               "completed_at": now, "locked_by": None,
                               "locked_at": None, "last_error": error[:2000]})


def retry_or_fail_job(job: dict, error: str) -> str:
    """Schedule another attempt with exponential backoff, or fail the job.

    Returns the status the job was left in.
    """
    if job["attempts"] >= job["max_attempts"]:
        fail_job(job["id"], error)
        return FAILED

    delay = min(30 * (2 ** max(0, job["attempts"] - 1)), 15 * 60)
    _backend().update(job["id"], {
        "status": QUEUED, "updated_at": stamp(), "locked_by": None,
        "locked_at": None, "last_error": error[:2000],
        "next_attempt_at": stamp(datetime.now(timezone.utc) + timedelta(seconds=delay)),
    })
    return QUEUED


def supersede_job(job_id: str, newer_job_id: str) -> None:
    now = stamp()
    _backend().update(job_id, {"status": SUPERSEDED, "superseded_by": newer_job_id,
                               "updated_at": now, "completed_at": now,
                               "locked_by": None, "locked_at": None})


def newer_job_for_pr(job: dict) -> str | None:
    """Id of a later-received job for the same PR at a different commit."""
    return _backend().newer_for_pr(job)


def latest_head_sha_for_pr(repo_full_name: str, pr_number: int) -> str | None:
    """Head SHA of the most recently received event for a PR, or None.

    Lets an approval notice a newer commit that has been delivered but not yet
    analysed — the mapping table only learns about it once the worker runs.
    """
    probe = {"id": "", "repo_full_name": repo_full_name, "pr_number": pr_number,
             "created_at": "", "head_sha": ""}   # newer_for_pr only sees pull_request jobs
    newest = _backend().newer_for_pr(probe)
    if not newest:
        return None
    row = _backend().get("id", newest)
    return row["head_sha"] if row else None
