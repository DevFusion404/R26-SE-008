# R26-SE-008 — Automated Code Refactoring Pipeline

> **Research Project** | Multi-Agent AI System for Intelligent Code Refactoring  
> SE Research Group · Y4S1

---

## Overview

R26-SE-008 is a multi-agent AI pipeline that automatically **analyses**, **plans**, and **applies** code refactorings to Python, Java, and C codebases. Four specialised agents work in an end-to-end sequence — from raw source code intake through to safe, validated, developer-approved transformation.

```
  Source Code
      │
      ▼
┌─────────────┐     quality_report.json    ┌─────────────┐
│  ① CUQA     │ ─────────────────────────► │  ② RDP      │
│  Agent      │                            │  Agent      │
└─────────────┘                            └──────┬──────┘
  FastAPI :8080                                   │ refactoring_plan.json
  React Frontend :5173                            │
                                                  ▼
                                         ┌─────────────────┐
                                         │ ③ Transformation │
                                         │  Agent (SCTVA)  │
                                         └────────┬────────┘
                                                  │ validated_result.json
                                                  ▼
                                         ┌─────────────────┐
                                         │ ④ Orchestration  │
                                         │  Agent (DIWO)   │
                                         └─────────────────┘
                                           Flask :5001
                                           React Frontend :5173
```

| # | Agent | Full Name | Role | Port |
|---|-------|-----------|------|------|
| ① | **CUQA** | Code Understanding & Quality Assessment | Parses ASTs, detects 20+ code smell types, emits structured quality reports | 8080 |
| ② | **RDP** | Refactoring Decision & Planning | Maps smells to refactoring techniques, scores candidates, generates ordered execution plans | 5000 |
| ③ | **SCTVA** | Safe Code Transformation & Validation | Applies transformations, validates syntax/structure/behaviour, manages rollbacks | 8002 |
| ④ | **DIWO** | Developer Interaction & Workflow Orchestration | 5-stage developer approval workflow, feedback capture, audit logging | 5001 |

---

## Quick Start

### Prerequisites

- Python 3.10+
- Node.js 18+ (for the React frontend)

### 1. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 2. Start the CUQA Agent (Agent ①)

```bash
cd agents/cuqa_agent/src
python main.py
```

FastAPI server: **http://localhost:8080**  
Interactive Swagger docs: **http://localhost:8080/docs**

### 3. Start the RDP Agent (Agent ②)

```bash
cd agents/rdp_agent
python app.py
```

Flask web UI: **http://localhost:5000**

### 4. Start the Transformation Agent (Agent ③)

```bash
cd agents/transformation_agent/safe_code_transformation_agent
python app.py
```

Flask + REST API + built-in UI: **http://localhost:8002**

### 5. Start the Orchestration Agent backend (Agent ④)

```bash
cd agents/orchestration_agent/backend
python app.py
```

DIWO Flask backend: **http://localhost:5001**

### 6. Start the React frontend dashboard

```bash
cd frontend
npm install
npm run dev
```

Frontend: **http://localhost:5173**

---

## Agent ① — CUQA: Code Understanding & Quality Assessment

### What it does

CUQA is the **entry point** of the pipeline. It accepts source code via ZIP upload or GitHub URL, parses multi-language ASTs, runs 20+ code smell detectors, and emits structured JSON quality reports consumed by the RDP Agent.

**Supported languages:** Python · Java · C / C Headers (`.c`, `.h`)

