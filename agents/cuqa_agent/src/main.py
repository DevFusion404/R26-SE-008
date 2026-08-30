"""
main.py — CUQA Agent FastAPI Server & API Gateway
-------------------------------------------------
REST API backend for the CUQA (Code Understanding & Quality Assessment) Agent.

===============================================================================
SPECIAL FUNCTION & ARCHITECTURAL OVERVIEW FOR CODE VIVA / PRESENTATION:
===============================================================================
1. POSITION IN REFACTORING PIPELINE:
   CUQA is Stage 1 of the multi-agent refactoring platform:
   [User Code Upload / GitHub] -> [CUQA Agent] -> [RDP Agent] -> [Transformation Agent]
   Output from CUQA is passed directly into the RDP (Refactoring & Design Pattern) Agent.

2. KEY SECURITY FUNCTIONS & HARDENING:
   - `_safe_extract_zip()`: Mitigates Zip Slip vulnerabilities by verifying that no member
     path inside uploaded ZIP archives escapes the designated extraction directory.
   - `_safe_resolve_path()`: Prevents Directory Traversal attacks (e.g. `../../etc/passwd`)
     by validating relative paths against workspace boundaries using `Path.relative_to()`.
   - Hostname Validation: Validates GitHub URLs strictly against `parsed.hostname == "github.com"`
     to block SSRF and domain spoofing attacks (e.g., `github.com.evil.com`).
   - Resource Quotas: Imforces a strict 500 MB upload / download memory ceiling (`MAX_ZIP_SIZE_MB`).

3. WORKSPACE MANAGEMENT:
   - Maintains single active workspace state in `_workspace` dictionary per server process.
===============================================================================
"""

import os
import sys
import json
import shutil
import zipfile
import tempfile
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
from pydantic import BaseModel, HttpUrl

import requests
from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Body, Request, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, HttpUrl

class GitHubRepoRequest(BaseModel):
    url: HttpUrl  # Built-in Pydantic URL syntax validation

# ---------------------------------------------------------------------------
# Dynamic Python Path Configuration
# Ensures sibling imports (ast_parser, ast_visualizer, report_generator) resolve
# regardless of current working directory when starting uvicorn.
# ---------------------------------------------------------------------------
SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from ast_parser import parse_source, detect_language
from ast_visualizer import enrich_ast, build_summary
from report_generator import generate_file_report, generate_repo_report, build_repo_name_index
from repository_understanding import analyze_repository_overview

# ---------------------------------------------------------------------------
# FastAPI Application Initialization & CORS Configuration
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

cors_origins_env = os.getenv("CUQA_CORS_ORIGINS", "*").strip()
if cors_origins_env == "*":
    origins = ["*"]
    origin_regex = r"https?://.*"
else:
    origins = [o.strip() for o in cors_origins_env.split(",") if o.strip()]
    origin_regex = None

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Multi-Tenant Workspace Session State
# Isolates repositories by session_id / user_id to prevent cross-user collisions in cloud deployments.
# ---------------------------------------------------------------------------
_workspaces: dict[str, dict] = {}


def _get_workspace_dict(session_id: str) -> dict:
    """Return the workspace dictionary for a given session.
    If session is 'default' and no repo is loaded in default,
    fallback to the latest active workspace so legacy / proxy callers without headers resolve.
    """
    sid = session_id or "default"
    if sid not in _workspaces:
        _workspaces[sid] = {
            "root": None,
            "source": None,
            "repo_name": None,
            "files": [],
        }

    ws = _workspaces[sid]
    if sid == "default" and not ws.get("root"):
        # For default/unspecified sessions only, find the most recently loaded active workspace
        for k, candidate in reversed(list(_workspaces.items())):
            if candidate.get("root") and os.path.exists(candidate["root"]):
                return candidate

    return ws


