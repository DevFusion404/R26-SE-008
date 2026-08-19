"""
CUQA Agent HTTP Client
======================
R26-SE-008 | Bandara S M Y M | IT22277886

The Code Understanding & Quality Assessment (CUQA) agent runs as a separate
FastAPI service (default http://localhost:8080). DIWO consumes one endpoint:

    POST /api/quality-report      ->  { "type": "repository",
                                        "report": { summary, files[], repo_name } }
                                  or  { "type": "file",
                                        "report": { file, language, metrics, ... } }

Body is optional: {"file_path": "relative/path/File.py"} narrows the report to a
single file; omitting it reports on the whole loaded workspace.

Stdlib-only (urllib) on purpose — the DIWO backend keeps its two-package
requirements.txt and needs no extra install to talk to CUQA.
"""

import json
import urllib.error
import urllib.request

from config import cuqa_base_url

QUALITY_REPORT_PATH = "/api/quality-report"
FILES_PATH = "/api/files"

__all__ = [
    "CUQAError", "cuqa_base_url", "fetch_quality_report",
    "fetch_workspace_files", "probe_cuqa",
]


class CUQAError(RuntimeError):
    """Raised when the CUQA agent cannot serve a quality report.

    `status` carries the HTTP status to hand back to the frontend:
      400 – CUQA is up but has no repository loaded (developer must upload one)
      503 – CUQA is not running / not reachable
    """

    def __init__(self, message: str, status: int = 502, detail=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.detail = detail


def _request(method: str, path: str, body=None, timeout: int = 120) -> dict:
    url = f"{cuqa_base_url()}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw.strip() else {}

    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(detail)
            detail = parsed.get("detail", detail) if isinstance(parsed, dict) else detail
        except ValueError:
            pass
        raise CUQAError(
            f"CUQA agent returned HTTP {exc.code}: {detail}",
            status=exc.code,
            detail=detail,
        ) from exc

    except urllib.error.URLError as exc:
        raise CUQAError(
            f"CUQA agent is not reachable at {cuqa_base_url()} ({exc.reason}). "
            f"Start it with: uvicorn main:app --port 8080 (from agents/cuqa_agent/src).",
            status=503,
        ) from exc

    except TimeoutError as exc:
        raise CUQAError(
            f"CUQA agent at {cuqa_base_url()} timed out after {timeout}s while "
            f"generating the quality report.",
            status=504,
        ) from exc

    except ValueError as exc:  # malformed JSON body
        raise CUQAError(
            f"CUQA agent returned a non-JSON response from {path}: {exc}",
            status=502,
        ) from exc


def fetch_quality_report(file_path: str = None, timeout: int = 120) -> dict:
    """Call POST /api/quality-report and return the raw CUQA payload."""
    body = {"file_path": file_path} if file_path else {}
    return _request("POST", QUALITY_REPORT_PATH, body=body, timeout=timeout)


def fetch_workspace_files(timeout: int = 15) -> dict:
    """Call GET /api/files — {repo_name, files[], total}."""
    return _request("GET", FILES_PATH, timeout=timeout)


def probe_cuqa(timeout: int = 5) -> dict:
    """Cheap reachability check used by the frontend to explain its data source.

    Never raises: a CUQA that is up but has no repository loaded answers 400,
    which is reported as reachable=True, repo_loaded=False.
    """
    status = {
        "reachable": False,
        "repo_loaded": False,
        "repo_name": None,
        "file_count": 0,
        "cuqa_url": cuqa_base_url(),
        "message": "",
    }

    try:
        payload = fetch_workspace_files(timeout=timeout)
    except CUQAError as exc:
        status["reachable"] = exc.status != 503
        status["message"] = exc.message
        return status

    status.update({
        "reachable": True,
        "repo_loaded": bool(payload.get("files")),
        "repo_name": payload.get("repo_name"),
        "file_count": payload.get("total", len(payload.get("files") or [])),
        "message": "CUQA workspace loaded.",
    })
    return status
