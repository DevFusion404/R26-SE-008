# CUQA Agent Test Findings & Defect Log

This document records security vulnerabilities, edge case defects, and architectural findings discovered during the comprehensive test suite implementation for the CUQA Agent (R26-SE-008).

---

## Security Vulnerabilities Discovered & Fixed

### 1. BUG-SEC-001 (Zip Slip Vulnerability in Archive Extraction)
- **Severity**: Critical
- **Component**: `main.py` (`upload_zip` and `load_github_repo`)
- **Description**: The archive extraction used `zipfile.ZipFile.extractall()` without inspecting member filenames. An archive entry containing directory traversal sequences (e.g. `../../evil.py`) could overwrite files outside the temporary workspace directory.
- **Steps to reproduce**: Upload a ZIP archive containing a file named `../../evil.py`.
- **Expected**: Rejection of the archive or safe extraction constrained strictly inside `tmp_dir`.
- **Actual**: `extractall()` extracted files outside the destination directory.
- **Fix applied**: Created `_safe_extract_zip()` helper in `main.py` which resolves each member's target path and asserts it is relative to `extract_dir` using `Path.relative_to()`. Raises `HTTPException(400)` if a Zip Slip attempt is detected.
- **Regression test**: `tests/security/test_zip_security.py` -> `test_zip_slip_rejection()`
- **Status**: Fixed & Protected

---

### 2. BUG-SEC-002 (Path Traversal Vulnerability in AST & Report Endpoints)
- **Severity**: Critical
- **Component**: `main.py` (`/api/parse-ast` and `/api/quality-report`)
- **Description**: User-provided `file_path` parameters were concatenated directly with `_workspace["root"]` using `os.path.join()`. Path sequences like `../../../etc/passwd` or `..\..\secret.py` escaped the workspace container and read arbitrary system files.
- **Steps to reproduce**: `POST /api/parse-ast` with body `{"file_path": "../../../etc/passwd"}`.
- **Expected**: Rejection of paths attempting to escape workspace root.
- **Actual**: `os.path.join` resolved to root filesystem paths and attempted file reading.
- **Fix applied**: Created `_safe_resolve_path()` helper in `main.py` which resolves absolute paths and verifies containment under `_workspace["root"]` using `Path.relative_to()`. Raises `HTTPException(400)` if traversal is attempted.
- **Regression test**: `tests/security/test_path_traversal.py` -> `test_parse_ast_rejects_path_traversal()`
- **Status**: Fixed & Protected

---

### 3. BUG-SEC-003 (GitHub Domain Spoofing in GitHub Repo Endpoint)
- **Severity**: High
- **Component**: `main.py` (`load_github_repo`)
- **Description**: Domain validation checked `"github.com" in url`. This allowed malicious URLs such as `https://github.com.evil.com/user/repo` or `https://evilgithub.com/user/repo`.
- **Steps to reproduce**: `POST /api/github-repo` with `{"url": "https://github.com.evil.com/user/repo"}`.
- **Expected**: Reject non-github.com hostnames.
- **Actual**: Passed substring validation and attempted network request to `github.com.evil.com`.
- **Fix applied**: Parsed URL using `urllib.parse.urlparse()` and verified `parsed.hostname == "github.com"` exactly.
- **Regression test**: `tests/security/test_github_url_validation.py` -> `test_github_url_spoofing_rejected()`
- **Status**: Fixed & Protected

---

## Architectural & Research Observations

### 1. OBS-DISC-001 (Directory Prefix Skipping in File Discovery)
- **Severity**: Low (Behavioral / Documented)
- **Component**: `main.py` (`_find_source_files`)
- **Description**: Ignore filter checks `rel_dir.startswith(p)` for `p = ("build", "venv", ...)`. Consequently, legitimate directory names sharing these prefixes (e.g. `builder/`, `build_tools/`, `venv_data/`) are also skipped.
- **Action taken**: Preserved current research implementation behavior and added test documentation in `tests/unit/test_file_discovery.py`.

---

### Summary of Defect Fixes

| ID | Severity | Description | Fix Location | Test File |
| --- | --- | --- | --- | --- |
| BUG-SEC-001 | Critical | Zip Slip path extraction | `main.py:_safe_extract_zip` | `security/test_zip_security.py` |
| BUG-SEC-002 | Critical | Path traversal file access | `main.py:_safe_resolve_path` | `security/test_path_traversal.py` |
| BUG-SEC-003 | High | GitHub URL domain spoofing | `main.py:load_github_repo` | `security/test_github_url_validation.py` |
