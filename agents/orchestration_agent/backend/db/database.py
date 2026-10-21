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

        -- GitHub webhook events accepted for background processing
        -- (db/github_job_repository.py). delivery_id is UNIQUE because GitHub
        -- reuses it on redelivery: that constraint IS the duplicate check.
        CREATE TABLE IF NOT EXISTS github_analysis_jobs (
            id                  TEXT PRIMARY KEY,
            delivery_id         TEXT NOT NULL UNIQUE,
            event               TEXT NOT NULL,
            action              TEXT,
            installation_id     INTEGER NOT NULL,
            repo_owner          TEXT NOT NULL,
            repo_name           TEXT NOT NULL,
            repo_full_name      TEXT NOT NULL,
            pr_number           INTEGER NOT NULL,
            head_sha            TEXT NOT NULL,
            head_repo_full_name TEXT,
            is_fork             INTEGER NOT NULL DEFAULT 0,
            status              TEXT NOT NULL DEFAULT 'queued',
            attempts            INTEGER NOT NULL DEFAULT 0,
            max_attempts        INTEGER NOT NULL DEFAULT 5,
            next_attempt_at     TEXT NOT NULL,
            locked_by           TEXT,
            locked_at           TEXT,
            check_run_id        INTEGER,
            last_error          TEXT,
            superseded_by       TEXT,
            created_at          TEXT NOT NULL,
            updated_at          TEXT NOT NULL,
            completed_at        TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_github_jobs_claim
            ON github_analysis_jobs (status, next_attempt_at);
        CREATE INDEX IF NOT EXISTS idx_github_jobs_pr
            ON github_analysis_jobs (repo_full_name, pr_number, created_at);

        -- One row per analysed PR COMMIT (db/github_pr_workflow_repository.py).
        -- Commit-specific on purpose: a result for an older head SHA can never
        -- be mistaken for the current one, and a second delivery for the same
        -- commit reuses its row instead of creating a second workflow.
        CREATE TABLE IF NOT EXISTS github_pr_workflows (
            id                   TEXT PRIMARY KEY,
            repository_owner     TEXT NOT NULL,
            repository_name      TEXT NOT NULL,
            repository_full_name TEXT NOT NULL,
            pr_number            INTEGER NOT NULL,
            head_sha             TEXT NOT NULL,
            installation_id      INTEGER NOT NULL,
            workflow_id          TEXT,
            analysis_status      TEXT NOT NULL DEFAULT 'pending',
            job_id               TEXT,
            check_run_id         INTEGER,
            smell_count          INTEGER,
            files_analyzed       INTEGER,
            error                TEXT,
            created_at           TEXT NOT NULL,
            updated_at           TEXT NOT NULL,
            UNIQUE (repository_full_name, pr_number, head_sha)
        );
        CREATE INDEX IF NOT EXISTS idx_github_pr_workflows_workflow
            ON github_pr_workflows (workflow_id);

        -- One proposed change per FILE of one SCTVA run on a PR commit
        -- (db/github_review_repository.py). Holds the exact original and
        -- refactored text the developer reviews, their hashes (what is
        -- committed must be what was reviewed), SCTVA's real validation and
        -- the developer's final decision.
        CREATE TABLE IF NOT EXISTS github_pr_transformations (
            id                 TEXT PRIMARY KEY,
            workflow_id        TEXT NOT NULL,
            mapping_id         TEXT NOT NULL,
            run_id             TEXT NOT NULL,
            display_id         TEXT NOT NULL,
            head_sha           TEXT NOT NULL,
            file_path          TEXT NOT NULL,
            original_blob_sha  TEXT,
            original_sha256    TEXT NOT NULL,
            refactored_sha256  TEXT,
            original_content   TEXT,
            refactored_content TEXT,
            diff               TEXT,
            refactorings_json  TEXT,
            validation_json    TEXT,
            confidence         REAL,
            validation_passed  INTEGER NOT NULL DEFAULT 0,
            coupled            INTEGER NOT NULL DEFAULT 0,
            status             TEXT NOT NULL,
            status_reason      TEXT,
            decided_by         TEXT,
            decided_at         TEXT,
            applied_commit_sha TEXT,
            applied_at         TEXT,
            created_at         TEXT NOT NULL,
            updated_at         TEXT NOT NULL,
            UNIQUE (workflow_id, run_id, file_path)
        );
        CREATE INDEX IF NOT EXISTS idx_github_pr_transformations_workflow
            ON github_pr_transformations (workflow_id, run_id);

        -- One commit DIWO makes (or refuses to make) on a PR branch.
        -- idempotency_key = hash(base SHA + accepted transformation ids): the
        -- same acceptance can never produce two commits.
        CREATE TABLE IF NOT EXISTS github_pr_writebacks (
            id                     TEXT PRIMARY KEY,
            workflow_id            TEXT NOT NULL,
            mapping_id             TEXT NOT NULL,
            idempotency_key        TEXT NOT NULL UNIQUE,
            base_sha               TEXT NOT NULL,
            branch                 TEXT,
            transformation_ids_json TEXT NOT NULL,
            status                 TEXT NOT NULL,
            commit_sha             TEXT,
            requested_by           TEXT,
            error                  TEXT,
            created_at             TEXT NOT NULL,
            updated_at             TEXT NOT NULL,
            committed_at           TEXT
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
        # The selected files' source, read at smell selection, so the
        # transformation does not depend on CUQA's shared workspace still
        # holding this repository (services/source_snapshot.py).
        if "source_snapshot_json" not in existing_cols:
            conn.execute("ALTER TABLE workflows ADD COLUMN source_snapshot_json TEXT")

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

        # Milestone 4: where the developer review of a PR commit stands, and
        # who made the last decision on it.
        mapping_cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(github_pr_workflows)").fetchall()
        }
        for column in ("review_status", "reviewed_by", "reviewed_at",
                       # Interactive GitHub review: the shared draft state
                       # GitHub commands and DIWO both read and write.
                       "draft_selection_json", "draft_plan_decisions_json",
                       "pending_confirmation", "status_comment_id",
                       "origin_writeback_id"):
            if column not in mapping_cols:
                conn.execute(f"ALTER TABLE github_pr_workflows ADD COLUMN {column} TEXT")

        # PR-comment and requested-action jobs carry the parsed command.
        job_cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(github_analysis_jobs)").fetchall()
        }
        if "command_json" not in job_cols:
            conn.execute("ALTER TABLE github_analysis_jobs ADD COLUMN command_json TEXT")

    print(f"[DIWO] Database initialized (SQLite: {DB_PATH}).")


def now_iso():
    return datetime.now(timezone.utc).isoformat()
