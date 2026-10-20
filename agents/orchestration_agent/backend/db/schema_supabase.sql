-- ============================================================================
-- DIWO Orchestration Agent — Supabase / Postgres schema
-- R26-SE-008 | Bandara S M Y M | IT22277886
--
-- RUN THIS ONCE, in the Supabase SQL editor, before pointing a deployment at
-- Supabase. The service creates no tables of its own: PostgREST cannot issue
-- DDL, and a backend that silently created its own schema on startup is how
-- two environments quietly drift apart.
--
--   Supabase dashboard -> SQL Editor -> New query -> paste -> Run
--
-- Everything is IF NOT EXISTS, so re-running it is safe.
--
-- DIFFERENCES FROM THE SQLITE SCHEMA (db/database.py), and why:
--
--   * TEXT *_json columns become jsonb. PostgREST then returns real objects
--     instead of strings, which is what lets the plan be queried in SQL
--     (plan_json->'steps') instead of only in Python. parse_json_field()
--     already accepted both, so no application code depends on which it is.
--
--   * INTEGER PRIMARY KEY AUTOINCREMENT becomes GENERATED ALWAYS AS IDENTITY.
--
--   * `accepted` stays smallint 0/1 rather than becoming boolean, because
--     domain/planning_recommendation.py sums that column and a boolean would
--     change what the sum means.
--
--   * The migrated columns SQLite adds via ALTER TABLE at startup
--     (updated_smells_json, planning_input_json, cuqa_report_json,
--     plan_full_json, step_key) are declared here up front.
-- ============================================================================

-- ── Workflows ───────────────────────────────────────────────────────────────
create table if not exists public.workflows (
    id                          text primary key,
    target                      text        not null,
    language                    text        not null,
    status                      text        not null default 'smell_review',
    created_at                  text        not null,
    updated_at                  text        not null,
    smells_json                 jsonb,
    selected_smells_json        jsonb,
    updated_smells_json         jsonb,
    planning_input_json         jsonb,
    -- The CUQA quality report verbatim, so the filtered report handed to the
    -- RDP agent is a copy of it rather than a rebuild from the flattened smell
    -- list — which loses per-file metrics, quality_score and each smell's own
    -- fields (entity, start_line, ...).
    cuqa_report_json            jsonb,
    plan_json                   jsonb,
    -- The plan as the RDP agent produced it, before approval reduced plan_json
    -- to the approved steps. Rolling back from the transformation stage
    -- restores it, so a step rejected on the first pass can be approved on the
    -- second.
    plan_full_json              jsonb,
    transformation_result_json  jsonb,
    metrics_before_json         jsonb,
    metrics_after_json          jsonb
);

create index if not exists idx_workflows_created
    on public.workflows (created_at desc);


-- ── Audit trail ─────────────────────────────────────────────────────────────
create table if not exists public.audit_logs (
    id           bigint generated always as identity primary key,
    workflow_id  text not null,
    stage        text not null,
    action       text not null,
    actor        text not null default 'developer',
    details_json jsonb,
    timestamp    text not null
);

create index if not exists idx_audit_workflow
    on public.audit_logs (workflow_id, timestamp);


-- ── Per-smell Selection Impact Records (domain/impact_model.py) ─────────────
-- Keyed by model_version so records from different model revisions stay
-- distinguishable rather than overwriting each other — that is what makes a
-- later before/after comparison of model accuracy possible.
create table if not exists public.smell_impacts (
    id            bigint generated always as identity primary key,
    workflow_id   text not null,
    smell_id      text not null,
    model_version text not null,
    record_json   jsonb not null,
    computed_at   text not null,
    -- save_impact_records() upserts on exactly this triple.
    constraint smell_impacts_unique unique (workflow_id, smell_id, model_version)
);

create index if not exists idx_impacts_workflow
    on public.smell_impacts (workflow_id);


-- ── Feedback the ML Feedback Manager trains on ─────────────────────────────
create table if not exists public.feedback_entries (
    id               bigint generated always as identity primary key,
    workflow_id      text not null,
    stage            text not null,
    action           text not null,
    smell_type       text,
    refactoring_type text,
    severity         text,
    reason           text,
    rating           integer,
    accepted         smallint not null default 0,
    step_key         text,
    timestamp        text not null
);

-- Makes "have we already recorded this decision?" one indexed lookup, which is
-- what stops a rejection being counted twice when the frontend sends `modify`
-- and then `approve` for the same review.
create index if not exists idx_feedback_step
    on public.feedback_entries (workflow_id, action, step_key);


-- ── GitHub webhook jobs (db/github_job_repository.py) ──────────────────────
-- One row per accepted GitHub webhook delivery, processed by github_worker.py.
-- delivery_id is UNIQUE because GitHub reuses it on redelivery: the constraint
-- is the duplicate check. Added for the GitHub App integration (Milestone 1);
-- on an existing project, re-running this whole file adds just this table.
create table if not exists public.github_analysis_jobs (
    id                  text primary key,
    delivery_id         text    not null unique,
    event               text    not null,
    action              text,
    installation_id     bigint  not null,
    repo_owner          text    not null,
    repo_name           text    not null,
    repo_full_name      text    not null,
    pr_number           integer not null,
    head_sha            text    not null,
    head_repo_full_name text,
    is_fork             smallint not null default 0,
    status              text    not null default 'queued',
    attempts            integer not null default 0,
    max_attempts        integer not null default 5,
    next_attempt_at     text    not null,
    locked_by           text,
    locked_at           text,
    check_run_id        bigint,
    last_error          text,
    superseded_by       text,
    created_at          text    not null,
    updated_at          text    not null,
    completed_at        text
);

