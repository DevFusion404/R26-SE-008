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
