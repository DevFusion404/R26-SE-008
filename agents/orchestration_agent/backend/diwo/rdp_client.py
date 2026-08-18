"""
RDP Agent HTTP Client
=====================
R26-SE-008 | Bandara S M Y M | IT22277886

The Refactoring Decision & Planning (RDP) agent runs as a separate Flask
service (default http://localhost:5000). DIWO consumes one endpoint:

    POST /generate   ->  { "success": true,
                           "plan":  { plan_id, target, steps[], summary },
                           "trace": { candidate_generation, mcda_selection, ... } }

It accepts a CUQA-shaped quality report ({files: [...], summary: {...}}) and
translates it internally (app._translate_cuqa_to_rdp), so the smell report the
developer just filtered can be posted unchanged — there is no second format to
maintain here.

Two failure modes need separate handling:
  * HTTP error / unreachable — the agent is down or refused the report.
  * HTTP 200 with `error` inside the plan — RDP ran but could not order the
    steps (a circular dependency between refactorings), which is a planning
    failure, not a transport one.

Stdlib-only (urllib) on purpose, matching cuqa_client: the DIWO backend keeps
its small requirements.txt and needs no extra install to talk to RDP.
"""

import json
import os
import urllib.error
import urllib.request

DEFAULT_RDP_URL = "http://localhost:5000"

GENERATE_PATH = "/generate"
INDEX_PATH = "/"


def rdp_base_url() -> str:
    """Base URL of the RDP agent, overridable with the RDP_AGENT_URL env var."""
    return os.environ.get("RDP_AGENT_URL", DEFAULT_RDP_URL).rstrip("/")


class RDPError(RuntimeError):
    """Raised when the RDP agent cannot produce a refactoring plan.

    `status` carries the HTTP status to hand back to the frontend:
      422 – RDP ran but refused to plan for this report
      503 – RDP is not running / not reachable
      504 – RDP timed out while planning
    """

    def __init__(self, message: str, status: int = 502, detail=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.detail = detail


def _request(method: str, path: str, body=None, timeout: int = 120):
    url = f"{rdp_base_url()}{path}"
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
        raise RDPError(
            f"RDP agent returned HTTP {exc.code}: {detail}",
            status=exc.code,
            detail=detail,
        ) from exc

    except urllib.error.URLError as exc:
        raise RDPError(
            f"RDP agent is not reachable at {rdp_base_url()} ({exc.reason}). "
            f"Start it with: cd agents/rdp_agent && python app.py",
            status=503,
        ) from exc

    except TimeoutError as exc:
        raise RDPError(
            f"RDP agent at {rdp_base_url()} timed out after {timeout}s while "
            f"generating the refactoring plan.",
            status=504,
        ) from exc

    except ValueError as exc:  # malformed JSON body
        raise RDPError(
            f"RDP agent returned a non-JSON response from {path}: {exc}",
            status=502,
        ) from exc


def generate_plan(report: dict, timeout: int = 120) -> dict:
    """Post a CUQA-shaped report to /generate and return {plan, trace}.

    `report` must already be narrowed to the smells the developer selected —
    RDP plans for every smell it is given.
    """
    payload = _request("POST", GENERATE_PATH, body=report, timeout=timeout)

    if isinstance(payload, dict) and payload.get("error"):
        raise RDPError(f"RDP agent could not plan: {payload['error']}", status=422)

    plan = payload.get("plan") if isinstance(payload, dict) else None
    if not isinstance(plan, dict):
        raise RDPError("RDP agent returned no plan for this report.", status=502)

    # A circular dependency between steps comes back as HTTP 200 with the
    # error nested inside the plan, so it has to be checked separately.
    if plan.get("error"):
        raise RDPError(f"RDP agent could not plan: {plan['error']}", status=422)

    return {"plan": plan, "trace": payload.get("trace") or {}}


def probe_rdp(timeout: int = 5) -> dict:
    """Cheap reachability check. Never raises.

    The RDP agent exposes no /health route — only "/" and "/generate" — so
    reachability is judged by whether "/" answers at all.
    """
    status = {"reachable": False, "rdp_url": rdp_base_url(), "message": ""}

    try:
        req = urllib.request.Request(f"{rdp_base_url()}{INDEX_PATH}", method="GET")
        with urllib.request.urlopen(req, timeout=timeout):
            pass
        status.update(reachable=True, message="RDP agent is running.")
    except urllib.error.HTTPError as exc:
        # It answered, so the service is up even if the route is not 200.
        status.update(reachable=True, message=f"RDP agent responded HTTP {exc.code}.")
    except Exception as exc:  # URLError, timeout, anything else
        status["message"] = f"RDP agent is not reachable at {rdp_base_url()}: {exc}"

    return status