### REST API — Base URL `http://localhost:8080`

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/` | Health check & version |
| `GET` | `/api/health` | Workspace load status |
| `POST` | `/api/upload-zip` | Upload a ZIP file, extract & scan source files |
| `POST` | `/api/github-repo` | Load a public GitHub repository by URL |
| `GET` | `/api/project-structure` | File tree of the loaded repository |
| `POST` | `/api/parse-ast` | Parse a single file and return its AST JSON |
| `POST` | `/api/quality-report` | Generate quality report (single file or full repo) |
| `GET` | `/api/files` | List all discovered source files in the workspace |

#### `POST /api/upload-zip`

**Request:** `multipart/form-data`, field `file` (`.zip` only)  
**Response:**
```json
{
  "message": "ZIP uploaded and extracted successfully.",
  "repo_name": "my-project",
  "files_found": 24,
  "source_files": ["src/Main.java", "src/utils.py"],
  "language_breakdown": {"Python": 18, "Java": 6},
  "detected_languages": ["Python", "Java"],
  "primary_language": "Python",
  "is_polyglot": true
}
```

#### `POST /api/github-repo`

**Request body:**
```json
{ "url": "https://github.com/owner/repo" }
```
Automatically tries `main`, `master`, `develop`, and `trunk` branches.

#### `POST /api/parse-ast`

**Request body:**
```json
{ "file_path": "src/Main.java" }
```
Returns a structured AST JSON with stable IDs for the React tree renderer, plus a build summary.

#### `POST /api/quality-report`

**Request body (optional):**
```json
{ "file_path": "src/utils.py" }
```
Omit `file_path` for a full repository report (capped at 50 files).

**Response fields:**

| Field | Description |
|-------|-------------|
| `quality_score` | 0–100 score (higher = better; deductions: high −8, medium −4, low −1 per smell) |
| `metrics` | LOC, blank lines, comment lines, functions, classes, **coupling** |
| `code_smells` | Array of detected smells — see schema below |
| `smell_summary` | `{high, medium, low}` severity counts |

### Code Smell Schema

Every smell dict contains:

```json
{
  "type": "LongMethod",
  "message": "Function 'process_order' has 47 lines (>30)",
  "line": 42,
  "severity": "high",
  "entity": "process_order",
  "parameter_count": 6,
  "start_line": 42,
  "end_line": 89,
  "cyclomatic_complexity": 9
}
```

Fields marked **bold** are new RDP-facing fields added in the latest update:

| Field | Present on | Description |
|-------|-----------|-------------|
| `type` | all | Smell type string (see table below) |
| `message` | all | Human-readable description |
| `line` | all | Source line where the smell starts |
| `severity` | all | `"high"` / `"medium"` / `"low"` |
| `entity` | all | Function or class name |
| **`parameter_count`** | LongMethod, TooManyParameters | Number of non-self/cls parameters |
| **`start_line`** | LongMethod, TooManyParameters, SwitchStatements | First line of the code block |
| **`end_line`** | LongMethod, TooManyParameters, SwitchStatements | Last line of the code block |
| **`cyclomatic_complexity`** | LongMethod, SwitchStatements | Estimated CC (branch-count + 1) |
| **`method_count`** | LargeClass, LazyClass | Number of methods in the class |
| **`chain_length`** | MessageChains | Depth of the attribute/call chain |
| **`primitive_field_count`** | PrimitiveObsession | Number of primitive-typed annotated fields |
| **`external_field_accesses`** | FeatureEnvy | Count of non-self attribute accesses |
| **`duplicate_group`** | DuplicateCode | List of function names sharing the same structure |
| **`clump_parameters`** | DataClumps | Sorted list of the repeated parameter names |

### Metrics Schema

```json
{
  "filename": "utils.py",
  "lines_of_code": 312,
  "blank_lines": 34,
  "comment_lines": 28,
  "functions": 18,
  "classes": 4,
  "coupling": 7
}
```

`coupling` = number of `import` / `from … import` statements (Python) or `import` lines (Java). Used by RDP for `has_multiple_dependencies` precondition and severity scoring.

### Detected Code Smells — Full Catalog

#### Python

| Smell Type | Trigger | Severity | Key Extra Fields |
|-----------|---------|----------|-----------------|
| `LongMethod` | Function body > 30 lines | High | `parameter_count`, `start_line`, `end_line`, `cyclomatic_complexity` |
| `TooManyParameters` | > 5 non-self parameters | Medium | `start_line`, `end_line` |
| `LargeClass` | > 15 methods in class | High | `method_count` |
| `LazyClass` | ≤ 2 methods **and** class LOC < 30 | Low | `method_count` |
| `MagicNumber` | Numeric literal ∉ {0, 1, −1, 2} | Low | — |
| `BareExcept` | `except:` with no exception type | Medium | — |
| `SwitchStatements` | ≥ 4 elif branches in function | Medium | `cyclomatic_complexity`, `start_line`, `end_line` |
| `MessageChains` | Attribute/call chain depth ≥ 3 | Low | `chain_length` |
| `DeadCode` | Defined function/class with zero references in file | Low | — |
| `DuplicateCode` | 2+ functions with identical structural fingerprint | Medium | `duplicate_group` |
| `Comments` | comment\_lines / LOC > 30% and LOC > 50 | Low | — |
| `PrimitiveObsession` | Class with ≥ 4 primitive-annotated fields | Medium | `primitive_field_count` |
| `InappropriateIntimacy` | Class accesses `_private` attr of external object | Medium | — |
| `SpeculativeGenerality` | Class extends ABC / Mixin / Base pattern | Low | — |
| `FeatureEnvy` | Method accesses ≥ 3 external attrs, more than self | Medium | `external_field_accesses`, `self_field_accesses` |
| `DataClumps` | Same 3+ params appear together in multiple functions | Medium | `clump_parameters` |

#### Java

| Smell Type | Trigger | Severity | Key Extra Fields |
|-----------|---------|----------|-----------------|
| `LongMethod` | Method LOC > 30 | High | `start_line`, `end_line`, `parameter_count`, `cyclomatic_complexity` |
| `TooManyParameters` | > 5 parameters | Medium | `parameter_count` |
| `LargeClass` | > 15 methods | High | `method_count` |
| `MagicNumber` | Numeric literal ∉ {0, 1, −1, 2} | Low | — |

#### C / C Headers

| Smell Type | Trigger | Severity |
|-----------|---------|----------|
| `LongFunction` | Function body > 40 lines | High |
| `TooManyParameters` | > 5 parameters | Medium |
| `DeepNesting` | Nesting depth > 4 | High |
| `MagicNumber` | Numeric literal ∉ safe set | Low |
| `UnsafeFunctionUsage` | `gets`, `strcpy`, `strcat`, `sprintf`, `scanf` | High |
| `GlobalVariable` | File-scope variable declaration | Medium |
| `LargeHeaderFile` | `.h` file > 300 lines | Medium |

### Source Modules — `agents/cuqa_agent/src/`

| Module | Purpose |
|--------|---------|
| `main.py` | FastAPI server — all REST endpoints, workspace state, ZIP/GitHub ingestion |
| `report_generator.py` | **Core smell engine** — all language detectors, metrics computation, `generate_file_report()`, `generate_repo_report()` |
| `ast_parser.py` | Language detection + dispatch to language-specific parsers |
| `python_ast_parser.py` | Python AST parsing via `ast` stdlib |
| `java_ast_parser.py` | Java AST parsing via `javalang` |
| `c_ast_parser.py` | C AST parsing via `tree-sitter` (regex fallback if unavailable) |
| `ast_visualizer.py` | Enriches AST with stable IDs, builds summary for frontend rendering |

---

## Agent ② — RDP: Refactoring Decision & Planning

### What it does

The RDP Agent consumes CUQA's quality report JSON and produces a structured, ordered, machine-executable **refactoring plan**. It runs an 8-step pipeline to filter viable candidates, score them, predict quality-metric impact, resolve dependencies, and generate step-by-step transformation instructions.

### 8-Step Pipeline

| Step | Module | Responsibility |
|------|--------|---------------|
| 1 | `problem_interpreter.py` | Evaluate preconditions per smell (e.g. `has_multiple_parameters`, `has_code_block`, `has_type_checking`) |
| 2 | `knowledge_base.py` | Map each smell type to candidate refactoring techniques (13-smell catalog) |
| 3 | `candidate_generator.py` | Filter, score and select the best candidate per smell |
| 3b | `impact_predictor.py` | Predict quality-metric changes before scoring (CC reduction, coupling, cohesion, maintainability, risk) |
| 4 | `decision_engine.py` | Impact-aware weighted scoring → final candidate selection |
| 5–6 | `dependency_analyzer.py` | Analyse refactoring dependencies, topological sort with deadlock resolution |
| 7 | `plan_generator.py` | Assemble structured plan with explanations and transformation parameters |

### Smell → Refactoring Catalog

| Smell Type | Candidate Refactorings |
|-----------|----------------------|
| `LongMethod` | Extract Method, Replace Temp with Query, Introduce Parameter Object |
| `LargeClass` / God Class | Extract Class, Extract Subclass |
| `FeatureEnvy` | Move Method |
| `DuplicateCode` | Extract Method, Pull Up Method |
| `DataClumps` | Introduce Parameter Object, Extract Class |
| `SwitchStatements` | Replace Conditional with Polymorphism |
| `LazyClass` | Inline Class, Collapse Hierarchy |
| `SpeculativeGenerality` | Collapse Hierarchy, Remove Dead Code |
| `PrimitiveObsession` | Replace Data Value with Object, Introduce Parameter Object |
| `TooManyParameters` | Introduce Parameter Object, Replace Parameter with Method Call |
| `MessageChains` | Hide Delegate |
| `Comments` | Extract Method, Rename Method |
| `DeadCode` | Remove Dead Code |

### Scoring Formula

**Base score:**
```
base_score = complexity_weight × (4 − complexity) + risk_weight × (4 − risk) + impact_weight × impact
```

**Impact-aware score** (when predictions available):
```
impact_bonus = complexity_reduction + coupling_bonus + cohesion_bonus + maintainability − risk_penalty
final_score  = base_score + impact_prediction_weight × impact_bonus
```

### Usage

**Web UI (default):**
```bash
cd agents/rdp_agent
python app.py          # → http://localhost:5000
```

**CLI:**
```bash
python -m rdp_agent --input quality_report.json --output refactoring_plan.json
```

**Python API:**
```python
from rdp_agent import generate_plan, generate_plan_from_dict

