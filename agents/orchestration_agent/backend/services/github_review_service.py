"""
Developer review of GitHub-linked workflows (Milestone 4)
=========================================================
R26-SE-008 | Bandara S M Y M | IT22277886

What changes when a DIWO workflow came from a pull request:

  WHO MAY DECIDE. Viewing needs a signed-in user (services/access_service.py).
  Deciding — selecting smells, approving or rejecting a plan — additionally
  needs a grant for THAT repository: an explicit reviewer entry, or a GitHub
  login with write/maintain/admin permission, checked live through the App.

  ON WHICH COMMIT. A decision is only accepted while the analysed commit is
  still the PR head. Once the developer pushes again the findings describe old
  code, and approving a plan for them would approve the wrong thing.

  WITH WHICH SOURCE. Nothing downstream may read CUQA's shared workspace,
  which holds whatever repository someone last loaded. RDP gets the selected
  files' source read from GitHub at the analysed commit; the stages that read
  the workspace (transformation, archive) are refused until Milestone 5 gives
  them a commit-pinned source.

  WHAT GITHUB SEES. The commit's Check Run is updated as the review moves:
  plan ready → approved / rejected. It never says anything was applied.
"""

import base64
import logging
import threading
import time
from urllib.parse import quote

import config
from db import github_job_repository as jobs
from db import github_pr_workflow_repository as prs
from services.access_service import AccessError
from services.github_app_service import GitHubAPIError, GitHubConfigError

logger = logging.getLogger("diwo.github.review")

#: Workflow endpoints that record a developer decision. They need repository
#: authorization and a current commit, on top of sign-in.
REVIEW_ENDPOINTS = frozenset({
    "workflow.select_smells", "workflow.plan_decision", "workflow.plan_preference_update",
    "workflow.reset_to_smell_review", "workflow.reset_to_plan_approval",
    "workflow.save_updated_report", "workflow.complete_workflow",
    "github.github_transformation_decision", "github.github_submit_refactoring",
})

#: Endpoints that read CUQA's shared workspace or transform code. Refused for
#: GitHub-linked workflows until they can work from the analysed commit.
BLOCKED_ENDPOINTS = frozenset({
    "workflow.transform", "workflow.transformation_decision",
    "workflow.download_refactored_archive",
})

WRITE_PERMISSIONS = ("admin", "maintain", "write")

#: Source attached to an RDP request: per file, and in total.
MAX_SOURCE_FILES = 20
MAX_SOURCE_BYTES = 2 * 1024 * 1024

_PERMISSION_TTL = 300
_permission_cache: dict = {}
_permission_lock = threading.Lock()


# ─── Repository authorization ───────────────────────────────────────────────

def _app_service():
    from services.github_job_service import shared_app_service
    return shared_app_service()


def _collaborator_permission(mapping: dict, login: str) -> str:
    """The login's permission on the repository: admin/maintain/write/triage/read/none."""
    key = (mapping["repository_full_name"].lower(), login.lower())
    now = time.time()
    with _permission_lock:
        hit = _permission_cache.get(key)
        if hit and hit[1] > now:
            return hit[0]

    if not config.github_integration_enabled():
        raise AccessError("GitHub integration is disabled, so repository permission "
                          "cannot be verified.", 503)
    try:
        payload = _app_service().request(
            "GET", f"repos/{mapping['repository_owner']}/{mapping['repository_name']}"
                   f"/collaborators/{quote(login, safe='')}/permission",
            mapping["installation_id"], expected=(200,), what="Repository permission check") or {}
        permission = (payload.get("role_name") or payload.get("permission") or "none").lower()
    except GitHubAPIError as exc:
        if exc.status == 404:
            permission = "none"
        else:
            raise AccessError("Could not verify your repository permission with GitHub. "
                              "Try again.", 503) from None
    except GitHubConfigError:
        raise AccessError("GitHub integration is not configured, so repository permission "
                          "cannot be verified.", 503) from None

    with _permission_lock:
        _permission_cache[key] = (permission, now + _PERMISSION_TTL)
    return permission


