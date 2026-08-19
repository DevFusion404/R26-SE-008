"""
SCTVA Agent HTTP Client
=======================
R26-SE-008 | Bandara S M Y M | IT22277886

The Safe Code Transformation & Validation (SCTVA) agent runs as a separate
Flask service (default http://localhost:8002, SCTVA_PORT). It exposes:

    GET  /health              ->  service liveness
    POST /sctva/execute       ->  { actions[], source_files[] } -> transformation result
    POST /sctva/cuqa-sources  ->  { paths[] } -> the source text of those paths

Written to match cuqa_client and rdp_client: stdlib urllib only, one error
type carrying the status the frontend needs, no DIWO workflow rules inside.

Scope note — the browser currently posts the approved plan to SCTVA itself
(frontend services/sctvaApi.js), which is the existing, working integration
and is left exactly as it is. This client gives the orchestrator the same
reach for its own integration checks, and is the place a future server-side
hand-off belongs so that no new direct DIWO -> SCTVA path has to be invented.
"""

import json
import urllib.error
import urllib.request

from config import sctva_base_url

HEALTH_PATH = "/health"
EXECUTE_PATH = "/sctva/execute"
SOURCES_PATH = "/sctva/cuqa-sources"

__all__ = [
    "SCTVAError", "sctva_base_url", "execute_transformation",
    "fetch_workspace_sources", "probe_sctva",
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


def fetch_workspace_sources(paths: list, timeout: int = 60) -> dict:
    """POST /sctva/cuqa-sources — read repo-relative paths out of the workspace."""
    return _request("POST", SOURCES_PATH, body={"paths": list(paths or [])}, timeout=timeout)


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