plan = generate_plan("quality_report.json", "plan.json")
plan_dict = generate_plan_from_dict(report_data)
```

**Configuration** (`config.yaml`):
```yaml
weights:
  complexity_weight: 0.2
  risk_weight: 0.4
  impact_weight: 0.4
  impact_prediction_weight: 0.3
log_level: INFO
```

### Source Modules — `agents/rdp_agent/src/`

| Module | Purpose |
|--------|---------|
| `models.py` | Data models: `CodeSmell`, `QualityReport`, `ImpactPrediction`, `RefactoringPlan` |
| `knowledge_base.py` | 13-smell refactoring catalog, dependency graph |
| `problem_interpreter.py` | Precondition evaluation — reads `parameter_count`, `cyclomatic_complexity`, `coupling`, `chain_length`, etc. from CUQA output |
| `impact_predictor.py` | 15-technique heuristic rules table for quality-metric impact prediction |
| `decision_engine.py` | Weighted scoring, impact-aware mode, severity escalation |
| `candidate_generator.py` | Filter viable candidates per smell, delegate to decision engine |
| `dependency_analyzer.py` | Dependency graph traversal, topological sort, deadlock resolution |
| `plan_generator.py` | Final plan assembly with source_lines, parameters, and natural-language explanations |
| `pipeline.py` | `RDPAgent` orchestrator class + `generate_plan()` convenience functions |
| `ml_scorer.py` | ML-based scoring extension (experimental) |
| `config.py` | YAML/JSON configuration loader |
| `cli.py` | `python -m rdp_agent` entry point |

---

## Agent ③ — SCTVA: Safe Code Transformation & Validation

### What it does

SCTVA consumes source code + an RDP refactoring plan and returns **transformed code** with multi-level safety guarantees: syntax validation → structural validation → behavioural validation, with automatic rollback if any stage fails.

**Supported languages:** Python (AST-based), Java (text-based with javac check)

### REST API — Base URL `http://localhost:8002`

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/sctva/health` | Health check |
| `POST` | `/sctva/execute` | Execute from a full SCTVA request payload |
| `POST` | `/sctva/execute_from_rdp` | Execute from an RDP plan payload (via PlannerAdapter) |

### Supported Transformation Actions

| Action | Description |
|--------|-------------|
| `rename_symbol` | Rename a function, class, or variable across the file |
| `extract_constant` | Replace a magic number/literal with a named constant |
| `replace_literal` | Replace a specific literal value at a given location |
| `inject_syntax_error` | Intentional error injection for negative testing |
| `noop` | Safe no-op placeholder for unsupported RDP refactoring types |

### Validation Pipeline

1. **Syntax validation** — parse transformed code with language parser
2. **Structural validation** — verify entity presence, scope integrity
3. **Behavioural validation** — run test harness (Python subprocess; Java mock/javac)

If any stage fails → automatic rollback to pre-transformation state.

### Python Integration

```python
from sctva.agent import SafeCodeTransformationValidationAgent
from sctva.integration.planner_adapter import PlannerAdapter

