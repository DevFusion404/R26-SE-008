"""
GitHub PR <-> DIWO workflow persistence
=======================================
R26-SE-008 | Bandara S M Y M | IT22277886

One row per analysed pull-request COMMIT, linking it to the DIWO workflow its
CUQA findings were loaded into. Same per-call SQLite / Supabase seam as
db/workflow_repository.py.

    (repository_full_name, pr_number, head_sha)   UNIQUE

Commit-specific on purpose:
  * a result for an older head SHA can never be mistaken for the current one;
  * a second delivery for the same commit (reopened, redelivered) finds the
    existing row, and through it the existing workflow, instead of creating a
    duplicate.

analysis_status values:

    pending      row created, analysis not finished
    completed    CUQA found smells; workflow_id is set, developer review needed
    no_smells    CUQA ran and found nothing; no workflow is created
    failed       analysis could not be completed; `error` says why
    superseded   a newer commit arrived before this one was published
    skipped_fork fork PRs are not analysed yet
"""

import uuid

import config
from db.github_job_repository import stamp

TABLE = "github_pr_workflows"

PENDING = "pending"
COMPLETED = "completed"
NO_SMELLS = "no_smells"
FAILED = "failed"
SUPERSEDED = "superseded"
SKIPPED_FORK = "skipped_fork"

__all__ = [
    "get_or_create_mapping", "get_mapping", "get_mapping_for_workflow",
    "update_mapping", "is_github_linked", "github_linked_workflow_ids",
    "latest_mapping_for_pr",
    "PENDING", "COMPLETED", "NO_SMELLS", "FAILED", "SUPERSEDED", "SKIPPED_FORK",
]


class _Exists(Exception):
    pass


class _Sqlite:

    @staticmethod
    def _db():
        from db.database import get_db
        return get_db()

    def insert(self, row):
        import sqlite3
        db = self._db()
        try:
            db.execute(f"INSERT INTO {TABLE} ({', '.join(row)}) VALUES "
                       f"({', '.join('?' for _ in row)})", list(row.values()))
        except sqlite3.IntegrityError as exc:
            db.rollback()
            raise _Exists() from exc
        db.commit()

    def select(self, where: dict, order_desc: str | None = None, limit: int | None = None,
               columns: str = "*"):
        clause = " AND ".join(f"{k}=?" for k in where) or "1=1"
        sql = f"SELECT {columns} FROM {TABLE} WHERE {clause}"
        if order_desc:
            sql += f" ORDER BY {order_desc} DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [dict(r) for r in self._db().execute(sql, list(where.values())).fetchall()]

    def update(self, row_id, fields):
        db = self._db()
        db.execute(f"UPDATE {TABLE} SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?",
                   list(fields.values()) + [row_id])
        db.commit()

    def linked_ids(self):
        rows = self._db().execute(
            f"SELECT DISTINCT workflow_id FROM {TABLE} WHERE workflow_id IS NOT NULL").fetchall()
        return {r[0] for r in rows}


class _Supabase:

    @staticmethod
    def _table():
        from db.supabase_client import get_supabase
        return get_supabase().table(TABLE)

    def insert(self, row):
        try:
            self._table().insert(row).execute()
        except Exception as exc:  # postgrest APIError, imported lazily
            if getattr(exc, "code", None) == "23505":
                raise _Exists() from exc
            raise

    def select(self, where: dict, order_desc: str | None = None, limit: int | None = None,
               columns: str = "*"):
        query = self._table().select(columns)
        for key, value in where.items():
            query = query.eq(key, value)
        if order_desc:
            query = query.order(order_desc, desc=True)
        if limit:
            query = query.limit(limit)
        return query.execute().data or []

    def update(self, row_id, fields):
        self._table().update(fields).eq("id", row_id).execute()

    def linked_ids(self):
        rows = (self._table().select("workflow_id")
                .not_.is_("workflow_id", "null").execute().data or [])
        return {r["workflow_id"] for r in rows}


def _backend():
    return _Supabase() if config.uses_supabase() else _Sqlite()


def _commit_key(repo_full_name, pr_number, head_sha):
    return {"repository_full_name": repo_full_name, "pr_number": pr_number,
            "head_sha": head_sha}


def get_mapping(repo_full_name: str, pr_number: int, head_sha: str) -> dict | None:
    rows = _backend().select(_commit_key(repo_full_name, pr_number, head_sha), limit=1)
    return rows[0] if rows else None


def get_or_create_mapping(job: dict) -> dict:
    """The row for the job's commit, created on first sight."""
    existing = get_mapping(job["repo_full_name"], job["pr_number"], job["head_sha"])
    if existing:
        return existing

    now = stamp()
    row = {
        "id": f"ghpr_{uuid.uuid4().hex[:16]}",
        "repository_owner": job["repo_owner"],
        "repository_name": job["repo_name"],
        "repository_full_name": job["repo_full_name"],
        "pr_number": job["pr_number"],
        "head_sha": job["head_sha"],
        "installation_id": job["installation_id"],
        "analysis_status": PENDING,
        "job_id": job["id"],
        "created_at": now,
        "updated_at": now,
    }
    try:
        _backend().insert(row)
    except _Exists:
        # Another worker created it between our read and insert.
        return get_mapping(job["repo_full_name"], job["pr_number"], job["head_sha"])
    return row


def update_mapping(row_id: str, **fields) -> None:
    fields["updated_at"] = stamp()
    _backend().update(row_id, fields)


def get_mapping_for_workflow(workflow_id: str) -> dict | None:
    rows = _backend().select({"workflow_id": workflow_id}, order_desc="created_at", limit=1)
    return rows[0] if rows else None


def latest_mapping_for_pr(repo_full_name: str, pr_number: int) -> dict | None:
    """The most recently created row for a PR — i.e. its newest analysed commit."""
    rows = _backend().select({"repository_full_name": repo_full_name, "pr_number": pr_number},
                             order_desc="created_at", limit=1)
    return rows[0] if rows else None


def get_draft(mapping: dict, field: str, default):
    """Decode one of the mapping's JSON draft columns (selection, decisions...)."""
    import json
    raw = mapping.get(field)
    if raw in (None, ""):
        return default
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return default
    return raw


def set_draft(row_id: str, field: str, value) -> None:
    import json
    update_mapping(row_id, **{field: None if value is None else json.dumps(value)})


def get_mapping_by_id(row_id: str) -> dict | None:
    rows = _backend().select({"id": row_id}, limit=1)
    return rows[0] if rows else None


def is_github_linked(workflow_id: str) -> bool:
    return bool(workflow_id) and get_mapping_for_workflow(workflow_id) is not None


def github_linked_workflow_ids() -> set:
    return _backend().linked_ids()