def authorize_repository(identity: dict, mapping: dict) -> str:
    """How this user is authorized to decide on the repository, or AccessError (403).

    Returns "reviewer_list", "github:<permission>" or "unenforced".
    """
    repo = mapping["repository_full_name"]
    email = (identity.get("email") or "").lower()
    reviewers = config.github_repo_reviewers()
    grant = reviewers.get(repo.lower()) or reviewers.get(f"{mapping['repository_owner'].lower()}/*")

    if grant and email in grant:
        return "reviewer_list"

    login = config.github_user_logins().get(email)
    if login:
        permission = _collaborator_permission(mapping, login)
        if permission in WRITE_PERMISSIONS:
            return f"github:{permission}"
        raise AccessError(
            f"Your GitHub account ({login}) has '{permission}' access to {repo}; approving "
            "refactoring decisions requires write, maintain or admin.", 403)

    if grant:
        raise AccessError(f"You are not a configured reviewer for {repo}.", 403)
    if not config.github_require_repo_authorization():
        return "unenforced"
    raise AccessError(
        f"No reviewers are configured for {repo}. Ask an administrator to add you to "
        "GITHUB_REPO_REVIEWERS or to map your account in GITHUB_USER_LOGINS.", 403)


def reset_permission_cache() -> None:
    with _permission_lock:
        _permission_cache.clear()


# ─── Current commit ─────────────────────────────────────────────────────────

def ensure_current_commit(mapping: dict) -> None:
    """AccessError (409) when the analysed commit is no longer the PR head.

    Three signals, cheapest first: a newer analysed commit, a newer delivered
    event, and — when the integration is configured — the PR as GitHub sees
    it right now (which also catches a closed PR).
    """
    sha = mapping["head_sha"]
    stale = ("A newer commit has been pushed to this pull request, so these findings are "
             "outdated. Open the latest RefactorIQ / DIWO check on GitHub to review the "
             "current code.")

    latest = prs.latest_mapping_for_pr(mapping["repository_full_name"], mapping["pr_number"])
    if latest and latest["head_sha"] != sha:
        raise AccessError(stale, 409)
    delivered = jobs.latest_head_sha_for_pr(mapping["repository_full_name"], mapping["pr_number"])
    if delivered and delivered != sha:
        raise AccessError(stale, 409)

    if not config.github_integration_enabled():
        return
    from services.github_pr_pipeline import fetch_pr_head
    try:
        head = fetch_pr_head(_app_service(), {
            "repo_owner": mapping["repository_owner"], "repo_name": mapping["repository_name"],
            "pr_number": mapping["pr_number"], "installation_id": mapping["installation_id"]})
    except (GitHubAPIError, GitHubConfigError):
        raise AccessError("Could not confirm the pull request's current commit with GitHub. "
                          "Try again.", 503) from None
    if head.get("state") and head["state"] != "open":
        raise AccessError("This pull request is no longer open.", 409)
    if head.get("head_sha") and head["head_sha"] != sha:
        raise AccessError(stale, 409)


# ─── Request guard (called from services/access_service.py) ─────────────────

def guard_review_request(endpoint: str | None, identity: dict, mapping: dict) -> None:
    """Apply the Milestone 4 rules to one request on a GitHub-linked workflow."""
    if endpoint in BLOCKED_ENDPOINTS:
        raise AccessError(
            "Transformation is not enabled for workflows created from GitHub pull requests "
            "yet: it would read CUQA's shared workspace instead of the analysed commit. "
            "The approved plan is recorded and shown on the pull request.", 409)
    if endpoint in REVIEW_ENDPOINTS:
        authorize_repository(identity, mapping)
        ensure_current_commit(mapping)


def review_capability(identity: dict | None, mapping: dict) -> dict:
    """{can_review, reason, authorization} for the UI — never raises."""
    if identity is None:
        return {"can_review": False, "reason": "Sign in to review.", "authorization": None}
    try:
        how = authorize_repository(identity, mapping)
        ensure_current_commit(mapping)
        return {"can_review": True, "reason": None, "authorization": how}
    except AccessError as exc:
        return {"can_review": False, "reason": exc.message, "authorization": None}


# ─── Source at the analysed commit ──────────────────────────────────────────