agent = SafeCodeTransformationValidationAgent()
adapter = PlannerAdapter()

sctva_request = adapter.build_request_from_rdp(
    request_id="req_001",
    language="python",
    source_code=source,
    planner_output=rdp_plan,
    correlation_id="corr_123",
)
result = agent.execute(sctva_request)
```

### Source Modules — `agents/transformation_agent/safe_code_transformation_agent/sctva/`

| Module | Purpose |
|--------|---------|
| `agent.py` | `SafeCodeTransformationValidationAgent` — main orchestrator |
| `contracts.py` | Pydantic schemas for request/response validation |
| `constants.py` | Shared constants (supported languages, action types) |
| `transformers/` | Language-specific transformation implementations |
| `validators/` | Syntax, structural, and behavioural validators |
| `rollback/rollback_manager.py` | Snapshot and rollback management |
| `scoring/confidence_scorer.py` | Confidence score computation (0–1) |
| `reporting/safety_reporter.py` | Safety report generation |
| `integration/planner_adapter.py` | RDP plan → SCTVA request mapping |
| `integration/api.py` | Flask REST routes |

---

## Agent ④ — DIWO: Developer Interaction & Workflow Orchestration

### What it does

DIWO provides the human-in-the-loop layer. It drives a **5-stage developer approval workflow**, captures structured feedback for the ML feedback model, maintains a full SQLite audit log, and coordinates rollback via Git-snapshot simulation.

### 5-Stage Workflow

```
Stage 1: Smell Review     → Developer reviews detected smells
Stage 2: Smell Selection  → Developer selects smells to address
Stage 3: Plan Approval    → Developer approves/rejects the RDP plan
Stage 4: Transformation   → SCTVA executes approved transformations
Stage 5: Comparison       → Developer reviews before/after diff
```

### REST API — Base URL `http://localhost:5001`

