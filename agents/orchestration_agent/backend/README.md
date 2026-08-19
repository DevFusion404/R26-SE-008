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
    └── fixtures/cquaAgent.json
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
```

The end-to-end check drives the whole workflow through the Flask test client
against a throwaway database, with the other agents deliberately not running,
so it also exercises the fallback paths. It asserts the two hand-offs that
matter most:

* **step 4 → 7** — the report forwarded to RDP contains only the smells the
  developer kept.
* **step 10 → 12** — the plan sent onward contains only the steps the
  developer approved.

and the rollback behaviour: a rejected file is archived as its original
source, not as the refactored one.

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
| GET        | `/api/cuqa/status`, `/api/rdp/status`, `/api/sctva/status` | agent reachability          |
| GET, POST  | `/api/cuqa/quality-report`                    | proxy Agent 1's quality report            |
| POST       | `/api/diwo/apply-and-push`                    | write the project to a git branch         |
| GET        | `/api/health`, `/`                            | backend health                            |

`/api/sctva/status` is the only endpoint added in the 2026-08 restructure;
every other URL, method, request body and response body is unchanged.

## Notes

* `runtime/database/diwo_audit.db` and `runtime/reports/*.json` are still
  tracked in git from before `runtime/` existed. Untrack them with
  `git rm --cached <path>` if the workflow history should stop being
  versioned; the files themselves stay on disk.
* The approved plan is still posted to SCTVA from the browser
  (`frontend/src/pages/diwo/services/sctvaApi.js`). `clients/sctva_client.py`
  is the seam for moving that behind the orchestrator without inventing a
  second integration path.