def fetch_commit_sources(mapping: dict, paths: list, report: dict) -> tuple[list, list]:
    """(files, missing): source of analysed files, read from GitHub at the commit.

    Only paths that are in the workflow's CUQA report are read — this serves
    the findings, it is not a general repository proxy. Raises GitHubAPIError
    for failures other than "not found"; GitHubConfigError when unconfigured.
    """
    analysed = {(f.get("relative_path") or "").replace("\\", "/"): f
                for f in (report or {}).get("files") or []}
    app = _app_service()
    files, missing = [], []
    for path in paths:
        entry = analysed.get(str(path).replace("\\", "/"))
        if entry is None:
            missing.append(path)
            continue
        try:
            payload = app.request(
                "GET",
                f"repos/{mapping['repository_owner']}/{mapping['repository_name']}/contents/"
                f"{quote(path, safe='/')}?ref={mapping['head_sha']}",
                mapping["installation_id"], expected=(200,), what="Source file read") or {}
        except GitHubAPIError as exc:
            if exc.status == 404:
                missing.append(path)
                continue
            raise
        if payload.get("encoding") != "base64" or not isinstance(payload.get("content"), str):
            missing.append(path)            # >1 MB files come back without content
            continue
        files.append({
            "file_name": path,
            "file_path": path,
            "source_code": base64.b64decode(payload["content"]).decode("utf-8", errors="replace"),
            "language": entry.get("language") or "",
            "source_mode": "github_commit",
            # The git blob id of this exact original — write-back checks the
            # branch still holds it before replacing it.
            "blob_sha": payload.get("sha"),
        })
    return files, missing


def rdp_source_files(mapping: dict, selected: list, report: dict) -> tuple[list | None, str | None]:
    """Source for the files with selected smells, for the RDP request.

    Returns (source_files, warning). Best effort: a failure means RDP plans
    without source (as it always has for ordinary workflows), and the warning
    says so — it never falls back to CUQA's workspace.
    """
    if not config.github_rdp_attach_sources() or not config.github_integration_enabled():
        return None, None
    paths = []
    for smell in selected:
        path = (smell.get("location") or {}).get("file") or smell.get("relative_path")
        if path and path not in paths:
            paths.append(path)
    paths = paths[:MAX_SOURCE_FILES]
    try:
        files, missing = fetch_commit_sources(mapping, paths, report)
    except (GitHubAPIError, GitHubConfigError) as exc:
        return None, ("Source at the analysed commit could not be read from GitHub "
                      f"({getattr(exc, 'message', exc)}); RDP planned from the report only.")
    kept, total = [], 0
    for entry in files:
        total += len(entry["source_code"].encode("utf-8"))
        if total > MAX_SOURCE_BYTES:
            break
        kept.append(entry)
    warning = None
    if missing or len(kept) < len(files):
        warning = (f"{len(kept)} of {len(paths)} selected file(s) were sent to RDP with their "
                   "source; the rest were planned from the report only.")
    return kept, warning


# ─── Check Run updates ──────────────────────────────────────────────────────

def publish_review_status(workflow_id: str, review_status: str, identity: dict | None,
                          plan: dict | None = None, details: dict | None = None) -> bool:
    """Record the review state and show it on the commit's Check Run.

    Best effort: GitHub being unavailable never fails the developer's request.
    Returns True when the Check Run was updated.
    """
    from services.github_check_service import (build_review_output, publish_result,
                                               workflow_details_url)

    mapping = prs.get_mapping_for_workflow(workflow_id)
    if not mapping:
        return False
    actor = actor_name(identity)
    try:
        prs.update_mapping(mapping["id"], review_status=review_status, reviewed_by=actor,
                           reviewed_at=jobs.stamp())
    except Exception:  # noqa: BLE001
        logger.warning("Could not record review status for %s", workflow_id)

    if not mapping.get("check_run_id") or not config.github_integration_enabled():
        return False
    details_url = workflow_details_url(workflow_id)
    job = {"repo_owner": mapping["repository_owner"], "repo_name": mapping["repository_name"],
           "repo_full_name": mapping["repository_full_name"], "pr_number": mapping["pr_number"],
           "head_sha": mapping["head_sha"], "installation_id": mapping["installation_id"]}
    details = dict(details or {})
    details.setdefault("draft_decisions", prs.get_draft(mapping, "draft_plan_decisions_json", {}))
    output, conclusion = build_review_output(job, review_status, plan, actor, details,
                                             workflow_id, details_url)
    try:
        publish_result(_app_service(), job, mapping["check_run_id"], conclusion=conclusion,
                       output=output, details_url=details_url)
        return True
    except (GitHubAPIError, GitHubConfigError) as exc:
        logger.warning("Check Run %s not updated (%s): %s", mapping["check_run_id"],
                       review_status, getattr(exc, "message", type(exc).__name__))
        return False