All routes are prefixed with `/api` via the `diwo_bp` blueprint.

| Purpose | Route pattern |
|---------|--------------|
| Workflow sessions | `/api/sessions/*` |
| Smell management | `/api/smells/*` |
| Plan approval | `/api/plans/*` |
| Feedback capture | `/api/feedback/*` |
| Audit log | `/api/audit/*` |

### Source Modules — `agents/orchestration_agent/backend/`

| Module | Purpose |
|--------|---------|
| `app.py` | Flask factory (`create_app`), CORS, blueprint registration |
| `db/database.py` | SQLite initialisation (prototype; swap PostgreSQL for production) |
| `diwo/routes.py` | All DIWO API route handlers |
| `models/` | SQLAlchemy / data models |
| `feedback_model/` | ML feedback manager (rating capture + model updating) |

---

## Frontend Dashboard

**Tech stack:** React 19 · Vite 8 · Tailwind CSS 4 · Recharts

Runs at **http://localhost:5173** and provides a unified interface across all four agents.

### Pages

| Page | Route | Description |
|------|-------|-------------|
| Overview | `/` | Pipeline status, agent health cards |
| Dashboard | `/dashboard` | Cross-agent metrics and quality trends |
| Repository Input | `/repository` | Load code via ZIP upload or GitHub URL |
| CUQA Agent | `/cuqa` | AST visualiser, quality report viewer, smell browser |
| RDP Agent | `/rdp` | Upload quality report JSON, view refactoring plan |
| Transform | `/transform` | SCTVA transformation UI |
| DIWO | `/diwo` | 5-stage developer workflow |
| Reports | `/reports` | Historical reports and comparisons |
| Evaluation | `/evaluation` | Research evaluation metrics |
| Documentation | `/docs` | In-app documentation |
| Settings | `/settings` | Agent endpoint configuration |

