"""
SCTVA Agent HTTP Client
=======================
R26-SE-008 | Bandara S M Y M | IT22277886

The Safe Code Transformation & Validation (SCTVA) agent runs as a separate
Flask service (default http://localhost:8002, SCTVA_PORT). It exposes:

    GET  /health              ->  service liveness
    GET  /sctva/health        ->  { supported_actions[], supported_capabilities[] }
    POST /sctva/execute       ->  { actions[], source_files[] } -> transformation result
    POST /sctva/cuqa-sources  ->  { file_paths[] } -> the source text of those paths

Written to match cuqa_client and rdp_client: stdlib urllib only, one error
type carrying the status the frontend needs, no DIWO workflow rules inside.

The DIWO browser no longer talks to SCTVA at all: the approved plan is posted
here by services/transformation_service.py, behind
POST /api/workflows/<id>/transform, so every agent hand-off goes

    DIWO frontend -> Orchestration Agent -> specialized agent
"""

import json
import urllib.error
import urllib.request

from config import sctva_base_url

HEALTH_PATH = "/health"
AGENT_HEALTH_PATH = "/sctva/health"
EXECUTE_PATH = "/sctva/execute"
SOURCES_PATH = "/sctva/cuqa-sources"

__all__ = [
    "SCTVAError", "sctva_base_url", "execute_transformation",
    "fetch_workspace_sources", "fetch_supported_actions", "probe_sctva",
]


class SCTVAError(RuntimeError):
    """Raised when the SCTVA agent cannot serve a request.

    `status` carries the HTTP status to hand back to the frontend:
      422 – SCTVA ran but refused the actions it was given
      503 – SCTVA is not running / not reachable
      504 – SCTVA timed out while transforming
    """

    def __init__(self, message: str, status: int = 502, detail=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.detail = detail


def _request(method: str, path: str, body=None, timeout: int = 120):
    url = f"{sctva_base_url()}{path}"
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
            detail = parsed.get("error", detail) if isinstance(parsed, dict) else detail
        except ValueError:
            pass
        raise SCTVAError(
            f"SCTVA agent returned HTTP {exc.code}: {detail}",
            status=exc.code,
            detail=detail,
        ) from exc

    except urllib.error.URLError as exc:
        raise SCTVAError(
            f"SCTVA agent is not reachable at {sctva_base_url()} ({exc.reason}). "
            f"Start it with: cd agents/transformation_agent/safe_code_transformation_agent "
            f"&& python app.py",
            status=503,
        ) from exc

    except TimeoutError as exc:
        raise SCTVAError(
            f"SCTVA agent at {sctva_base_url()} timed out after {timeout}s while "
            f"transforming.",
            status=504,
        ) from exc

    except ValueError as exc:  # malformed JSON body
        raise SCTVAError(
            f"SCTVA agent returned a non-JSON response from {path}: {exc}",
            status=502,
        ) from exc


def execute_transformation(payload: dict, timeout: int = 120) -> dict:
    """POST /sctva/execute with an already-approved action list.

    `payload` must carry only the steps the developer approved — SCTVA
    executes everything it is given.
    """
    return _request("POST", EXECUTE_PATH, body=payload, timeout=timeout)


#: /sctva/cuqa-sources truncates the path list at 1000 entries, so send it in
#: batches. 400 keeps each request comfortably inside that cap.
SOURCE_BATCH_SIZE = 400


def fetch_workspace_sources(file_paths: list, timeout: int = 60) -> dict:
    """POST /sctva/cuqa-sources — read repo-relative paths out of the workspace.

    The CUQA report describes files but never ships their contents; SCTVA reads
    them back out of the CUQA temp workspace (%TEMP%/cuqa_*) and returns
    entries already shaped for the `source_files` field of an execute request.

    Batched across SOURCE_BATCH_SIZE, so a whole-project archive of several
    thousand files is one call from the caller's point of view. Files SCTVA
    could not locate come back in `missing`; the caller decides whether that
    is fatal, because a plan spanning ten files should not be blocked by one
    stale path.

    The request field is `file_paths` — that is what the agent reads
    (sctva/integration/api.py::sctva_cuqa_sources).
    """
    requested = [str(p) for p in (file_paths or [])]

    files, missing = [], []
    for start in range(0, len(requested), SOURCE_BATCH_SIZE):
        batch = requested[start:start + SOURCE_BATCH_SIZE]
        payload = _request("POST", SOURCES_PATH, body={"file_paths": batch}, timeout=timeout)
        files.extend(payload.get("files") or [])
        missing.extend(payload.get("missing") or [])

    return {
        "files": files,
        "missing": missing,
        "imported": len(files),
        "total": len(requested),
    }


def fetch_supported_actions(timeout: int = 5):
    """The action types this SCTVA build can execute, from GET /sctva/health.

    This is the live half of the Stage 1 feasibility gate: domain/capability_map
    derives which refactorings CAN be mapped to an action, and this says which
    of those the running agent actually exposes. A build compiled without, say,
    the C transformers would otherwise be advertised as able to fix C smells.

    Returns a set, or None when the agent could not be reached — the caller
    falls back to the static tables rather than reporting everything as
    unfixable.
    """
    try:
        payload = _request("GET", AGENT_HEALTH_PATH, timeout=timeout)
    except SCTVAError:
        return None

    actions = payload.get("supported_actions") if isinstance(payload, dict) else None
    return set(actions) if isinstance(actions, list) else None


def probe_sctva(timeout: int = 5) -> dict:
    """Cheap reachability check used by the integration routes. Never raises."""
    status = {"reachable": False, "sctva_url": sctva_base_url(), "message": ""}

    try:
        payload = _request("GET", HEALTH_PATH, timeout=timeout)
    except SCTVAError as exc:
        # It answered with an HTTP error, so the service itself is up.
        if exc.status != 503 and exc.status != 504:
            status.update(reachable=True, message=f"SCTVA agent responded: {exc.message}")
        else:
            status["message"] = exc.message
        return status

    status.update(reachable=True, message="SCTVA agent is running.")
    if isinstance(payload, dict):
        status["detail"] = payload
    return status