# ─── Shared review actions (DIWO routes and GitHub commands) ────────────────
#
# One implementation of "select these smells and plan" and "decide on this
# plan", so a decision made on GitHub and one made in DIWO go through the same
# business rules and land in the same records.

def actor_name(identity: dict | None) -> str | None:
    identity = identity or {}
    if identity.get("email"):
        return identity["email"]
    if identity.get("github_login"):
        return f"github:{identity['github_login']}"
    return None


def authorize_github_login(mapping: dict, login: str) -> str:
    """A GitHub command sender's write access, checked live (AccessError 403)."""
    permission = _collaborator_permission(mapping, login)
    if permission not in WRITE_PERMISSIONS:
        raise AccessError(
            f"@{login} has '{permission}' access to {mapping['repository_full_name']}; "
            "RefactorIQ decisions require write, maintain or admin.", 403)
    return f"github:{permission}"


def plan_for_selection(wf: dict, mapping: dict, identity: dict | None, selected_ids: list,
                       selected_files=None, selection_mode: str = "smell",
                       feedback: dict | None = None) -> dict:
    """Commit a smell selection for a GitHub-linked workflow and plan it with RDP.

    Exactly the DIWO stage logic (commit_smell_selection), with RDP given the
    selected files' source from the analysed commit, the shared draft
    selection kept in step, and the Check Run updated.
    """
    from db.workflow_repository import parse_json_field
    from services.workflow_service import commit_smell_selection

    report = parse_json_field(wf, "cuqa_report_json") or {}
    payload = commit_smell_selection(
        wf, selected_ids, selected_files or [], selection_mode, feedback or {},
        wf["status"] == "plan_approval",
        source_provider=lambda selected: rdp_source_files(mapping, selected, report))
    prs.set_draft(mapping["id"], "draft_selection_json", list(payload.get("selected_ids") or []))
    prs.set_draft(mapping["id"], "draft_plan_decisions_json", {})
    payload["github_check_updated"] = publish_review_status(
        wf["id"], "plan_ready", identity, plan=payload.get("plan"),
        details={"selected_count": payload.get("selected_count"),
                 "excluded_count": payload.get("excluded_count"),
                 "plan_source": payload.get("plan_source"),
                 "plan_warning": payload.get("plan_warning"),
                 "draft_decisions": {}})
    return payload


def decide_plan(wf: dict, mapping: dict, identity: dict | None, decision: str,
                data: dict) -> dict:
    """Apply a plan decision to a GitHub-linked workflow (same rules as DIWO).

    On approve the workflow stops at plan_approved and — only when PR
    transformation is enabled — a durable SCTVA job is queued.
    """
    from db.workflow_repository import parse_json_field
    from services.workflow_service import apply_plan_decision

    result = apply_plan_decision(wf, decision, data, run_transformation=False)
    if isinstance(data.get("decisions"), dict):
        prs.set_draft(mapping["id"], "draft_plan_decisions_json",
                      {str(k): v for k, v in data["decisions"].items()})
    review_status = {"approve": "plan_approved", "reject": "plan_rejected",
                     "modify": "plan_ready"}[decision]
    result["github_check_updated"] = publish_review_status(
        wf["id"], review_status, identity,
        plan=result.get("approved_plan") or result.get("plan") or parse_json_field(wf, "plan_json"),
        details={"reason": (data.get("feedback") or {}).get("reason")})
    if decision == "approve":
        from services.github_transformation_service import queue_transformation
        result["transformation"] = queue_transformation(mapping, wf["id"], identity)
    return result