### Frontend Start

```bash
cd frontend
npm install
npm run dev      # dev server at http://localhost:5173
npm run build    # production bundle → dist/
```

---

## Project Structure

```
R26-SE-008/
├── agents/
│   ├── cuqa_agent/                         # Agent ① — Code Quality Assessment
│   │   ├── src/
│   │   │   ├── main.py                     # FastAPI server (8 REST endpoints)
│   │   │   ├── report_generator.py         # Smell detectors (20+ types), metrics engine
│   │   │   ├── ast_parser.py               # Language detection & parser dispatch
│   │   │   ├── python_ast_parser.py        # Python AST parser
│   │   │   ├── java_ast_parser.py          # Java AST parser (javalang)
│   │   │   ├── c_ast_parser.py             # C AST parser (tree-sitter / regex fallback)
│   │   │   └── ast_visualizer.py           # AST enrichment for React renderer
│   │   └── tests/                          # pytest suite (39 tests)
│   │
│   ├── rdp_agent/                          # Agent ② — Refactoring Decision & Planning
│   │   ├── src/
│   │   │   ├── pipeline.py                 # RDPAgent orchestrator + convenience fns
│   │   │   ├── problem_interpreter.py      # Precondition evaluation (Step 1)
│   │   │   ├── knowledge_base.py           # 13-smell refactoring catalog (Step 2)
│   │   │   ├── candidate_generator.py      # Candidate filtering & selection (Step 3)
│   │   │   ├── impact_predictor.py         # Quality-metric impact prediction (Step 3b)
│   │   │   ├── decision_engine.py          # Impact-aware weighted scoring (Step 4)
│   │   │   ├── dependency_analyzer.py      # Dependency graph + topo sort (Steps 5–6)
│   │   │   ├── plan_generator.py           # Plan assembly & explanations (Step 7)
│   │   │   ├── models.py                   # Data models (CodeSmell, QualityReport …)
│   │   │   ├── ml_scorer.py                # ML-based scoring extension (experimental)
│   │   │   ├── config.py                   # YAML/JSON config loader
│   │   │   └── cli.py                      # python -m rdp_agent CLI entry point
│   │   ├── app.py                          # Flask web UI (http://localhost:5000)
│   │   ├── config.yaml                     # Weights, thresholds, log level
│   │   └── CUQA_FIXES_CHECKLIST.txt        # RDP↔CUQA compatibility tracker [20/20 done]
│   │
│   ├── transformation_agent/               # Agent ③ — Safe Transformation & Validation
│   │   └── safe_code_transformation_agent/
│   │       ├── sctva/
│   │       │   ├── agent.py                # SafeCodeTransformationValidationAgent
│   │       │   ├── contracts.py            # Pydantic request/response schemas
│   │       │   ├── transformers/           # Python (AST) + Java (text) transformers
│   │       │   ├── validators/             # Syntax, structural, behavioural validators
│   │       │   ├── rollback/               # Snapshot + rollback manager
│   │       │   ├── scoring/                # Confidence scorer
│   │       │   ├── reporting/              # Safety report generator
│   │       │   └── integration/            # PlannerAdapter (RDP→SCTVA), Flask API
│   │       ├── app.py                      # Flask server (http://localhost:8002)
│   │       └── run_demo.py                 # Standalone demo runner
│   │
│   └── orchestration_agent/                # Agent ④ — Developer Workflow (DIWO)
│       ├── backend/
│       │   ├── app.py                      # Flask factory (http://localhost:5001)
│       │   ├── db/                         # SQLite database initialisation
│       │   ├── diwo/                       # DIWO blueprint routes
│       │   ├── models/                     # Data models
│       │   └── feedback_model/             # ML feedback manager
│       └── feedback_model/
│
├── frontend/                               # React 19 + Vite dashboard
│   ├── src/
│   │   ├── pages/                          # Route-level page components
│   │   ├── components/                     # Shared UI components
│   │   ├── services/                       # API client functions
│   │   └── utils/                          # Helpers
│   └── package.json
│
├── api/                                    # Shared API schemas / contracts
├── data/                                   # Sample datasets & quality reports
├── docs/                                   # Extended documentation & diagrams
├── experiments/                            # Research experiments
├── shared/                                 # Cross-agent shared utilities
├── scripts/                                # Dev/CI utility scripts
├── requirements.txt                        # Python dependencies (CUQA + shared)
└── README.md                               # This file
```

