# RefactorIQ End-to-End API Endpoint Documentation

**Project:** R26-SE-008 — An Agentic Intelligent Code Refactoring Assistant for Legacy Systems  
**Generated from:** current uploaded `R26-SE-008-main.zip` source tree  
**Scope:** CUQA, RDP, SCTVA, DIWO/Orchestration, User Management, inter-agent contracts, and the integrated request flow.

---

## 1. System API Architecture

```text
Developer / React Frontend
        |
        | repository upload / GitHub URL
        v
     CUQA Agent
     :8080 local
        |
        | CUQA quality report
        v
DIWO / Orchestration Agent
     :5001 local
        |
        | filtered CUQA report
        v
      RDP Agent
      :5000 local
        |
        | refactoring plan
        v
DIWO / Orchestration Agent
        |
        | approved plan + source files
        v
      SCTVA Agent
      :8002 local
        |
        | transformation + validation result
        v
DIWO / Orchestration Agent
        |
        +--> comparison / audit / ZIP / Git
```

The formal inter-agent contract chain in `shared/contracts/` is:

```text
CUQAReport
   -> FilteredCUQAReport
   -> RefactoringPlan
   -> ApprovedPlan
   -> SCTVARequest
   -> TransformationResult
   -> Workflow
```

Human decisions are carried at two gates:

1. **Smell approval**: rejected CUQA smells are removed before RDP planning.
2. **Plan approval**: rejected RDP steps are removed before SCTVA execution.

A final rejected transformed file is reverted to the original content before archive/Git output.

---

## 2. Base URLs

| Service | Local base URL | Hosted/default URL found in current source |
|---|---|---|
| CUQA | `http://localhost:8080` | `https://cuqaagent.gentleglacier-0204e61b.southeastasia.azurecontainerapps.io` |
| RDP | `http://localhost:5000` | `https://rdpagent.gentleglacier-0204e61b.southeastasia.azurecontainerapps.io` |
| SCTVA | `http://localhost:8002` | `https://sctvaagent.gentleglacier-0204e61b.southeastasia.azurecontainerapps.io` |
| DIWO/Orchestration | `http://localhost:5001` (`/api` prefix for blueprints) | frontend hosted default: `https://diwoagent.gentleglacier-0204e61b.southeastasia.azurecontainerapps.io/api` |
| User Management | frontend expects `http://localhost:6000` | no hosted default is defined in `frontend/src/config/env.js` |

### Environment variables used by DIWO backend

- `CUQA_AGENT_URL`
- `RDP_AGENT_URL`
- `SCTVA_AGENT_URL`
- `DIWO_HOST`
- `DIWO_PORT`
- `DIWO_CORS_ORIGINS`
- `DIWO_SECRET_KEY`
- `DIWO_RUNTIME_DIR`
- `DIWO_DB_PATH`

### Frontend runtime/build variables

- `VITE_CUQA_AGENT_API_URL`
- `VITE_CUQA_API_URL`
- `VITE_RDP_AGENT_API_URL`
- `VITE_TRANSFORMATION_AGENT_API_URL`
- `VITE_DIWO_API_URL`
- `VITE_API_URL`
- `VITE_USER_MANAGEMENT_API_URL`

---

# 3. End-to-End Integrated Flow

## Stage 0 — Optional authentication

```text
POST /api/auth/register
POST /api/auth/login
```

Protected user-management routes use:

```http
Authorization: Bearer <access_token>
```

The CUQA/RDP/SCTVA/DIWO agent APIs themselves do not currently enforce this Bearer token.

---

## Stage 1 — Load a repository into CUQA

Choose one:

### ZIP

```http
POST CUQA /api/upload-zip
Content-Type: multipart/form-data
```

Form field:

```text
file=<repository.zip>
```

### GitHub

```http
POST CUQA /api/github-repo
Content-Type: application/json
```

```json
{
  "url": "https://github.com/owner/repository"
}
```

CUQA scans `.py`, `.java`, `.c`, and `.h` files and stores one active server-side workspace.

---

## Stage 2 — Start DIWO from CUQA

```http
POST DIWO /api/workflows/from-cuqa
```

Optional body:

```json
{
  "file_path": null,
  "target": null,
  "language": null
}
```

DIWO internally calls:

```text
POST CUQA /api/quality-report
```

The result becomes the initial workflow and enters `smell_review`.

---

## Stage 3 — Review / preview detected smells

Useful endpoints:

```text
GET  /api/workflows/{wf_id}
GET  /api/workflows/{wf_id}/smell-categories
GET  /api/workflows/{wf_id}/smell-impacts
POST /api/workflows/{wf_id}/selection-impact
POST /api/workflows/{wf_id}/optimise-selection
POST /api/workflows/{wf_id}/smell-selection-pass
```

None of the preview/impact endpoints has to advance the workflow.

---

## Stage 4 — Commit smell selection and generate RDP plan

```http
POST DIWO /api/workflows/{wf_id}/select-smells
```

Example:

```json
{
  "selected_ids": ["smell_001", "smell_004"],
  "selected_files": ["src/OrderProcessor.java"],
  "selection_mode": "smell",
  "feedback": {
    "reason": "Prioritize maintainability issues"
  }
}
```

DIWO builds a **FilteredCUQAReport** containing only accepted smells and internally calls:

```http
POST RDP /generate
```

RDP returns:

```json
{
  "success": true,
  "plan": {
    "plan_id": "...",
    "target": "...",
    "steps": [],
    "summary": "..."
  },
  "trace": {}
}
```

