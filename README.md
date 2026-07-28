# R26-SE-008 — Automated Code Refactoring Pipeline

> **Research Project** | Multi-Agent AI System for Intelligent Code Refactoring  
> SE Research Group · Y4S1

---

## Overview

R26-SE-008 is a multi-agent AI pipeline that automatically analyses, plans, and applies code refactorings to Java and Python codebases. The pipeline consists of four specialised agents that work in sequence:

| # | Agent | Role |
|---|-------|------|
| ① | **CUQA** | Code Understanding & Quality Assessment |
| ② | **RDP** | Refactoring Decision & Planning |
| ③ | **Transformation** | Safe Code Transformation & Validation |
| ④ | **Orchestration** | Developer Interaction & Workflow |

---

## Quick Start

### 1. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 2. Start the CUQA Agent backend

```bash
cd agents/cuqa_agent/src
python main.py
```

The FastAPI server starts at **http://localhost:8080**  
Interactive API docs: **http://localhost:8080/docs**

### 3. Start the frontend dashboard

```bash
cd frontend
npm install
npm run dev
```

Frontend runs at **http://localhost:5173**

---

## CUQA Agent — Usage Guide

### Load a codebase

**Option A — Upload a ZIP file**

1. Open the dashboard at `http://localhost:5173`
2. In the **Load Repository** panel, select **📦 Upload ZIP**
3. Drag and drop your project ZIP, or click to browse
4. The agent extracts the archive and scans for `.py` and `.java` files

**Option B — Public GitHub Repository**

1. Select **🐙 GitHub URL** in the panel
2. Paste a public GitHub repository URL, e.g.:
   ```
   https://github.com/owner/repository-name
   ```
3. Click **→ Analyse** — the agent downloads the repo as a ZIP (no `git` required)

---

### Explore Project Structure

After loading, switch to the **🗂️ Project Structure** tab.

- The file tree shows all directories and files in the analysed repo
- Python files are highlighted in **blue** 🐍, Java files in **orange** ☕
- Click any `.py` or `.java` file to select it for AST analysis
- Selecting a file automatically switches to the **AST Visualization** tab

---

### Generate an AST

Switch to the **🌲 AST Visualization** tab after selecting a file.

The CUQA Agent:
1. Reads the source file from the workspace
2. Parses it using the language-appropriate parser
3. Returns a structured AST JSON
4. Renders it as an interactive, collapsible tree

**Toggle between tree view and raw JSON** using the `{ } Raw JSON` button — the raw JSON is the exact payload forwarded to the RDP Agent.

#### Example AST Output

```json
{
  "file": "OrderProcessor.java",
  "language": "java",
  "ast": {
    "type": "CompilationUnit",
    "name": "OrderProcessor.java",
    "children": [
      {
        "type": "ImportDeclaration",
        "name": "java.util.List",
        "children": []
      },
      {
        "type": "ClassDeclaration",
        "name": "OrderProcessor",
        "line": 5,
        "children": [
          {
            "type": "FieldDeclaration",
            "name": "orders",
            "line": 7,
            "children": []
          },
          {
            "type": "MethodDeclaration",
            "name": "calculateTotal",
            "line": 10,
            "children": [
              {
                "type": "Parameter",
                "name": "orderId",
                "paramType": "int",
                "children": []
              }
            ]
          }
        ]
      }
    ]
  }
}
```

---

### Quality Report

Switch to the **📊 Quality Report** tab.

- **Full Repo** — analyses all source files (up to 50), returns aggregate score + per-file breakdown
- **Selected File** — analyses only the currently selected file

The report includes:

| Field | Description |
|-------|-------------|
| `quality_score` | 0–100 score (higher = better) |
| `metrics` | LOC, blank lines, comments, functions, classes |
| `code_smells` | List of detected smells with type, message, line, severity |
| `smell_summary` | Count of high / medium / low severity smells |

#### Detected Code Smells

| Smell | Language | Severity |
|-------|----------|----------|
| Long Method (>30 lines) | Python | High |
| Large Class (>15 methods) | Python, Java | High |
| Too Many Parameters (>5) | Python, Java | Medium |
| Magic Number | Python | Low |
| Bare `except:` | Python | Medium |

---

## API Reference

Base URL: `http://localhost:8080`

### `POST /api/upload-zip`
Upload a ZIP file.

**Request:** `multipart/form-data` with field `file`  
**Response:**
```json
{
  "message": "ZIP uploaded and extracted successfully.",
  "repo_name": "my-project",
  "files_found": 24,
  "source_files": ["src/Main.java", "src/utils.py"]
}
```

---

### `POST /api/github-repo`
Load a public GitHub repository.

**Request body:**
```json
{ "url": "https://github.com/owner/repo" }
```

---

### `GET /api/project-structure`
Get the file tree of the loaded repository.

**Response:**
```json
{
  "repo_name": "my-project",
  "source": "github",
  "total_source_files": 12,
  "tree": {
    "name": "my-project",
    "type": "directory",
    "children": [...]
  }
}
```

---

### `POST /api/parse-ast`
Parse a file and return its AST.

**Request body:**
```json
{ "file_path": "src/Main.java" }
```

---

### `POST /api/quality-report`
Generate quality report. Omit `file_path` for full repo report.

**Request body:**
```json
{ "file_path": "src/utils.py" }
```

---

## Project Structure

See [`docs/project_structure.md`](docs/project_structure.md) for the full annotated directory tree and pipeline diagram.

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `fastapi` | CUQA Agent REST API |
| `uvicorn` | ASGI server |
| `javalang` | Java source code parsing |
| `requests` | GitHub ZIP download |
| `python-multipart` | FastAPI file upload support |

---

## Research Context

This system is developed as part of **R26-SE-008** — a Software Engineering research project exploring the use of multi-agent AI systems for automated code refactoring. The CUQA Agent is the entry point of the pipeline: it ingests source code, generates AST representations, and produces structured quality reports that drive the downstream refactoring decisions made by the RDP Agent.