---

## Dependencies

### Python (all agents)

| Package | Version | Purpose |
|---------|---------|---------|
| `fastapi` | 0.115.6 | CUQA Agent REST API |
| `uvicorn[standard]` | 0.32.1 | ASGI server for CUQA |
| `flask` | — | RDP Agent & SCTVA web UI |
| `flask-cors` | — | CORS for orchestration backend |
| `javalang` | 0.13.0 | Java source code parsing |
| `requests` | 2.32.3 | GitHub ZIP download |
| `python-multipart` | 0.0.18 | FastAPI file upload support |
| `pydantic` | 2.10.4 | Data validation (CUQA + SCTVA) |
| `pyflakes` | 3.2.0 | Python static analysis |
| `pyyaml` | — | RDP config file support |
| `tree-sitter` *(optional)* | ≥ 0.21 | C AST parsing (regex fallback if absent) |

### Frontend (Node)

| Package | Version | Purpose |
|---------|---------|---------|
| `react` | 19.x | UI framework |
| `vite` | 8.x | Dev server & bundler |
| `tailwindcss` | 4.x | Utility CSS |
| `recharts` | 3.x | Quality metric charts |

---

## CUQA ↔ RDP Compatibility

The file `agents/rdp_agent/CUQA_FIXES_CHECKLIST.txt` tracks all compatibility requirements between the CUQA output schema and the RDP input schema. All **20 fixes** are now complete:

| Phase | Items | Status |
|-------|-------|--------|
| 1 — Missing fields on existing smells | 5 | ✅ Complete |
| 2 — New Python smell detectors | 6 | ✅ Complete |
| 3 — New Java smell detectors | 4 | ✅ Complete |
| 4 — Advanced Python detectors | 5 | ✅ Complete |

Key fields now emitted by CUQA and consumed by RDP:
- `parameter_count` → unlocks *Introduce Parameter Object* for Long Method
- `start_line` / `end_line` → accurate `source_lines` in plans
- `cyclomatic_complexity` → enables *Replace Conditional with Polymorphism* selection
- `method_count` → accurate `has_multiple_responsibilities` precondition
- `coupling` → unlocks *Introduce Facade* and severity scoring bonuses

---

## Running Tests

```bash
# CUQA Agent — 39 tests
cd agents/cuqa_agent
pytest tests/ -v

# RDP Agent
cd agents/rdp_agent
pytest tests/ -v

# SCTVA
cd agents/transformation_agent/safe_code_transformation_agent
pytest tests -q
```

---

## Research Context

R26-SE-008 is a **Software Engineering research project** (Y4S1) exploring multi-agent AI systems for automated code refactoring. The pipeline is designed around strict agent boundaries and structured JSON contracts, making each agent independently testable and replaceable.

The CUQA Agent serves as the grounding layer: it provides objective, AST-derived evidence (smells, metrics, structure) that drives downstream decision-making without requiring the later agents to understand source code directly.