DIWO stores/enriches the plan and enters `plan_approval`.

---

## Stage 5 — Review / approve RDP plan

Optional preference re-ranking:

```http
POST /api/workflows/{wf_id}/plan-preference-update
```

Then submit the plan decision:

```http
POST /api/workflows/{wf_id}/plan-decision
```

Example:

```json
{
  "decision": "approve",
  "decisions": {
    "1": "approve",
    "2": "reject",
    "3": "manual"
  },
  "feedback": {
    "reason": "Approved safe automated steps",
    "rating": 4
  }
}
```

Valid top-level decisions:

- `approve`
- `reject`
- `modify`

Rejected/manual steps are not forwarded as executable approved steps.

---

## Stage 6 — Execute approved plan through SCTVA

```http
POST DIWO /api/workflows/{wf_id}/transform
```

Optional body:

```json
{
  "language": "python",
  "request_id": "optional-client-id",
  "execution_options": {
    "strict_mode": true,
    "enable_behavior_tests": true,
    "timeout_seconds": 10,
    "require_compilation": false,
    "rollback_on_behavior_failure": true
  }
}
```

Internally DIWO performs two SCTVA calls:

1. Fetch source text:

```http
POST SCTVA /sctva/cuqa-sources
```

2. Execute:

```http
POST SCTVA /sctva/execute
```

DIWO translates RDP names such as `Extract Method` into SCTVA action names such as `extract_method`.
Unmappable approved steps become explicit `noop` actions rather than silently disappearing.

---

## Stage 7 — Developer accepts/rejects transformed files

```http
POST DIWO /api/workflows/{wf_id}/transformation-decision
```

Example:

```json
{
  "decision": "accept",
  "accepted_files": ["src/a.py"],
  "rejected_files": ["src/b.py"],
  "written_files": ["src/a.py", "src/b.py"],
  "files": [
    {
      "path": "src/a.py",
      "content": "<accepted refactored source>"
    },
    {
      "path": "src/b.py",
      "content": "<original source because developer rejected refactoring>"
    }
  ],
  "feedback": {
    "rating": 4,
    "reason": "Accepted one file, reverted one file"
  }
}
```

Valid decisions:

- `accept`
- `rollback`

If `download: "zip"` is supplied and file contents are available, the endpoint returns ZIP bytes directly.

---

## Stage 8 — Results, audit, download and Git

```text
GET  /api/workflows/{wf_id}/refactored-archive
GET  /api/workflows/{wf_id}/audit-logs
POST /api/diwo/apply-and-push
POST /api/workflows/{wf_id}/complete
```

---

# 4. CUQA Agent API

**Framework:** FastAPI  
**Local port:** `8080`  
**Supported source extensions:** `.py`, `.java`, `.c`, `.h`

