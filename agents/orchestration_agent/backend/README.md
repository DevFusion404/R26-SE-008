# DIWO Orchestration Agent — Backend

R26-SE-008 | Bandara S M Y M | IT22277886

The Flask backend behind the DIWO frontend, and the orchestrator of the other
three research agents.

```
React DIWO frontend  (frontend/src/pages/diwo)
         │
         ▼
Orchestration Agent  (this backend, :5001, everything under /api)
         │
  ┌──────┼──────┐
  ▼      ▼      ▼
CUQA    RDP    SCTVA
:8080   :5000  :8002
```

## Layout

```
backend/
├── app.py                     application factory + entry point
├── config.py                  every URL, port and path
│
├── api/                       HTTP layer — validate, delegate, respond
│   ├── __init__.py            shared error shape + blueprint registration
│   ├── workflow_routes.py     workflow lifecycle and the approval stages
│   ├── integration_routes.py  agent reachability + the CUQA report proxy
│   ├── git_routes.py          apply-and-push
│   └── feedback_routes.py     audit logs + feedback export
│
├── clients/                   one HTTP client per specialized agent
│   ├── cuqa_client.py
│   ├── rdp_client.py
│   └── sctva_client.py
│
├── services/                  business logic
│   ├── workflow_service.py    coordinates the workflow and its decisions
│   ├── planning_service.py    filtered report → RDP → normalized plan
│   ├── transformation_service.py
│   ├── archive_service.py     the whole-project ZIP
│   └── git_service.py         clone, branch, apply, commit, push
│
├── domain/                    pure logic — no Flask, no HTTP, no database
│   ├── workflow_states.py
│   ├── cuqa_normalizer.py
│   ├── plan_normalizer.py
│   ├── sctva_mapper.py        approved plan <-> SCTVA action vocabulary
│   └── metrics.py
│
├── db/
│   ├── database.py            connection, schema, idempotent migrations
│   └── workflow_repository.py every SQL statement the workflow needs
│
├── runtime/                   generated data, never source
│   ├── database/diwo_audit.db
│   ├── reports/               saved updated-smell-report JSON
│   └── archives/              per-workflow refactored-source ZIPs
│
└── tests/
    ├── test_workflow_end_to_end.py
    ├── test_transform_endpoint.py
    ├── test_sctva_mapper.py
    └── fixtures/
        ├── cquaAgent.json
        ├── sctva_plan_cases.json
        └── sctva_mapper_golden.json
```

Dependencies flow one way: `api → services → clients / domain / db`. The
domain layer imports nothing from services, and the clients hold no workflow
rules — that is what keeps the specialized-agent integrations swappable.

## Running

```bash
cd agents/orchestration_agent/backend
pip install -r requirements.txt
python app.py                     # http://localhost:5001
```

Configuration comes from the process environment; see `.env.example` for every
variable and its default. Note the backend does **not** auto-load `.env`
(python-dotenv is not a dependency) — export the variables or set them in your
run configuration.

## Verifying

```bash
python -m compileall -q .
python -m tests.test_workflow_end_to_end
python -m tests.test_transform_endpoint
python -m tests.test_sctva_mapper
```

All three redirect `DIWO_RUNTIME_DIR` to a temporary folder, so a test run
never touches `runtime/`.

**`test_workflow_end_to_end`** drives the whole workflow through the Flask test
client. It passes whether or not the specialized agents happen to be running,
and prints which path it took. It asserts the two hand-offs that matter most:

* **step 4 → 7** — the report forwarded to RDP contains only the smells the
  developer kept.
* **step 10 → 12** — the plan sent onward contains only the steps the
  developer approved.

and the rollback behaviour: a rejected file is archived as its original
source, not as the refactored one.

**`test_transform_endpoint`** stubs SCTVA at the client boundary and checks
that only the approved plan reaches it, that its failure statuses pass through
(503 stays 503, not 500), and that the reply is normalized into the shape the
Transformation stage renders.

**`test_sctva_mapper`** is a golden test. The plan → SCTVA action mapping was
ported from JavaScript when the call moved server-side, so
`fixtures/sctva_mapper_golden.json` holds the output of the ORIGINAL browser
mapper and the test asserts an exact match — the request SCTVA receives is
byte-identical to what it received before.

## API

Every endpoint is mounted at `/api`. The Vite dev server proxies `/api` to
`http://localhost:5001`, so the frontend reaches them either way.

| Method     | Path                                          | Purpose                                  |
|------------|-----------------------------------------------|------------------------------------------|
| GET, POST  | `/api/workflows`                              | list / create a workflow                  |
| POST       | `/api/workflows/from-cuqa`                    | create from the live CUQA report          |
| GET        | `/api/workflows/<id>`                         | read a workflow                           |
| POST       | `/api/workflows/<id>/smell-selection-pass`    | preview a selection (no planning)         |
| POST       | `/api/workflows/<id>/save-updated-report`     | write the updated report to runtime/      |
| POST       | `/api/workflows/<id>/reset-to-smell-review`   | fall back to stage 1                      |
| POST       | `/api/workflows/<id>/select-smells`           | commit the selection, plan via RDP        |
| POST       | `/api/workflows/<id>/plan-preference-update`  | re-rank the plan from preferences         |
| POST       | `/api/workflows/<id>/reset-to-plan-approval`  | roll back to stage 2                      |
| POST       | `/api/workflows/<id>/plan-decision`           | approve / reject / modify the plan        |
| POST       | `/api/workflows/<id>/transformation-decision` | accept or roll back the transformation    |
| GET        | `/api/workflows/<id>/refactored-archive`      | download the project ZIP                  |
| POST       | `/api/workflows/<id>/complete`                | finish the workflow                       |
| GET        | `/api/workflows/<id>/audit-logs`              | the audit trail                           |
| GET        | `/api/feedback/export`                        | feedback training data                    |
| POST       | `/api/workflows/<id>/transform`               | run the approved plan through SCTVA       |
| GET        | `/api/cuqa/status`, `/api/rdp/status`, `/api/sctva/status` | agent reachability           |
| GET, POST  | `/api/cuqa/quality-report`                    | proxy Agent 1's quality report            |
| GET        | `/api/cuqa/project-structure`                 | proxy Agent 1's repository file tree      |
| POST       | `/api/workspace/sources`                      | read source text out of the workspace     |
| POST       | `/api/diwo/apply-and-push`                    | write the project to a git branch         |
| GET        | `/api/health`, `/`                            | backend health                            |

Four endpoints were ADDED in the 2026-08 restructure — `/api/sctva/status`,
`/api/workflows/<id>/transform`, `/api/cuqa/project-structure` and
`/api/workspace/sources`. Every pre-existing URL, method, request body and
response body is unchanged.

The last three exist because the DIWO browser used to call CUQA :8080 and
SCTVA :8002 itself. It no longer does: every agent hand-off is server-side, so
the frontend needs one base URL and one CORS origin, and there is exactly one
place where an agent contract can drift.

## Notes

* `runtime/` is git-ignored and untracked. The existing database and reports
  stayed on disk when they were untracked, so no workflow history was lost.
* The JSON shapes exchanged with the three agents are documented in
  `shared/contracts/`, built from payloads captured from a live session.