create index if not exists idx_github_jobs_claim
    on public.github_analysis_jobs (status, next_attempt_at);
create index if not exists idx_github_jobs_pr
    on public.github_analysis_jobs (repo_full_name, pr_number, created_at);


-- ── GitHub PR commit <-> DIWO workflow (db/github_pr_workflow_repository.py) ─
-- One row per analysed PR COMMIT. Commit-specific so a stale result can never
-- be mistaken for the current one, and so a repeated delivery for the same
-- commit reuses its row instead of creating a second workflow. Added for
-- GitHub integration Milestones 2-3; re-running this file adds just this table.
create table if not exists public.github_pr_workflows (
    id                   text    primary key,
    repository_owner     text    not null,
    repository_name      text    not null,
    repository_full_name text    not null,
    pr_number            integer not null,
    head_sha             text    not null,
    installation_id      bigint  not null,
    workflow_id          text,
    analysis_status      text    not null default 'pending',
    job_id               text,
    check_run_id         bigint,
    smell_count          integer,
    files_analyzed       integer,
    error                text,
    created_at           text    not null,
    updated_at           text    not null,
    constraint github_pr_workflows_commit_unique
        unique (repository_full_name, pr_number, head_sha)
);

create index if not exists idx_github_pr_workflows_workflow
    on public.github_pr_workflows (workflow_id);

-- Milestone 4: developer review state of the PR commit (who decided last, when).
alter table public.github_pr_workflows add column if not exists review_status text;
alter table public.github_pr_workflows add column if not exists reviewed_by   text;
alter table public.github_pr_workflows add column if not exists reviewed_at   text;

-- Interactive GitHub review (PR comment commands): shared draft state.
alter table public.github_pr_workflows add column if not exists draft_selection_json      text;
alter table public.github_pr_workflows add column if not exists draft_plan_decisions_json text;
alter table public.github_pr_workflows add column if not exists pending_confirmation      text;
alter table public.github_pr_workflows add column if not exists status_comment_id         text;
alter table public.github_pr_workflows add column if not exists origin_writeback_id       text;
alter table public.github_analysis_jobs add column if not exists command_json             text;


-- ── Proposed changes per file of one SCTVA run on a PR commit ─────────────
create table if not exists public.github_pr_transformations (
    id                 text primary key,
    workflow_id        text not null,
    mapping_id         text not null,
    run_id             text not null,
    display_id         text not null,
    head_sha           text not null,
    file_path          text not null,
    original_blob_sha  text,
    original_sha256    text not null,
    refactored_sha256  text,
    original_content   text,
    refactored_content text,
    diff               text,
    refactorings_json  text,
    validation_json    text,
    confidence         double precision,
    validation_passed  smallint not null default 0,
    coupled            smallint not null default 0,
    status             text not null,
    status_reason      text,
    decided_by         text,
    decided_at         text,
    applied_commit_sha text,
    applied_at         text,
    created_at         text not null,
    updated_at         text not null,
    constraint github_pr_transformations_unique unique (workflow_id, run_id, file_path)
);
create index if not exists idx_github_pr_transformations_workflow
    on public.github_pr_transformations (workflow_id, run_id);


-- ── Commits DIWO makes (or refuses) on PR branches; idempotent ────────────
create table if not exists public.github_pr_writebacks (
    id                      text primary key,
    workflow_id             text not null,
    mapping_id              text not null,
    idempotency_key         text not null unique,
    base_sha                text not null,
    branch                  text,
    transformation_ids_json text not null,
    status                  text not null,
    commit_sha              text,
    requested_by            text,
    error                   text,
    created_at              text not null,
    updated_at              text not null,
    committed_at            text
);


-- ============================================================================
-- ROW-LEVEL SECURITY
--
-- RLS is ENABLED with no policies, which denies every request that arrives
-- with the anon key. That is deliberate: these four tables are written only by
-- the orchestration backend using the SERVICE ROLE key, which bypasses RLS.
-- No browser should read or write them directly.
--
-- Leaving RLS disabled would instead expose the entire audit trail to anyone
-- holding the anon key — and the anon key ships in the frontend bundle.
--
-- If you later want a signed-in developer to read their own workflows from the
-- browser, add a scoped policy per table rather than disabling RLS, e.g.
--
--   create policy "read own workflows" on public.workflows
--       for select to authenticated using (auth.uid()::text = owner_id);
--
-- (which needs an owner_id column this schema does not yet carry).
-- ============================================================================
alter table public.workflows        enable row level security;
alter table public.audit_logs       enable row level security;
alter table public.smell_impacts    enable row level security;
alter table public.feedback_entries enable row level security;
alter table public.github_analysis_jobs enable row level security;
alter table public.github_pr_workflows  enable row level security;
alter table public.github_pr_transformations enable row level security;
alter table public.github_pr_writebacks      enable row level security;
