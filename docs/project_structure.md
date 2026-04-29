# Project Structure — R26-SE-008

> Automated Refactoring Pipeline using Multi-Agent AI Systems

```
R26-SE-008/
│
├── agents/                          # All AI agents in the pipeline
│   │
│   ├── cuqa_agent/                  ← ① CUQA — Code Understanding & Quality Assessment
│   │   ├── src/
│   │   │   ├── __init__.py          # Package exports
│   │   │   ├── main.py              # FastAPI server (port 8001)
│   │   │   ├── ast_parser.py        # Language dispatcher (Python / Java)
│   │   │   ├── python_ast_parser.py # Python AST via built-in `ast` module
│   │   │   ├── java_ast_parser.py   # Java AST via `javalang` library
│   │   │   ├── ast_visualizer.py    # AST enrichment & summary utilities
│   │   │   └── report_generator.py  # Code smell detection & quality scoring
│   │   ├── tests/
│   │   └── outputs/                 # JSON reports emitted for RDP Agent
│   │
│   ├── rdp_agent/                   ← ② RDP — Refactoring Decision & Planning
│   │   ├── src/
│   │   └── tests/
│   │
│   ├── transformation_agent/        ← ③ Transformation — Safe Code Transformation
│   │   ├── src/
│   │   ├── tests/
│   │   ├── rollback/
│   │   └── validators/
│   │
│   └── orchestration_agent/         ← ④ Orchestration — Developer Interaction & Workflow
│       ├── src/
│       ├── api/
│       ├── dashboard/
│       └── workflow/
│
├── api/                             # Shared REST API gateway
│   ├── main.py
│   ├── controllers/
│   └── routes/
│
├── frontend/                        # React + Vite dashboard (CUQA demo)
│   ├── src/
│   │   ├── App.jsx                  # Main dashboard shell
│   │   ├── index.css                # Dark-mode design system
│   │   ├── main.jsx                 # React entry point
│   │   └── components/
│   │       ├── UploadPanel.jsx      # ZIP upload & GitHub URL input
│   │       ├── ProjectStructureView.jsx  # Repo file-tree explorer
│   │       ├── ASTVisualization.jsx      # Interactive AST tree
│   │       └── QualityReportView.jsx     # Code smell report & metrics
│   ├── public/
│   ├── package.json
│   └── vite.config.js
│
├── shared/                          # Cross-agent utilities
│   ├── config/
│   ├── logging/
│   └── utils/
│
├── data/                            # Sample datasets & test codebases
├── docs/                            # Documentation
│   └── project_structure.md         # ← this file
├── experiments/                     # Research notebooks & spike work
├── scripts/
│   ├── run_all_agents.py
│   └── setup.sh
├── requirements.txt                 # Python dependencies
└── README.md
```

---

## Agent Pipeline Overview

```
[User uploads ZIP / GitHub URL]
            │
            ▼
  ┌─────────────────────┐
  │  ① CUQA Agent       │  Port 8001
  │  AST Parsing        │  Ingests code → generates AST JSON + quality report
  │  Code Smell Detect  │
  └────────┬────────────┘
           │ Structured JSON (AST + Quality Report)
           ▼
  ┌─────────────────────┐
  │  ② RDP Agent        │  Refactoring Decision & Planning
  │  Smell Prioritizer  │  Receives CUQA output, decides what to refactor
  └────────┬────────────┘
           │ Refactoring Plan JSON
           ▼
  ┌─────────────────────┐
  │  ③ Transform Agent  │  Safe Code Transformation & Validation
  │  Code Rewriter      │  Applies refactorings, runs validators, supports rollback
  └────────┬────────────┘
           │ Transformed Code + Diff
           ▼
  ┌─────────────────────┐
  │  ④ Orchestration    │  Developer Interaction & Workflow
  │  Workflow Manager   │  Presents results to developer, manages approvals
  └─────────────────────┘
```

---

## CUQA Agent — API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/upload-zip` | Upload a ZIP archive of the codebase |
| `POST` | `/api/github-repo` | Load a public GitHub repository by URL |
| `GET`  | `/api/project-structure` | Get the file tree of the loaded repo |
| `GET`  | `/api/files` | List all detected source files |
| `POST` | `/api/parse-ast` | Parse a single file and return its AST |
| `POST` | `/api/quality-report` | Generate quality report (file or full repo) |

---

## Supported Languages

| Language | Extension | Parser |
|----------|-----------|--------|
| Python   | `.py`     | Built-in `ast` module |
| Java     | `.java`   | `javalang` library |