class _WorkspaceProxy:
    """
    Backwards compatibility proxy for test suites and code referencing `_workspace`.
    Delegates to `_workspaces['default']`.
    """
    def __getitem__(self, key):
        return _get_workspace_dict("default")[key]

    def __setitem__(self, key, value):
        _get_workspace_dict("default")[key] = value

    def get(self, key, default=None):
        return _get_workspace_dict("default").get(key, default)

    def update(self, *args, **kwargs):
        _get_workspace_dict("default").update(*args, **kwargs)

    def __iter__(self):
        return iter(_get_workspace_dict("default"))

    def __len__(self):
        return len(_get_workspace_dict("default"))

    def items(self):
        return _get_workspace_dict("default").items()

    def keys(self):
        return _get_workspace_dict("default").keys()

    def values(self):
        return _get_workspace_dict("default").values()


_workspace: dict = _WorkspaceProxy()  # type: ignore


def _extract_session_id(request: Request) -> str:
    """
    Extracts session or user identifier from headers or query parameters.
    Priority:
      1. Header 'X-Session-ID'
      2. Header 'X-User-ID'
      3. Query parameter 'session_id' or 'sessionId'
      4. Header 'Authorization' (decode sub/user_id from JWT payload if present)
      5. Fallback to 'default'
    """
    sid = request.headers.get("X-Session-ID") or request.headers.get("x-session-id")
    if sid and sid.strip():
        return sid.strip()

    uid = request.headers.get("X-User-ID") or request.headers.get("x-user-id")
    if uid and uid.strip():
        return uid.strip()

    q_sid = request.query_params.get("session_id") or request.query_params.get("sessionId")
    if q_sid and q_sid.strip():
        return q_sid.strip()

    # If Authorization header contains Bearer JWT token, extract sub
    auth_header = request.headers.get("Authorization") or request.headers.get("authorization")
    if auth_header and auth_header.startswith("Bearer "):
        try:
            token = auth_header[7:].strip()
            parts = token.split(".")
            if len(parts) >= 2:
                import base64
                padded = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
                payload_json = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
                payload = json.loads(payload_json)
                sub = payload.get("sub") or payload.get("user_id") or payload.get("id")
                if sub:
                    return f"user_{sub}"
        except Exception:
            pass

    return "default"

SUPPORTED_EXTENSIONS = {".py", ".java", ".c", ".h"}


# ---------------------------------------------------------------------------
# SPECIAL SECURITY HELPER FUNCTIONS
# ---------------------------------------------------------------------------

def _safe_extract_zip(zip_file: zipfile.ZipFile, extract_dir: str) -> None:
    """
    SPECIAL SECURITY FUNCTION: Mitigate Zip Slip Path Traversal Vulnerabilities.

    -------------------------------------------------------------------------
    VIVA/SECURITY EXAM NOTE (Zip Slip Explanation):
    - Zip Slip is a critical arbitrary file write vulnerability. Malicious ZIP archives
      can contain filenames with relative directory traversal characters (e.g. `../../bin/sh`).
    - Standard `zipfile.extractall()` trusts these paths and writes files outside `extract_dir`.
    - This protection resolves the target file path and uses `Path.relative_to(extract_path)`
      to confirm that every extracted file stays strictly inside `extract_dir`.
    -------------------------------------------------------------------------
    """
    extract_path = Path(extract_dir).resolve()
    for member in zip_file.namelist():
        member_path = (extract_path / member).resolve()
        try:
            member_path.relative_to(extract_path)
        except ValueError:
            raise HTTPException(
                400,
                f"ZIP entry '{member}' attempts to escape the extraction directory (Zip Slip).",
            )
    zip_file.extractall(extract_dir)