## CUQA endpoint summary

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/` | Agent/version status |
| GET | `/api/health` | Health + workspace-loaded state |
| POST | `/api/upload-zip` | Upload/extract repository ZIP |
| POST | `/api/github-repo` | Load public GitHub repository |
| GET | `/api/project-structure` | Repository tree |
| POST | `/api/parse-ast` | Parse one source file into AST JSON |
| POST | `/api/quality-report` | File or repository quality report |
| GET | `/api/files` | List source files |
| GET | `/api/repository-overview` | Beginner-friendly structural repository overview |
| POST | `/api/update-workspace` | Overwrite workspace files, e.g. for re-analysis |
| POST | `/api/source-files` | Fetch multiple raw source files |
| POST | `/api/cuqa/source-files` | Alias of `/api/source-files` |
| POST | `/api/source-file` | Fetch one raw source file |
| POST | `/api/cuqa/source-file` | Alias |
| GET | `/api/raw-source?file_path=...` | Fetch one raw source file |
| GET | `/api/source-file?file_path=...` | Alias |
| GET | `/api/cuqa/raw-source?file_path=...` | Alias |
| GET | `/api/cuqa/source-file?file_path=...` | Alias |

## 4.1 GET `/`

Response:

```json
{
  "agent": "CUQA",
  "status": "running",
  "version": "1.0.0"
}
```

## 4.2 GET `/api/health`

Response:

```json
{
  "status": "ok",
  "workspace_loaded": true
}
```

## 4.3 POST `/api/upload-zip`

**Content-Type:** `multipart/form-data`  
**Field:** `file`  
**Maximum ZIP size:** 500 MB

Response shape:

```json
{
  "message": "ZIP uploaded and extracted successfully.",
  "repo_name": "sample",
  "files_found": 12,
  "source_files": ["src/a.py"],
  "language_breakdown": {"python": 10, "c": 2},
  "detected_languages": ["python", "c"],
  "primary_language": "python",
  "is_polyglot": true
}
```

Important errors:

- `400` not `.zip` / invalid ZIP
- `413` > 500 MB
- FastAPI `422` when required multipart field is missing

## 4.4 POST `/api/github-repo`

Request:

```json
{
  "url": "https://github.com/owner/repo"
}
```

Rules:

- exact hostname must be `github.com`
- `.git` suffix is normalized away
- tries branches `main`, `master`, `develop`, `trunk`
- public repos only in this implementation

Response additionally includes `github_url`.

Errors:

- `400` unsupported hostname
- `413` repository archive too large
- `502` repository/branch cannot be downloaded

## 4.5 GET `/api/project-structure`

Requires a loaded workspace.

Response:

```json
{
  "repo_name": "repo",
  "source": "zip",
  "total_source_files": 20,
  "tree": {
    "name": "repo",
    "type": "directory",
    "path": "",
    "children": []
  }
}
```

## 4.6 POST `/api/parse-ast`

Request:

```json
{
  "file_path": "src/example.py"
}
```

Response:

```json
{
  "parsed": {},
  "summary": {},
  "source_code": "..."
}
```

Errors:

- `400` no workspace / missing `file_path`
- `404` file not found

## 4.7 POST `/api/quality-report`

Whole repository:

```json
{}
```

One file:

```json
{
  "file_path": "src/example.py"
}
```

Repository response wrapper:

```json
{
  "type": "repository",
  "report": {
    "summary": {},
    "files": [],
    "repo_name": "repo"
  }
}
```

Single-file wrapper:

```json
{
  "type": "file",
  "report": {}
}
```

The repository endpoint currently caps analysis to the first **50 discovered source files**.

Canonical normalized CUQA report fields include:

```json
{
  "summary": {
    "files_analyzed": 0,
    "total_lines_of_code": 0,
    "total_code_smells": 0,
    "smell_severity": {"high": 0, "medium": 0, "low": 0},
    "average_quality_score": 100
  },
  "files": [
    {
      "file": "example.py",
      "relative_path": "src/example.py",
      "language": "python",
      "metrics": {},
      "code_smells": [
        {
          "type": "LongMethod",
          "severity": "high",
          "message": "...",
          "line": 10,
          "entity": "calculate_total"
        }
      ],
      "smell_summary": {"high": 1, "medium": 0, "low": 0},
      "quality_score": 85
    }
  ]
}
```

## 4.8 GET `/api/files`

Response:

```json
{
  "repo_name": "repo",
  "files": ["src/a.py", "src/B.java"],
  "total": 2
}
```

## 4.9 GET `/api/repository-overview`

Returns static structural understanding including:

- repository/file/LOC statistics
- language distribution
- build/dependency tools
- likely entry points
- important directories/files
- newcomer reading path
- dependency graph
- architectural pattern clues
- subproject/monorepo information

Errors:

- `400` no repository loaded
- `500` repository analysis failed

## 4.10 POST `/api/update-workspace`

Request:

```json
{
  "files": [
    {
      "file_path": "src/example.py",
      "content": "<new source>"
    }
  ]
}
```

Response:

```json
{
  "status": "success",
  "updated_files": 1,
  "errors": []
}
```

This endpoint can support an actual post-refactoring CUQA re-analysis workflow.

## 4.11 POST `/api/source-files` and `/api/cuqa/source-files`

Request:

```json
{
  "file_paths": ["src/a.py", "src/B.java"]
}
```

Response:

```json
{
  "files": [
    {
      "file_name": "src/a.py",
      "file_path": "src/a.py",
      "language": "python",
      "source_code": "...",
      "source_mode": "raw"
    }
  ],
  "imported": 1,
  "total": 2,
  "missing": ["src/B.java"],
  "source": "cuqa_workspace"
}
```

## 4.12 POST `/api/source-file` and `/api/cuqa/source-file`

Request:

```json
{"file_path": "src/a.py"}
```

Returns one source-file object.

## 4.13 GET source-file aliases

All use query parameter `file_path`:

```text
/api/raw-source
/api/source-file
/api/cuqa/raw-source
/api/cuqa/source-file
```

Example:

```http
GET /api/raw-source?file_path=src/a.py
```

---

# 5. RDP Agent API

**Framework:** Flask  
**Local port:** `5000`

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/` | HTML upload page |
| GET | `/health` | Health |
| GET | `/api/health` | Health alias |
| POST | `/generate` | Generate refactoring plan + decision trace |

## 5.1 GET `/health` and `/api/health`

```json
{
  "status": "ok",
  "service": "rdp-agent",
  "message": "RDP agent is healthy"
}
```

## 5.2 POST `/generate`

Accepts either:

1. `multipart/form-data` with a `.json` file, or
2. `application/json` body.

### Native RDP input

```json
{
  "target": "src/OrderProcessor.java",
  "smells": [
    {
      "id": "smell_001",
      "type": "LongMethod",
      "location": {
        "class": "OrderProcessor",
        "method": "calculateTotal",
        "lines": [20, 80]
      },
      "metrics": {
        "lines_of_code": 61,
        "cyclomatic_complexity": 14
      },
      "severity": "high",
      "details": "Long method detected"
    }
  ],
  "metrics_summary": {}
}
```

### CUQA-shaped input

RDP also accepts CUQA repository shape:

```json
{
  "files": [
    {
      "relative_path": "src/example.py",
      "code_smells": [],
      "metrics": {}
    }
  ],
  "summary": {}
}
```

If `files` is present and `smells` is absent, RDP translates CUQA format internally.

### Response

```json
{
  "success": true,
  "plan": {
    "plan_id": "plan_...",
    "target": "...",
    "steps": [
      {
        "step_id": 1,
        "smell_id": "smell_001",
        "refactoring": "Extract Method",
        "target": {},
        "parameters": {},
        "explanation": "..."
      }
    ],
    "summary": "..."
  },
  "trace": {}
}
```

Errors:

- `400` no JSON/file, wrong extension, malformed JSON
- `500` plan generation exception

---

# 6. SCTVA Agent API

