"""
main.py — CUQA Agent FastAPI Server
-------------------------------------
Exposes REST endpoints for:
  - ZIP upload + extraction
  - Public GitHub repo cloning
  - File-level AST parsing
  - Repository structure discovery
  - Quality report generation

This is the FIRST agent in the agentic pipeline.
Its JSON output feeds directly into the RDP Agent.
"""

import os
import sys
import json
import shutil
import zipfile
import tempfile
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, HttpUrl

import requests
from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, HttpUrl

class GitHubRepoRequest(BaseModel):
    url: HttpUrl  # Built-in validation for URLs

# ---------------------------------------------------------------------------
# Make sibling modules importable regardless of CWD
# main.py lives inside agents/cuqa_agent/src/ — add that dir to sys.path
# so we can import ast_parser, ast_visualizer, report_generator directly.
# ---------------------------------------------------------------------------
SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from ast_parser import parse_source, detect_language
from ast_visualizer import enrich_ast, build_summary
from report_generator import generate_file_report, generate_repo_report

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(
    title="CUQA Agent API",
    description=(
        "Code Understanding & Quality Assessment Agent — "
        "First stage of the automated refactoring pipeline. "
        "Accepts source code via ZIP or GitHub URL, parses ASTs, "
        "detects code smells, and emits structured JSON for the RDP Agent."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Session state — single active workspace per server instance
# ---------------------------------------------------------------------------
_workspace: dict = {
    "root": None,          # Path to extracted/cloned directory
    "source": None,        # "zip" | "github"
    "repo_name": None,
    "files": [],           # List of relative paths to supported source files
}

SUPPORTED_EXTENSIONS = {".py", ".java", ".c", ".h"}


def _find_source_files(root: str) -> list[str]:
    """Return relative paths of all supported source files under root."""
    result = []
    for dirpath, _, filenames in os.walk(root):
        # Skip common non-source dirs
        rel_dir = os.path.relpath(dirpath, root)
        skip_prefixes = (".git", "node_modules", "__pycache__", ".venv", "venv", "target", "build")
        if any(rel_dir.startswith(p) for p in skip_prefixes):
            continue
        for fname in filenames:
            ext = os.path.splitext(fname)[-1].lower()
            if ext in SUPPORTED_EXTENSIONS:
                full = os.path.join(dirpath, fname)
                result.append(os.path.relpath(full, root))
    return sorted(result)


def _build_tree(root: str) -> dict:
    """Build a recursive directory tree dict for the frontend."""

    def _recurse(path: str, rel: str) -> dict:
        name = os.path.basename(path)
        if os.path.isfile(path):
            ext = os.path.splitext(name)[-1].lower()
            return {
                "name": name,
                "type": "file",
                "path": rel,
                "language": detect_language(name) if ext in SUPPORTED_EXTENSIONS else None,
            }
        # Directory
        children = []
        try:
            entries = sorted(os.listdir(path))
        except PermissionError:
            entries = []

        skip = {".git", "node_modules", "__pycache__", ".venv", "venv", "target", "build", ".class"}
        for entry in entries:
            if entry in skip:
                continue
            child_path = os.path.join(path, entry)
            child_rel = os.path.join(rel, entry).replace("\\", "/")
            children.append(_recurse(child_path, child_rel))

        return {"name": name, "type": "directory", "path": rel, "children": children}

    return _recurse(root, "")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/")
def root():
    return {"agent": "CUQA", "status": "running", "version": "1.0.0"}


@app.get("/api/health")
def health():
    return {"status": "ok", "workspace_loaded": _workspace["root"] is not None}


# ── 1. Upload ZIP ────────────────────────────────────────────────────────────

@app.post("/api/upload-zip")
async def upload_zip(file: UploadFile = File(...)):
    """
    Accept a ZIP file, extract it to a temporary workspace, and
    scan for supported source files.
    """
    if not file.filename.endswith(".zip"):
        raise HTTPException(400, "Only .zip files are supported.")

    # Clean previous workspace
    if _workspace["root"] and os.path.exists(_workspace["root"]):
        shutil.rmtree(_workspace["root"], ignore_errors=True)

    tmp_dir = tempfile.mkdtemp(prefix="cuqa_")
    zip_path = os.path.join(tmp_dir, "upload.zip")

    contents = await file.read()
    with open(zip_path, "wb") as f:
        f.write(contents)

    extract_dir = os.path.join(tmp_dir, "extracted")
    os.makedirs(extract_dir, exist_ok=True)

    try:
        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(extract_dir)
    except zipfile.BadZipFile:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise HTTPException(400, "Invalid or corrupted ZIP file.")

    # If ZIP has a single top-level folder, go inside it
    entries = os.listdir(extract_dir)
    if len(entries) == 1 and os.path.isdir(os.path.join(extract_dir, entries[0])):
        extract_dir = os.path.join(extract_dir, entries[0])

    source_files = _find_source_files(extract_dir)

    _workspace.update({
        "root": extract_dir,
        "source": "zip",
        "repo_name": file.filename.replace(".zip", ""),
        "files": source_files,
    })

    return {
        "message": "ZIP uploaded and extracted successfully.",
        "repo_name": _workspace["repo_name"],
        "files_found": len(source_files),
        "source_files": source_files[:100],  # cap for response size
    }


# ── 2. GitHub Repo ───────────────────────────────────────────────────────────

@app.post("/api/github-repo")
def load_github_repo(request: GitHubRepoRequest):  # Use Pydantic model
    url = str(request.url).rstrip('/')  # Normalize trailing slash
    
    # Remove .git suffix if present
    if url.endswith('.git'):
        url = url[:-4]
    
    # Validate GitHub domain
    if "github.com" not in url:
        raise HTTPException(400, "Only github.com URLs are supported.")
    
    repo_name = url.split("/")[-1]
    
    # Try multiple common branches
    for branch in ["main", "master", "develop", "trunk"]:
        zip_url = f"{url}/archive/refs/heads/{branch}.zip"
        try:
            response = requests.get(zip_url, timeout=30, allow_redirects=True)
            if response.status_code == 200:
                break
        except requests.exceptions.RequestException:
            continue
    else:
        raise HTTPException(502, f"Could not download '{repo_name}' from GitHub. Repo may be private or use non-standard branch names.")
    
    # Clean previous workspace
    if _workspace["root"] and os.path.exists(_workspace["root"]):
        shutil.rmtree(_workspace["root"], ignore_errors=True)

    tmp_dir = tempfile.mkdtemp(prefix="cuqa_gh_")
    zip_path = os.path.join(tmp_dir, "repo.zip")

    with open(zip_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)

    extract_dir = os.path.join(tmp_dir, "extracted")
    os.makedirs(extract_dir, exist_ok=True)

    try:
        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(extract_dir)
    except zipfile.BadZipFile:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise HTTPException(500, "Downloaded archive is not a valid ZIP.")

    entries = os.listdir(extract_dir)
    if len(entries) == 1 and os.path.isdir(os.path.join(extract_dir, entries[0])):
        extract_dir = os.path.join(extract_dir, entries[0])

    source_files = _find_source_files(extract_dir)

    _workspace.update({
        "root": extract_dir,
        "source": "github",
        "repo_name": repo_name,
        "files": source_files,
    })

    return {
        "message": f"GitHub repository '{repo_name}' loaded successfully.",
        "repo_name": repo_name,
        "github_url": url,
        "files_found": len(source_files),
        "source_files": source_files[:100],
    }


# ── 3. Project Structure ─────────────────────────────────────────────────────

@app.get("/api/project-structure")
def project_structure():
    """Return the file tree of the loaded repository."""
    if not _workspace["root"]:
        raise HTTPException(400, "No repository loaded. Upload a ZIP or provide a GitHub URL first.")
    tree = _build_tree(_workspace["root"])
    return {
        "repo_name": _workspace["repo_name"],
        "source": _workspace["source"],
        "total_source_files": len(_workspace["files"]),
        "tree": tree,
    }


# ── 4. Parse AST ─────────────────────────────────────────────────────────────

@app.post("/api/parse-ast")
def parse_ast(payload: dict):
    """
    Parse a specific file in the workspace and return its AST JSON.

    Body: { "file_path": "relative/path/to/File.java" }
    """
    if not _workspace["root"]:
        raise HTTPException(400, "No repository loaded.")

    rel_path = payload.get("file_path", "")
    if not rel_path:
        raise HTTPException(400, "file_path is required.")

    full_path = os.path.join(_workspace["root"], rel_path)
    if not os.path.isfile(full_path):
        raise HTTPException(404, f"File not found: {rel_path}")

    with open(full_path, "r", encoding="utf-8", errors="replace") as f:
        source = f.read()

    filename = os.path.basename(full_path)
    parsed = parse_source(source, filename)

    # Enrich with stable IDs for React tree keys
    if parsed.get("ast"):
        enrich_ast(parsed["ast"])

    summary = build_summary(parsed)

    return {
        "parsed": parsed,
        "summary": summary,
    }


# ── 5. Quality Report ────────────────────────────────────────────────────────

@app.post("/api/quality-report")
def quality_report(payload: dict = None):
    """
    Generate a quality report for one or all files in the workspace.

    Body (optional): { "file_path": "relative/path/File.py" }
    If file_path omitted, reports on all loaded files (capped at 50).
    """
    if not _workspace["root"]:
        raise HTTPException(400, "No repository loaded.")

    file_path = (payload or {}).get("file_path")

    if file_path:
        full_path = os.path.join(_workspace["root"], file_path)
        if not os.path.isfile(full_path):
            raise HTTPException(404, f"File not found: {file_path}")
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            source = f.read()
        report = generate_file_report(source, os.path.basename(full_path))
        return {"type": "file", "report": report}

    # All files — cap at 50 to avoid timeout
    file_reports = []
    for rel in _workspace["files"][:50]:
        full_path = os.path.join(_workspace["root"], rel)
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                source = f.read()
            report = generate_file_report(source, os.path.basename(full_path))
            report["relative_path"] = rel.replace("\\", "/")
        except Exception as exc:
            report = {"file": rel, "error": str(exc)}
        file_reports.append(report)

    repo_report = generate_repo_report(file_reports)
    repo_report["repo_name"] = _workspace["repo_name"]
    return {"type": "repository", "report": repo_report}


# ── 6. List source files ─────────────────────────────────────────────────────

@app.get("/api/files")
def list_files():
    """Return list of all discovered source files in the workspace."""
    if not _workspace["root"]:
        raise HTTPException(400, "No repository loaded.")
    return {
        "repo_name": _workspace["repo_name"],
        "files": _workspace["files"],
        "total": len(_workspace["files"]),
    }


# ---------------------------------------------------------------------------
# Run directly
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=True, reload_dirs=[str(SRC_DIR)])