def _safe_resolve_path(workspace_root: str, rel_path: str) -> str:
    """
    SPECIAL SECURITY FUNCTION: Prevent Arbitrary File Access / Directory Traversal.

    -------------------------------------------------------------------------
    VIVA/SECURITY EXAM NOTE (Path Traversal Explanation):
    - When users query an API endpoint with a filename string like `file_path="../../etc/passwd"`,
      naive string concatenation allows reading arbitrary host system files.
    - `_safe_resolve_path` canonicalizes `(root / rel_path).resolve()` and checks that it
      is contained within `workspace_root` using `candidate.relative_to(root)`.
    -------------------------------------------------------------------------
    """
    root = Path(workspace_root).resolve()
    candidate = (root / rel_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise HTTPException(
            400,
            f"Path '{rel_path}' attempts to escape the workspace directory.",
        )
    return str(candidate)


def _find_source_files(root: str) -> list[str]:
    """
    Recursively discover all supported source files in the active workspace.

    VIVA NOTE: Filters out irrelevant directories like `.git`, `node_modules`, `__pycache__`,
    `venv`, and build targets to speed up analysis.
    """
    result = []
    for dirpath, _, filenames in os.walk(root):
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


# Extension → canonical display name mapping
_EXT_TO_LANG = {".py": "Python", ".java": "Java", ".c": "C", ".h": "C"}


def _get_language_breakdown(file_list: list[str]) -> dict:
    """
    SPECIAL FUNCTION: Analyze repository language distribution and Polyglot status.

    -------------------------------------------------------------------------
    VIVA/INTERVIEW NOTE (Polyglot Repository Analysis):
    - Scans discovered files and counts file occurrences per language.
    - Determines:
      1. `primary_language`: The language with the highest file count.
      2. `is_polyglot`: True if files from multiple distinct languages (e.g. Python & C) exist.
      3. `detected_languages`: Sorted array of language names by frequency.
    -------------------------------------------------------------------------

    Returns:
        dict: Language distribution and polyglot diagnostic summary.
    """
    counts: dict[str, int] = {}
    for path in file_list:
        ext = os.path.splitext(path)[-1].lower()
        lang = _EXT_TO_LANG.get(ext)
        if lang:
            counts[lang] = counts.get(lang, 0) + 1

    if not counts:
        return {
            "breakdown": {},
            "detected_languages": [],
            "primary_language": None,
            "is_polyglot": False,
        }

    sorted_langs = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    detected = [lang for lang, _ in sorted_langs]
    return {
        "breakdown": counts,
        "detected_languages": detected,
        "primary_language": detected[0],
        "is_polyglot": len(detected) > 1,
    }


def _build_tree(root: str) -> dict:
    """
    SPECIAL RECURSIVE ALGORITHM: Build a hierarchical directory tree for UI visualizer.

    -------------------------------------------------------------------------
    VIVA/INTERVIEW NOTE (Directory Tree Traversal):
    - Uses Depth-First Search (DFS) recursion (`_recurse`) over file system directories.
    - Replaces Windows backslashes `\\` with POSIX forward slashes `/` for cross-platform UI consistency.
    - Skips build folders, virtual environments (`venv`), and version control directories (`.git`).
    -------------------------------------------------------------------------
    """

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
# API Endpoints Definition
# ---------------------------------------------------------------------------

@app.get("/")
def root():
    return {"agent": "CUQA", "status": "running", "version": "1.0.0"}


@app.get("/api/health")
def health(request: Request):
    session_id = _extract_session_id(request)
    ws = _get_workspace_dict(session_id)
    return {
        "status": "ok",
        "workspace_loaded": ws["root"] is not None,
        "session_id": session_id,
    }


# ── 1. Upload ZIP ────────────────────────────────────────────────────────────

MAX_ZIP_SIZE_MB = 500
MAX_ZIP_SIZE_BYTES = MAX_ZIP_SIZE_MB * 1024 * 1024  # 500 MB limit

@app.post("/api/upload-zip")
async def upload_zip(request: Request, file: UploadFile = File(...)):
    """
    Accept a ZIP file (up to 500 MB), extract it to an isolated session workspace, and
    scan for supported source files.
    """
    assert file.filename is not None
    if not file.filename.endswith(".zip"):
        raise HTTPException(400, "Only .zip files are supported.")

    session_id = _extract_session_id(request)
    ws = _get_workspace_dict(session_id)

    # Clean previous workspace for this session only
    if ws["root"] and os.path.exists(ws["root"]):
        shutil.rmtree(ws["root"], ignore_errors=True)

    tmp_dir = tempfile.mkdtemp(prefix=f"cuqa_{session_id}_")
    zip_path = os.path.join(tmp_dir, "upload.zip")

    total_bytes = 0
    with open(zip_path, "wb") as f:
        while chunk := await file.read(1024 * 1024):  # Read in 1MB chunks
            total_bytes += len(chunk)
            if total_bytes > MAX_ZIP_SIZE_BYTES:
                f.close()
                shutil.rmtree(tmp_dir, ignore_errors=True)
                raise HTTPException(413, f"ZIP file exceeds the {MAX_ZIP_SIZE_MB}MB size limit.")
            f.write(chunk)

    extract_dir = os.path.join(tmp_dir, "extracted")
    os.makedirs(extract_dir, exist_ok=True)

    try:
        with zipfile.ZipFile(zip_path, "r") as z:
            _safe_extract_zip(z, extract_dir)  # SEC-FIX: Zip Slip prevention
    except zipfile.BadZipFile:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise HTTPException(400, "Invalid or corrupted ZIP file.")
    except HTTPException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    # If ZIP has a single top-level folder, go inside it
    entries = os.listdir(extract_dir)
    if len(entries) == 1 and os.path.isdir(os.path.join(extract_dir, entries[0])):
        extract_dir = os.path.join(extract_dir, entries[0])

    source_files = _find_source_files(extract_dir)
    lang_info = _get_language_breakdown(source_files)

    ws.update({
        "root": extract_dir,
        "source": "zip",
        "repo_name": file.filename.replace(".zip", ""),
        "files": source_files,
    })

    return {
        "message": "ZIP uploaded and extracted successfully.",
        "session_id": session_id,
        "repo_name": ws["repo_name"],
        "files_found": len(source_files),
        "source_files": source_files[:100],  # cap for response size
        # Language detection results
        "language_breakdown":   lang_info["breakdown"],
        "detected_languages":   lang_info["detected_languages"],
        "primary_language":     lang_info["primary_language"],
        "is_polyglot":          lang_info["is_polyglot"],
    }


# ── 2. GitHub Repo ───────────────────────────────────────────────────────────

@app.post("/api/github-repo")
def load_github_repo(req: Request, payload: GitHubRepoRequest):
    url = str(payload.url).rstrip('/')  # Normalize trailing slash

    # Remove .git suffix if present
    if url.endswith('.git'):
        url = url[:-4]

    # SEC-FIX: Exact hostname validation — prevents github.com.evil.com spoofing
    parsed = urlparse(url)
    if parsed.hostname != "github.com":
        raise HTTPException(400, "Only github.com URLs are supported.")
    
    repo_name = url.split("/")[-1]
    
    # Try multiple common branches
    response = None
    for branch in ["main", "master", "develop", "trunk"]:
        zip_url = f"{url}/archive/refs/heads/{branch}.zip"
        try:
            res = requests.get(zip_url, timeout=120, stream=True, allow_redirects=True)
            if res.status_code == 200:
                content_length = res.headers.get("Content-Length")
                if content_length and int(content_length) > MAX_ZIP_SIZE_BYTES:
                    raise HTTPException(413, f"GitHub repository archive exceeds the {MAX_ZIP_SIZE_MB}MB size limit.")
                response = res
                break
        except requests.exceptions.RequestException:
            continue
    else:
        raise HTTPException(502, f"Could not download '{repo_name}' from GitHub. Repo may be private or use non-standard branch names.")
    
    session_id = _extract_session_id(req)
    ws = _get_workspace_dict(session_id)

    # Clean previous workspace for this session
    if ws["root"] and os.path.exists(ws["root"]):
        shutil.rmtree(ws["root"], ignore_errors=True)

    tmp_dir = tempfile.mkdtemp(prefix=f"cuqa_gh_{session_id}_")
    zip_path = os.path.join(tmp_dir, "repo.zip")

    total_bytes = 0
    with open(zip_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=65536):
            if chunk:
                total_bytes += len(chunk)
                if total_bytes > MAX_ZIP_SIZE_BYTES:
                    f.close()
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                    raise HTTPException(413, f"Downloaded GitHub repository archive exceeds the {MAX_ZIP_SIZE_MB}MB size limit.")
                f.write(chunk)

    extract_dir = os.path.join(tmp_dir, "extracted")
    os.makedirs(extract_dir, exist_ok=True)

    try:
        with zipfile.ZipFile(zip_path, "r") as z:
            _safe_extract_zip(z, extract_dir)  # SEC-FIX: Zip Slip prevention
    except zipfile.BadZipFile:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise HTTPException(500, "Downloaded archive is not a valid ZIP.")
    except HTTPException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    entries = os.listdir(extract_dir)
    if len(entries) == 1 and os.path.isdir(os.path.join(extract_dir, entries[0])):
        extract_dir = os.path.join(extract_dir, entries[0])

    source_files = _find_source_files(extract_dir)
    lang_info = _get_language_breakdown(source_files)

    ws.update({
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
        # Language detection results
        "language_breakdown":   lang_info["breakdown"],
        "detected_languages":   lang_info["detected_languages"],
        "primary_language":     lang_info["primary_language"],
        "is_polyglot":          lang_info["is_polyglot"],
    }


# ── 3. Project Structure ─────────────────────────────────────────────────────

@app.get("/api/project-structure")
def project_structure(request: Request):
    """Return the file tree of the loaded repository for the current session."""
    session_id = _extract_session_id(request)
    ws = _get_workspace_dict(session_id)
    if not ws["root"]:
        raise HTTPException(400, "No repository loaded. Upload a ZIP or provide a GitHub URL first.")
    tree = _build_tree(ws["root"])
    return {
        "session_id": session_id,
        "repo_name": ws["repo_name"],
        "source": ws["source"],
        "total_source_files": len(ws["files"]),
        "tree": tree,
    }


# ── 4. Parse AST ─────────────────────────────────────────────────────────────

@app.post("/api/parse-ast")
def parse_ast(request: Request, payload: dict):
    """
    Parse a specific file in the workspace and return its AST JSON.

    Body: { "file_path": "relative/path/to/File.java" }
    """
    session_id = _extract_session_id(request)
    ws = _get_workspace_dict(session_id)
    if not ws["root"]:
        raise HTTPException(400, "No repository loaded.")

    rel_path = payload.get("file_path", "")
    if not rel_path:
        raise HTTPException(400, "file_path is required.")

    # SEC-FIX: path traversal prevention
    full_path = _safe_resolve_path(ws["root"], rel_path)
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
        "source_code": source,   # raw file content — used by transformation agent for zip download
    }


# ── 5. Quality Report ────────────────────────────────────────────────────────

@app.post("/api/quality-report")
def quality_report(request: Request, payload: dict = None): # type: ignore
    """
    Generate a quality report for one or all files in the workspace.

    Body (optional): { "file_path": "relative/path/File.py" }
    If file_path omitted, reports on all loaded files (capped at 50).
    """
    session_id = _extract_session_id(request)
    ws = _get_workspace_dict(session_id)
    if not ws["root"]:
        raise HTTPException(400, "No repository loaded.")

    file_path = (payload or {}).get("file_path")

    if file_path:
        # SEC-FIX: path traversal prevention
        full_path = _safe_resolve_path(ws["root"], file_path)
        if not os.path.isfile(full_path):
            raise HTTPException(404, f"File not found: {file_path}")
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            source = f.read()
        report = generate_file_report(source, os.path.basename(full_path))
        return {"type": "file", "report": report}

    # All files — cap at 50 to avoid timeout
    # Collect (relative_filename, source_text) for every file so that
    # generate_repo_report can build a cross-file name index for accurate
    # dead-code detection.  We read each file once and reuse the text.
    file_reports = []
    all_sources: list[tuple[str, str]] = []   # (basename, source) for repo index
    for rel in ws["files"][:50]:
        full_path = os.path.join(ws["root"], rel)
        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                source = f.read()
            report = generate_file_report(source, os.path.basename(full_path))
            report["relative_path"] = rel.replace("\\", "/")
            # Collect Python sources for repo-wide dead-code index
            if os.path.splitext(full_path)[-1].lower() == ".py":
                all_sources.append((os.path.basename(full_path), source))
        except Exception as exc:
            report = {"file": rel, "error": str(exc)}
        file_reports.append(report)

    # Pass source texts so generate_repo_report can apply cross-file dead-code
    # detection: functions imported by other files are NOT flagged as dead.
    repo_report = generate_repo_report(file_reports, sources=all_sources)
    repo_report["repo_name"] = ws["repo_name"]
    return {"type": "repository", "report": repo_report}


# ── 6. List source files ─────────────────────────────────────────────────────

@app.get("/api/files")
def list_files(request: Request):
    """Return list of all discovered source files in the workspace for current session."""
    session_id = _extract_session_id(request)
    ws = _get_workspace_dict(session_id)
    if not ws["root"]:
        raise HTTPException(400, "No repository loaded.")
    return {
        "session_id": session_id,
        "repo_name": ws["repo_name"],
        "files": ws["files"],
        "total": len(ws["files"]),
    }


# ── 7. Repository Understanding Overview ──────────────────────────────────────

@app.get("/api/repository-overview")
def repository_overview(request: Request):
    """
    Generate a beginner-friendly structural understanding of the loaded repository.

    Derives a complete, evidence-based overview using only static analysis
    (no external LLM or API calls required).  Returns:
      - Repository statistics (files, LOC, directories, languages)
      - Language distribution with percentages
      - Detected build tools, dependency managers, CI/CD, deployment tools
      - Likely application entry points with confidence and evidence
      - Important directories with role classification
      - Structurally important files
      - Recommended newcomer reading path
      - Static module dependency graph (nodes + edges, capped at 50 nodes)
      - Architectural pattern clues with evidence
      - Subproject / monorepo detection

    Part of CUQA's Code Understanding responsibility (distinct from Quality
    Assessment which is handled by /api/quality-report).
    """
    session_id = _extract_session_id(request)
    ws = _get_workspace_dict(session_id)
    if not ws["root"]:
        raise HTTPException(400, "No repository loaded. Upload a ZIP or provide a GitHub URL first.")

    try:
        overview = analyze_repository_overview(
            root=ws["root"],
            repo_name=ws["repo_name"] or "unknown",
            source_files=ws["files"],
        )
    except Exception as exc:
        raise HTTPException(500, f"Repository analysis failed: {exc}")

    return overview


# ── 7. Update Workspace Files ────────────────────────────────────────────────

class WorkspaceFileUpdate(BaseModel):
    file_path: str
    content: str

class WorkspaceUpdateRequest(BaseModel):
    files: list[WorkspaceFileUpdate]

@app.post("/api/update-workspace")
def update_workspace(request: Request, payload: WorkspaceUpdateRequest):
    """
    Update/overwrite workspace source files with refactored code for current session.
    Allows CUQA to re-analyze refactored code and accurately report updated metrics/smells.
    """
    session_id = _extract_session_id(request)
    ws = _get_workspace_dict(session_id)
    if not ws["root"]:
        raise HTTPException(400, "No repository loaded.")

    updated_count = 0
    errors = []

    for item in payload.files:
        rel_path = item.file_path.strip()
        if not rel_path:
            continue
        try:
            full_path = _safe_resolve_path(ws["root"], rel_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(item.content)
            updated_count += 1
        except Exception as exc:
            errors.append({"file_path": rel_path, "error": str(exc)})

    return {
        "status": "success",
        "updated_files": updated_count,
        "errors": errors,
    }


# ── 8. Fetch Raw Source Files ────────────────────────────────────────────────

class SourceFilesRequest(BaseModel):
    file_paths: list[str]

class SingleSourceFileRequest(BaseModel):
    file_path: str

@app.post("/api/source-files")
@app.post("/api/cuqa/source-files")
def fetch_source_files(request: Request, raw_payload: dict = Body(...)):
    """
    Return raw source code content for a list of workspace file paths.
    Used by downstream Transformation (SCTVA) and Orchestration (DIWO) Agents.
    """
    session_id = _extract_session_id(request)
    ws = _get_workspace_dict(session_id)
    if not ws["root"]:
        raise HTTPException(400, "No repository loaded.")

    file_paths = raw_payload.get("file_paths")
    if not isinstance(file_paths, list):
        raise HTTPException(400, "file_paths must be a list of string paths.")

    found_files = []
    missing_files = []

    for rel_path in file_paths:
        clean_path = str(rel_path).strip()
        if not clean_path:
            continue
        try:
            full_path = _safe_resolve_path(ws["root"], clean_path)
            if os.path.isfile(full_path):
                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                found_files.append({
                    "file_name": clean_path,
                    "file_path": clean_path,
                    "language": detect_language(clean_path),
                    "source_code": content,
                    "source_mode": "raw",
                })
            else:
                missing_files.append(clean_path)
        except Exception:
            missing_files.append(clean_path)

    return {
        "files": found_files,
        "imported": len(found_files),
        "total": len(file_paths),
        "missing": missing_files,
        "source": "cuqa_workspace",
    }


@app.post("/api/source-file")
@app.post("/api/cuqa/source-file")
def fetch_single_source_file_post(request: Request, payload: SingleSourceFileRequest):
    """Return raw source code content for a single workspace file path (POST)."""
    session_id = _extract_session_id(request)
    ws = _get_workspace_dict(session_id)
    if not ws["root"]:
        raise HTTPException(400, "No repository loaded.")

    clean_path = payload.file_path.strip()
    if not clean_path:
        raise HTTPException(400, "file_path is required.")

    full_path = _safe_resolve_path(ws["root"], clean_path)
    if not os.path.isfile(full_path):
        raise HTTPException(404, f"File not found in workspace: {clean_path}")

    with open(full_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    return {
        "file_name": clean_path,
        "file_path": clean_path,
        "language": detect_language(clean_path),
        "source_code": content,
        "source_mode": "raw",
    }


@app.get("/api/raw-source")
@app.get("/api/source-file")
@app.get("/api/cuqa/raw-source")
@app.get("/api/cuqa/source-file")
def fetch_single_source_file_get(request: Request, file_path: str = ""):
    """Return raw source code content for a single workspace file path (GET query param)."""
    session_id = _extract_session_id(request)
    ws = _get_workspace_dict(session_id)
    if not ws["root"]:
        raise HTTPException(400, "No repository loaded.")

    clean_path = file_path.strip()
    if not clean_path:
        raise HTTPException(400, "file_path parameter is required.")

    full_path = _safe_resolve_path(ws["root"], clean_path)
    if not os.path.isfile(full_path):
        raise HTTPException(404, f"File not found in workspace: {clean_path}")

    with open(full_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    return {
        "file_name": clean_path,
        "file_path": clean_path,
        "language": detect_language(clean_path),
        "source_code": content,
        "source_mode": "raw",
    }


# ---------------------------------------------------------------------------
# Run directly
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=True, reload_dirs=[str(SRC_DIR)])