**Framework:** Flask  
**Local port:** `8002`

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/` | HTML interface |
| GET | `/health` | Basic liveness |
| GET | `/sctva/health` | Capabilities / supported actions |
| POST | `/sctva/cuqa-sources` | Read repository source from CUQA temp workspace |
| POST | `/sctva/execute` | Execute transformation + validation |
| POST | `/sctva/execute_from_rdp` | Current alias-like execution path; RDP adapter code is commented out |

## 6.1 GET `/health`

```json
{
  "status": "ok",
  "service": "sctva"
}
```

## 6.2 GET `/sctva/health`

Returns:

```json
{
  "status": "ok",
  "service": "sctva",
  "implementation": "sctva-real-transformers",
  "execution_contract_version": 2,
  "supported_actions": [],
  "supported_capabilities": [
    "line_based_remove_dead_code",
    "source_range_extract_method",
    "c_safe_unsafe_function_replacement",
    "c_global_variable_encapsulation",
    "cuqa_temp_workspace_source_import",
    "sctva_internal_refactoring_detector",
    "multiline_statement_normalization"
  ]
}
```

Use the actual returned `supported_actions` list as the live capability source rather than hard-coding it in clients.

## 6.3 POST `/sctva/cuqa-sources`

Request:

```json
{
  "file_paths": ["src/a.py", "src/B.java"]
}
```

Also accepts `files` as an alias input key.

Limits/behavior:

- takes at most the first 1000 requested paths per direct call
- each source file is limited to 5 MB

Response:

```json
{
  "files": [
    {
      "file_name": "src/a.py",
      "source_code": "...",
      "language": "python",
      "source_mode": "raw",
      "origin": "cuqa_temp_workspace"
    }
  ],
  "missing": [],
  "imported": 1,
  "total": 1,
  "source": "cuqa_temp_workspace",
  "workspace_candidates_scanned": 1
}
```

## 6.4 POST `/sctva/execute`

Canonical request contract:

```json
{
  "request_id": "sctva_diwo_123",
  "language": "python",
  "source_files": [
    {
      "file_name": "src/example.py",
      "source_code": "...",
      "language": "python",
      "source_mode": "raw",
      "origin": "cuqa_temp_workspace"
    }
  ],
  "refactoring_plan": {
    "plan_id": "plan_123",
    "actions": [
      {
        "action_type": "extract_method",
        "parameters": {
          "source_file": "src/example.py",
          "method": "calculate_total",
          "new_method_name": "calculate_totalCore",
          "start_line": 20,
          "end_line": 40
        },
        "source_step_id": 1,
        "source_refactoring": "Extract Method",
        "warnings": []
      }
    ],
    "behavior_tests": [],
    "metadata": {}
  },
  "execution_options": {
    "strict_mode": true,
    "enable_behavior_tests": true,
    "timeout_seconds": 10,
    "require_compilation": false,
    "rollback_on_behavior_failure": true,
    "enable_sctva_auto_refactoring": false,
    "max_parallel_files": 0
  }
}
```

A single-file request may use top-level `source_code` instead of `source_files`, but DIWO uses the multi-file form.

Supported languages in the current contract:

- `python`
- `java`
- `c`

### Multi-file response shape

```json
{
  "request_id": "sctva_diwo_123",
  "status": "FULL_SUCCESS",
  "language": "python",
  "success": true,
  "rollback_occurred": false,
  "transformation_applied": true,
  "total_replacements": 1,
  "confidence_score": 0.95,
  "confidence_applicable": true,
  "validation_score": 1.0,
  "file_summary": {
    "total": 1,
    "succeeded": 1,
    "applied": 1,
    "rolled_back": 0,
    "not_applied": 0
  },
  "file_results": [],
  "transformed_workspace_files": [],
  "artifact_persistence": {
    "mode": "browser_storage",
    "backend_results_folder_disabled": true
  }
}
```

Possible `status` values visible in current logic include:

- `FULL_SUCCESS`
- `PARTIAL_SUCCESS`
- `REVIEW_REQUIRED`
- `FAILED`

Errors:

- `400` contract validation failure
- `500` internal execution error

## 6.5 POST `/sctva/execute_from_rdp`

Current implementation simply executes the supplied SCTVA-shaped payload. The older planner-adapter block inside this endpoint is commented out.

Errors may include:

- `400` contract validation
- `422` planner-adapter error if that path is re-enabled/raised
- `500` internal error

---

# 7. DIWO / Orchestration Agent API

**Framework:** Flask  
**Local port:** `5001`  
**Blueprint prefix:** `/api`

## DIWO endpoint summary

| Method | Full endpoint | Purpose |
|---|---|---|
| GET | `/` | DIWO backend root status |
| GET | `/api/health` | DIWO API health |
| GET | `/api/cuqa/status` | CUQA reachability/workspace status |
| GET | `/api/rdp/status` | RDP reachability |
| GET | `/api/sctva/status` | SCTVA reachability |
| GET/POST | `/api/cuqa/quality-report` | Proxy/normalize CUQA quality report |
| GET | `/api/cuqa/project-structure` | Proxy CUQA project tree |
| POST | `/api/workspace/sources` | Proxy workspace source retrieval |
| GET | `/api/workflows` | List workflows |
| POST | `/api/workflows` | Create workflow from client smell list |
| POST | `/api/workflows/from-cuqa` | Create workflow from live CUQA report |
| GET | `/api/workflows/{wf_id}` | Get workflow state |
| POST | `/api/workflows/{wf_id}/smell-selection-pass` | Preview filtered smell report |
| GET | `/api/workflows/{wf_id}/smell-categories` | Smell taxonomy/categories |
| POST | `/api/workflows/{wf_id}/save-updated-report` | Save filtered report JSON |
| POST | `/api/workflows/{wf_id}/reset-to-smell-review` | Return to Stage 1 |
| GET | `/api/workflows/{wf_id}/smell-impacts` | Per-smell impact records |
| POST | `/api/workflows/{wf_id}/selection-impact` | What-if selection impact |
| POST | `/api/workflows/{wf_id}/optimise-selection` | Suggest smells under time budget |
| POST | `/api/workflows/{wf_id}/select-smells` | Commit selection + invoke RDP |
| POST | `/api/workflows/{wf_id}/plan-preference-update` | Re-rank/regenerate plan by preferences |
| POST | `/api/workflows/{wf_id}/reset-to-plan-approval` | Return to Stage 2 |
| POST | `/api/workflows/{wf_id}/plan-decision` | Approve/reject/modify plan |
| POST | `/api/workflows/{wf_id}/transform` | Execute approved plan using SCTVA |
| POST | `/api/workflows/{wf_id}/transformation-decision` | Accept/rollback transformed result |
| GET | `/api/workflows/{wf_id}/refactored-archive` | Download stored ZIP |
| POST | `/api/workflows/{wf_id}/complete` | Complete workflow |
| GET | `/api/workflows/{wf_id}/audit-logs` | Audit trail |
| GET | `/api/feedback/export` | Export feedback dataset |
| POST | `/api/diwo/apply-and-push` | Apply project to Git branch/commit/push |

## 7.1 GET `/`

```json
{
  "status": "DIWO Agent Backend Running",
  "version": "1.0.0"
}
```

## 7.2 GET `/api/health`

```json
{
  "status": "ok",
  "agent": "DIWO",
  "version": "1.1.0"
}
```

## 7.3 GET `/api/cuqa/status`

Shape:

```json
{
  "reachable": true,
  "repo_loaded": true,
  "repo_name": "repo",
  "file_count": 25,
  "cuqa_url": "...",
  "message": "CUQA workspace loaded."
}
```

## 7.4 GET `/api/rdp/status`

Shape:

```json
{
  "reachable": true,
  "rdp_url": "...",
  "message": "RDP agent is running."
}
```

## 7.5 GET `/api/sctva/status`

Shape:

```json
{
  "reachable": true,
  "sctva_url": "...",
  "message": "SCTVA agent is running.",
  "detail": {
    "status": "ok",
    "service": "sctva"
  }
}
```

## 7.6 GET/POST `/api/cuqa/quality-report`

Optional body/query:

```json
{"file_path": "src/a.py"}
```

Response:

```json
{
  "status": "ok",
  "source": "cuqa",
  "cuqa_url": "...",
  "report_type": "repository",
  "report": {},
  "smells": [],
  "smell_count": 0,
  "language": "python"
}
```

CUQA errors are proxied with:

```json
{
  "error": "...",
  "cuqa_url": "...",
  "reachable": true
}
```

## 7.7 GET `/api/cuqa/project-structure`

Response:

```json
{
  "repo_name": "repo",
  "source": "zip",
  "total_source_files": 20,
  "tree": {},
  "cuqa_url": "..."
}
```

## 7.8 POST `/api/workspace/sources`

Request:

```json
{
  "file_paths": ["src/a.py"]
}
```

The route proxies SCTVA `/sctva/cuqa-sources`.

## 7.9 GET `/api/workflows`

Returns an array of workflow summaries:

```json
[
  {
    "id": "wf_abc",
    "target": "repo",
    "language": "python",
    "status": "smell_review",
    "created_at": "...",
    "updated_at": "..."
  }
]
```

## 7.10 POST `/api/workflows`

Creates a workflow from a caller-supplied smell list.

Request:

```json
{
  "target": "repo",
  "language": "python",
  "smells": [
    {
      "id": "smell_1",
      "type": "LongMethod",
      "severity": "high"
    }
  ]
}
```

`smells` must be a non-empty list and each smell must contain `type`.

Response `201`:

```json
{
  "workflow_id": "wf_...",
  "status": "smell_review",
  "message": "Workflow started. Developer can now review detected smells.",
  "metrics_before": {}
}
```

## 7.11 POST `/api/workflows/from-cuqa`

Optional request:

```json
{
  "file_path": null,
  "target": null,
  "language": null
}
```

Response `201` includes:

```json
{
  "workflow_id": "wf_...",
  "status": "smell_review",
  "source": "cuqa",
  "cuqa_url": "...",
  "target": "repo",
  "language": "python",
  "report": {},
  "smells": [],
  "smell_count": 5,
  "metrics_before": {},
  "message": "..."
}
```

Returns `400` if the CUQA report contains no smells.

## 7.12 GET `/api/workflows/{wf_id}`

Response:

```json
{
  "id": "wf_...",
  "target": "repo",
  "language": "python",
  "status": "plan_approval",
  "created_at": "...",
  "updated_at": "...",
  "smells": [],
  "selected_smells": [],
  "updated_smells": [],
  "planning_input": {},
  "plan": {},
  "transformation_result": {},
  "metrics_before": {},
  "metrics_after": {}
}
```

## 7.13 POST `/api/workflows/{wf_id}/smell-selection-pass`

Read-only preview.

Accepted body fields:

```json
{
  "selected_ids": [],
  "selected_files": [],
  "selected_file_paths": [],
  "selected_smells": [],
  "selection_mode": "smell"
}
```

Response includes:

- `all_smells`
- `selected`
- `excluded`
- `updated_smells`
- `updated_report`
- `planning_input`
- `rdp_plan_input`
- `status: "smell_review"`
- `selection_mode`
- `selected_ids`

No RDP call is made here.

## 7.14 GET `/api/workflows/{wf_id}/smell-categories`

Returns taxonomy/category grouping plus the original CUQA repository overview if available.

## 7.15 POST `/api/workflows/{wf_id}/save-updated-report`

Request:

```json
{
  "updated_report": {}
}
```

Writes the JSON under the DIWO runtime reports directory.

## 7.16 POST `/api/workflows/{wf_id}/reset-to-smell-review`

Optional:

```json
{
  "reason": "Need to change smell selection"
}
```

or:

```json
{
  "feedback": {"reason": "Need to change smell selection"}
}
```

Clears stored plan/selection state and restores `smell_review`.

## 7.17 GET `/api/workflows/{wf_id}/smell-impacts`

Optional query:

```text
?refresh=1
```

Response:

```json
{
  "workflow_id": "wf_...",
  "model_version": "...",
  "tier": "static",
  "count": 5,
  "executable": 3,
  "advisory": 2,
  "records": []
}
```

## 7.18 POST `/api/workflows/{wf_id}/selection-impact`

Request may use:

```json
{
  "selected_ids": [],
  "selected_files": [],
  "selected_smells": []
}
```

Read-only what-if analysis; does not call RDP.

## 7.19 POST `/api/workflows/{wf_id}/optimise-selection`

Request:

```json
{
  "preset": "best_value",
  "budget_minutes": 30
}
```

Valid presets:

- `best_value`
- `safe_wins`
- `stop_bleeding`

This is a suggestion only; it does not persist the selection.

## 7.20 POST `/api/workflows/{wf_id}/select-smells`

Request fields:

```json
{
  "selected_ids": [],
  "selected_files": [],
  "selected_file_paths": [],
  "selected_smells": [],
  "selection_mode": "file|smell|category",
  "feedback": {
    "reason": "..."
  }
}
```

Response includes:

```json
{
  "status": "plan_approval",
  "selected_count": 2,
  "excluded_count": 3,
  "plan": {},
  "trace": {},
  "plan_source": "rdp_agent",
  "plan_warning": null,
  "rdp_url": "...",
  "selected_ids": [],
  "selected_files": [],
  "selection_mode": "smell",
  "updated_report": {},
  "planning_input": {},
  "message": "..."
}
```

This is the integrated endpoint that calls RDP.

## 7.21 POST `/api/workflows/{wf_id}/plan-preference-update`

Request:

```json
{
  "decisions": {
    "1": "approve",
    "2": "reject"
  },
  "preferences": {
    "developer_strategy": "balanced",
    "risk_tolerance": "balanced",
    "impact_focus": "high",
    "preferred_refactorings": ["Extract Method"]
  }
}
```

`developer_strategy` values described by the route:

- `safety_first`
- `balanced`
- `max_improvement`

Response:

```json
{
  "status": "plan_approval",
  "message": "Updated planning report generated using developer preferences.",
  "updated_planning_report": {},
  "developer_strategy": "balanced",
  "decision_support_summary": {}
}
```

## 7.22 POST `/api/workflows/{wf_id}/reset-to-plan-approval`

Optional reason/feedback body. Restores `plan_full_json` where available and clears transformation/after-metric state.

## 7.23 POST `/api/workflows/{wf_id}/plan-decision`

Top-level request:

```json
{
  "decision": "approve",
  "decisions": {
    "1": "approve",
    "2": "reject"
  },
  "modified_steps": [],
  "feedback": {
    "reason": "...",
    "rating": 5,
    "step_reasons": {}
  }
}
```

Valid `decision`:

- `approve`
- `reject`
- `modify`

### Reject response

```json
{
  "status": "rolled_back",
  "message": "Plan rejected. Workflow terminated."
}
```

### Modify response

```json
{
  "status": "plan_approval",
  "plan": {},
  "message": "Plan reduced to the approved steps. Please approve to proceed."
}
```

### Approve response

```json
{
  "status": "transformation",
  "approved_plan": {},
  "transformation_result": {},
  "refactored_code": "...",
  "diff_rows": [],
  "files": [],
  "metrics_after": {},
  "message": "Plan approved. Forward the approved plan to the Transformation Agent."
}
```

**Implementation note:** this approval route still creates a simulated transformation summary/heuristic `metrics_after`; the real SCTVA execution is performed separately by `/api/workflows/{wf_id}/transform`.

## 7.24 POST `/api/workflows/{wf_id}/transform`

Optional body:

```json
{
  "plan": {},
  "language": "java",
  "request_id": "...",
  "execution_options": {}
}
```

If `plan` is omitted, the stored approved-only plan is used.

Response:

```json
{
  "status": "transformation",
  "result": {},
  "request": {},
  "mapping": {
    "plan": {},
    "warnings": [],
    "executableCount": 1,
    "noopCount": 0
  },
  "sources": {
    "imported": 1,
    "missing": [],
    "total": 1
  },
  "sctva_url": "...",
  "executed_at": "..."
}
```

The normalized `result` uses camelCase fields such as:

- `requestId`
- `success`
- `rollbackOccurred`
- `transformationApplied`
- `confidenceScore`
- `validationScore`
- `totalReplacements`
- `fileSummary`
- `files[]`
- `refactored_code`

## 7.25 POST `/api/workflows/{wf_id}/transformation-decision`

Request:

```json
{
  "decision": "accept",
  "accepted_files": [],
  "rejected_files": [],
  "written_files": [],
  "files": [
    {
      "path": "src/a.py",
      "content": "...",
      "state": "accepted"
    }
  ],
  "download": null,
  "feedback": {
    "reason": "...",
    "rating": 5
  }
}
```

Valid decision:

- `accept`
- `rollback`

Accept response:

```json
{
  "status": "comparison",
  "metrics_before": {},
  "metrics_after": {},
  "accepted_files": [],
  "rejected_files": [],
  "written_files": [],
  "archive": {},
  "archive_error": null,
  "message": "Changes accepted. View comparison report."
}
```

Rollback response:

```json
{
  "status": "rolled_back",
  "message": "Rolled back to snapshot ..."
}
```

If `download: "zip"`, returns `application/zip` instead of JSON when an archive can be built.

## 7.26 GET `/api/workflows/{wf_id}/refactored-archive`

Returns:

```http
Content-Type: application/zip
Content-Disposition: attachment
```

The archive contains repository-relative paths and rejected transformed files are represented by their original source content when the caller supplied that final content.

## 7.27 POST `/api/workflows/{wf_id}/complete`

Allowed only from `comparison` stage.

Optional:

```json
{
  "notes": "Final developer notes"
}
```

Response:

```json
{
  "status": "completed",
  "message": "Workflow successfully completed."
}
```

## 7.28 GET `/api/workflows/{wf_id}/audit-logs`

Response:

```json
[
  {
    "id": 1,
    "stage": "plan_approval",
    "action": "plan_approved",
    "actor": "developer",
    "details": {},
    "timestamp": "..."
  }
]
```

## 7.29 GET `/api/feedback/export`

Response:

```json
{
  "count": 10,
  "data": []
}
```

## 7.30 POST `/api/diwo/apply-and-push`

Request:

```json
{
  "files": [
    {
      "path": "src/a.py",
      "after": "<final source>"
    }
  ],
  "branch_name": "refactoring/diwo-changes",
  "repository_path": "https://github.com/user/repo",
  "commit_message": "Apply approved RefactorIQ changes",
  "commit": true,
  "push": true
}
```

`repository_path` may be a GitHub URL or local path.

The `files` collection is intended to represent the **whole final project**, not only changed files.

Git failures use the DIWO error shape:

```json
{"error": "..."}
```

---

# 8. User Management API

**Blueprint prefix:** `/api/auth`  
**Backend:** Flask + Supabase  
**Authentication:** Bearer token for protected routes

> Current source inconsistency: `user_management/app.py` defaults its process port to **5005**, while comments/frontend configuration refer to **6000**. Align this before documenting a production URL.

| Method | Endpoint | Auth | Purpose |
|---|---|---|---|
| GET | `/` | No | Service overview |
| GET | `/api/auth/health` | No | Health |
| POST | `/api/auth/register` | No | Register |
| POST | `/api/auth/login` | No | Login |
| POST | `/api/auth/logout` | Bearer | Logout |
| GET | `/api/auth/profile` | Bearer | Get profile |
| PUT | `/api/auth/profile` | Bearer | Update profile |
| DELETE | `/api/auth/account` | Bearer | Delete account |
| GET | `/api/auth/users` | Admin Bearer | List users |
| PUT | `/api/auth/users/{user_id}/role` | Admin Bearer | Change role |

## 8.1 POST `/api/auth/register`

Request:

```json
{
  "email": "dev@example.com",
  "password": "...",
  "full_name": "Developer Name",
  "role": "user"
}
```

Success `201`:

```json
{
  "success": true,
  "data": {
    "user": {},
    "profile": {},
    "session": {
      "access_token": "...",
      "refresh_token": "..."
    }
  }
}
```

## 8.2 POST `/api/auth/login`

Request:

```json
{
  "email": "dev@example.com",
  "password": "..."
}
```

Success:

```json
{
  "success": true,
  "data": {
    "user": {},
    "profile": {},
    "session": {
      "access_token": "...",
      "refresh_token": "...",
      "expires_in": 3600,
      "token_type": "bearer"
    }
  }
}
```

## 8.3 Protected-route header

```http
Authorization: Bearer <access_token>
Content-Type: application/json
```

## 8.4 PUT `/api/auth/profile`

Allowed service fields are currently:

- `full_name`
- `email`

## 8.5 PUT `/api/auth/users/{user_id}/role`

Request:

```json
{"role": "admin"}
```

Allowed roles:

- `user`
- `admin`

---

# 9. Formal Inter-Agent JSON Contracts

Located in:

```text
shared/contracts/
```

Files:

- `cuqa-report.schema.json`
- `filtered-cuqa-report.schema.json`
- `rdp-plan.schema.json`
- `approved-plan.schema.json`
- `sctva-request.schema.json`
- `sctva-result.schema.json`
- `workflow.schema.json`

## CUQAReport

Required top-level fields:

```text
summary
files
```

## FilteredCUQAReport

Required:

```text
filtered
files
summary
```

`summary` includes:

- `selected_smell_ids`
- `selected_count`
- `excluded_count`

## RefactoringPlan

Required:

```text
plan_id
steps
summary
```

## ApprovedPlan

Required:

```text
steps
summary
approval
```

`approval` records:

- `approved_step_ids`
- `rejected_step_ids`
- `pending_step_ids`
- counts
- decision timestamp

## SCTVARequest

Required by formal shared schema:

```text
request_id
language
source_files
refactoring_plan
execution_options
```

The SCTVA runtime contract also supports single-file `source_code` when `source_files` is not supplied.

## TransformationResult

Normalized DIWO-side contract requires:

```text
requestId
success
files
```

and can contain validation, confidence, safety and raw SCTVA output.

## Workflow

Required:

```text
id
target
language
status
created_at
updated_at
```

Main stages seen in current code:

```text
smell_review
smell_selection
plan_approval
transformation
comparison
completed
rolled_back
```

---

# 10. Common Error Shapes

## CUQA / FastAPI

Typical error:

```json
{
  "detail": "No repository loaded."
}
```

## DIWO

Standard error:

```json
{
  "error": "..."
}
```

CUQA proxy errors add:

```json
{
  "error": "...",
  "cuqa_url": "...",
  "reachable": true
}
```

Transformation integration errors may add:

- `sctva_url`
- `missing`

## RDP

```json
{
  "error": "Plan generation failed: ..."
}
```

## SCTVA

```json
{
  "error": "..."
}
```

## User Management

```json
{
  "success": false,
  "error": "..."
}
```

or validation:

```json
{
  "success": false,
  "errors": {}
}
```

---

# 11. Important Implementation/Documentation Caveats Found in Current Repo

These are worth fixing before freezing the API documentation for PP2/final submission.

### 1. Frontend declares an RDP `/config` endpoint that the RDP backend does not implement

`frontend/src/config/api.config.js` contains:

```text
/config
```

but the current `agents/rdp_agent/app.py` exposes only:

```text
/
/health
/api/health
/generate
```

Treat `/config` as **not implemented** unless you add it.

### 2. Frontend contains an old `CUA_AGENT` configuration

It declares:

```text
/analyze
/health
```

against a CUA base URL, but no matching CUA backend route set was found in the uploaded repository. The active component is CUQA.

### 3. User Management port mismatch

- frontend default: `http://localhost:6000`
- comments say service runs on `6000`
- current `user_management/app.py` default process port: `5005`

Align these values.

### 4. Plan approval still persists a simulated transformation summary

`POST /api/workflows/{wf_id}/plan-decision` calls a legacy `simulate_transformation()` and generates heuristic `metrics_after`.

The **real** transformation call is:

```text
POST /api/workflows/{wf_id}/transform
```

Therefore, for research evidence, use the actual SCTVA result rather than treating the simulated approval response as execution evidence.

### 5. Workspace source retrieval currently depends on SCTVA finding CUQA temporary workspace files

The integrated transform path calls SCTVA `/sctva/cuqa-sources`, whose implementation scans CUQA-style temp-workspace candidates. This assumes those workspace files are visible to the SCTVA process. If CUQA and SCTVA are isolated in separate containers without a shared volume, this filesystem coupling requires redesign or shared storage/source transfer.

### 6. CUQA repository quality report currently caps repository analysis at 50 source files

This is coded in `/api/quality-report`. Document this as a current limitation when evaluating larger repositories.

### 7. Shared JSON schemas are documentation/test contracts, not runtime request validators

`shared/contracts/README.md` explicitly states that the JSON schemas are not automatically enforced on every live request.

---

# 12. Recommended Public API Boundary

For the integrated product, the cleanest external architecture is:

```text
Browser -> DIWO only
```

with DIWO calling CUQA/RDP/SCTVA internally.

A production-facing API could expose repository ingestion through DIWO as well, instead of the browser calling CUQA directly. That would provide one base URL, one CORS boundary and one audit path for the entire workflow.

Current source already centralizes most agent-to-agent handoffs through DIWO, especially RDP planning and SCTVA execution.

---

# 13. Viva-Friendly API Flow Summary

```text
1. POST CUQA /api/upload-zip
   OR POST CUQA /api/github-repo

2. POST DIWO /api/workflows/from-cuqa
      -> POST CUQA /api/quality-report

3. POST DIWO /api/workflows/{id}/smell-selection-pass
   Preview only

4. POST DIWO /api/workflows/{id}/select-smells
      -> filters rejected smells
      -> POST RDP /generate

5. POST DIWO /api/workflows/{id}/plan-decision
      -> removes rejected/manual plan steps

6. POST DIWO /api/workflows/{id}/transform
      -> POST SCTVA /sctva/cuqa-sources
      -> maps RDP steps to SCTVA actions
      -> POST SCTVA /sctva/execute

7. POST DIWO /api/workflows/{id}/transformation-decision
      -> accepted file = refactored code
      -> rejected file = original code

8. GET DIWO /api/workflows/{id}/refactored-archive
   OR POST DIWO /api/diwo/apply-and-push

9. GET DIWO /api/workflows/{id}/audit-logs

10. POST DIWO /api/workflows/{id}/complete
```

**One-sentence viva explanation:**

> The frontend initiates repository analysis, but all critical cross-agent decisions are controlled by the DIWO orchestration layer: CUQA produces the structured quality report, DIWO filters it using developer decisions before RDP, DIWO filters the plan again before SCTVA, SCTVA returns validation/rollback evidence, and DIWO records the final accepted state for archive, Git and audit.
