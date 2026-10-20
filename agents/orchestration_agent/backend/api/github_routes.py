"""
GitHub routes
=============
R26-SE-008 | Bandara S M Y M | IT22277886

    POST /api/github/webhook                      GitHub App webhook receiver
    GET  /api/github/status                       whether the integration is on and configured
    GET  /api/github/workflows/<id>               PR context + stored CUQA report (sign-in)
    POST /api/github/workflows/<id>/sources       analysed files at the PR commit (sign-in)

The webhook does the minimum a webhook should, in this order, and returns:

    1. integration enabled and a webhook secret configured     else 503
    2. X-Hub-Signature-256 is a valid HMAC of the RAW body     else 401
    3. event and delivery headers present, body is JSON        else 400
    4. pull_request opened / synchronize / reopened            else 200 ignored
    5. payload carries everything a job needs                  else 422
    6. job durably stored (duplicate delivery -> 200)          else 503
    7. 202 Accepted

No GitHub API call, CUQA call or git operation happens inside the request:
GitHub times a webhook out after ten seconds, and the work belongs to
github_worker.py. A 202 is only returned once the job row is committed, so
GitHub never sees success for an event DIWO could lose.

Logs carry the delivery id, repository, PR number and a short SHA — never the
payload, the signature, the secret or any token.
"""

import hashlib
import hmac
import json
import logging
import re

from flask import Blueprint, jsonify, request

import config
from db import github_job_repository as jobs

logger = logging.getLogger("diwo.github.webhook")

github_bp = Blueprint("github", __name__)

#: Pull-request actions that put a new commit (or a reopened PR) in front of
#: DIWO. Everything else — labels, review requests, edits — is acknowledged
#: and ignored.
SUPPORTED_PR_ACTIONS = ("opened", "synchronize", "reopened")

#: GitHub caps webhook payloads at 25 MB.
MAX_WEBHOOK_BYTES = 25 * 1024 * 1024

_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
_DELIVERY_RE = re.compile(r"^[A-Za-z0-9-]{1,100}$")


def is_valid_signature(body: bytes, signature: str, secret: str) -> bool:
    """Constant-time check of GitHub's `sha256=<hex>` HMAC over the raw body."""
    if not secret or not signature or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _json_error(message: str, status: int):
    return jsonify({"error": message}), status


def _obj(value) -> dict:
    """`value` if it is a JSON object, else an empty one — payload fields are untrusted."""
    return value if isinstance(value, dict) else {}


def _positive_int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def extract_pr_event(payload: dict) -> tuple[dict | None, str | None]:
    """The job fields from a pull_request payload, or (None, reason)."""
    repo = payload.get("repository")
    pr = payload.get("pull_request")
    if not isinstance(repo, dict) or not isinstance(pr, dict):
        return None, "payload has no repository or pull_request object"

    installation = _obj(payload.get("installation"))
    installation_id = _positive_int(installation.get("id"))
    if installation_id is None:
        return None, "payload has no installation id - is this delivery from the GitHub App?"

    owner = _obj(repo.get("owner")).get("login")
    name = repo.get("name")
    full_name = repo.get("full_name")
    if not (isinstance(owner, str) and _NAME_RE.match(owner)
            and isinstance(name, str) and _NAME_RE.match(name)
            and full_name == f"{owner}/{name}"):
        return None, "repository owner/name are missing or invalid"

    number = _positive_int(pr.get("number"))
    if number is None:
        return None, "pull_request.number is missing or invalid"

    head = _obj(pr.get("head"))
    sha = head.get("sha")
    if not (isinstance(sha, str) and _SHA_RE.match(sha)):
        return None, "pull_request.head.sha is missing or invalid"

    # head.repo is null when the fork it came from has been deleted.
    head_full_name = _obj(head.get("repo")).get("full_name")

    return {
        "installation_id": installation_id,
        "repo_owner": owner,
        "repo_name": name,
        "repo_full_name": full_name,
        "pr_number": number,
        "head_sha": sha,
        "head_repo_full_name": head_full_name if isinstance(head_full_name, str) else None,
        "is_fork": head_full_name != full_name,
    }, None


@github_bp.route("/github/webhook", methods=["POST"])
def github_webhook():
    if not config.github_integration_enabled():
        return _json_error("GitHub integration is disabled.", 503)

    secret = config.github_webhook_secret()
    if not secret:
        logger.error("Webhook rejected: GITHUB_WEBHOOK_SECRET is not configured.")
        return _json_error("GitHub integration is not configured.", 503)

    if (request.content_length or 0) > MAX_WEBHOOK_BYTES:
        return _json_error("Payload too large.", 413)

    raw_body = request.get_data(cache=False)
    if len(raw_body) > MAX_WEBHOOK_BYTES:
        return _json_error("Payload too large.", 413)

    if not is_valid_signature(raw_body, request.headers.get("X-Hub-Signature-256", ""), secret):
        logger.warning("Webhook rejected: missing or invalid signature (delivery=%s).",
                       _safe_delivery(request.headers.get("X-GitHub-Delivery")))
        return _json_error("Invalid signature.", 401)

    event = request.headers.get("X-GitHub-Event", "")
    delivery_id = request.headers.get("X-GitHub-Delivery", "")
    if not event or not _DELIVERY_RE.match(delivery_id):
        return _json_error("Missing X-GitHub-Event or X-GitHub-Delivery header.", 400)

    try:
        payload = json.loads(raw_body)
    except (ValueError, UnicodeDecodeError):
        return _json_error(
            "Malformed JSON. Set the GitHub App webhook content type to application/json.", 400)
    if not isinstance(payload, dict):
        return _json_error("Malformed JSON: expected an object.", 400)

    if event == "ping":
        return jsonify({"pong": True, "delivery_id": delivery_id}), 200

    if event in ("issue_comment", "check_run"):
        return _accept_command(event, payload, delivery_id)

    action = payload.get("action")
    if event != "pull_request" or action not in SUPPORTED_PR_ACTIONS:
        return jsonify({"ignored": True, "event": event,
                        "action": action if isinstance(action, str) else None}), 200

    fields, problem = extract_pr_event(payload)
    if fields is None:
        logger.warning("Webhook rejected (delivery=%s): %s", delivery_id, problem)
        return _json_error(f"Invalid pull_request payload: {problem}", 422)

    fields.update(delivery_id=delivery_id, event=event, action=action)
    try:
        job, created = jobs.enqueue_pr_job(fields)
    except Exception:  # noqa: BLE001 - any storage failure means "not accepted"
        logger.exception("Webhook could not be persisted (delivery=%s).", delivery_id)
        return _json_error("Event could not be stored; GitHub may redeliver it.", 503)

    if not created:
        logger.info("Duplicate delivery ignored: delivery=%s job=%s", delivery_id, job["id"])
        return jsonify({"duplicate": True, "job_id": job["id"],
                        "status": job["status"]}), 200

    logger.info("Accepted pull_request.%s: delivery=%s repo=%s pr=#%s sha=%s job=%s",
                action, delivery_id, fields["repo_full_name"], fields["pr_number"],
                fields["head_sha"][:7], job["id"])
    return jsonify({"accepted": True, "job_id": job["id"]}), 202


def _repo_context(payload: dict) -> tuple[dict | None, str | None]:
    """installation + repository fields shared by every event, validated."""
    repo = _obj(payload.get("repository"))
    installation_id = _positive_int(_obj(payload.get("installation")).get("id"))
    if installation_id is None:
        return None, "payload has no installation id - is this delivery from the GitHub App?"
    owner = _obj(repo.get("owner")).get("login")
    name = repo.get("name")
    if not (isinstance(owner, str) and _NAME_RE.match(owner) and isinstance(name, str)
            and _NAME_RE.match(name) and repo.get("full_name") == f"{owner}/{name}"):
        return None, "repository owner/name are missing or invalid"
    return {"installation_id": installation_id, "repo_owner": owner, "repo_name": name,
            "repo_full_name": f"{owner}/{name}"}, None


def _ignored(event, reason):
    return jsonify({"ignored": True, "event": event, "reason": reason}), 200


def _accept_command(event: str, payload: dict, delivery_id: str):
    """Queue a /refactoriq PR comment or a Check Run button press.

    The actor is taken from the SIGNED event's `sender`, never from the
    comment text, and bot senders are ignored so DIWO's own comments can never
    trigger it. Authorization, PR state and the command itself are checked by
    the worker (services/github_command_service.py), which may call GitHub.
    """
    from services.github_command_parser import command_for_action, parse_command

    if not config.github_commands_enabled():
        return _ignored(event, "commands are disabled")

    sender = _obj(payload.get("sender"))
    if sender.get("type") == "Bot" or str(sender.get("login", "")).endswith("[bot]"):
        return _ignored(event, "bot sender")
    login, user_id = sender.get("login"), _positive_int(sender.get("id"))
    if not (isinstance(login, str) and _NAME_RE.match(login) and user_id):
        return _json_error("Invalid payload: sender is missing or invalid.", 422)

    context, problem = _repo_context(payload)
    if context is None:
        return _json_error(f"Invalid {event} payload: {problem}", 422)

    if event == "issue_comment":
        if payload.get("action") != "created":
            return _ignored(event, "only newly created comments are commands")
        issue = _obj(payload.get("issue"))
        if not isinstance(issue.get("pull_request"), dict):
            return _ignored(event, "comment is not on a pull request")
        number = _positive_int(issue.get("number"))
        comment = _obj(payload.get("comment"))
        if _obj(comment.get("performed_via_github_app")).get("id"):
            return _ignored(event, "comment was made by an app")
        command = parse_command(comment.get("body"))
        if command is None or number is None:
            return _ignored(event, "no /refactoriq command")
        command.update(source="comment", comment_id=_positive_int(comment.get("id")))
    else:
        if payload.get("action") != "requested_action":
            return _ignored(event, "only requested actions are handled")
        check_run = _obj(payload.get("check_run"))
        if str(_obj(check_run.get("app")).get("id")) != config.github_app_id():
            return _ignored(event, "check run belongs to another app")
        prs_list = check_run.get("pull_requests") or []
        number = _positive_int(_obj(prs_list[0]).get("number")) if prs_list else None
        command = command_for_action(_obj(payload.get("requested_action")).get("identifier"))
        if command is None or number is None:
            return _ignored(event, "unknown action or no pull request")
        command.update(source="check_run", check_run_id=_positive_int(check_run.get("id")),
                       head_sha=check_run.get("head_sha"))

    command.update(actor_login=login, actor_id=user_id)
    context["pr_number"] = number
    try:
        job, created = jobs.enqueue_task(event, delivery_id, context, command)
    except Exception:  # noqa: BLE001 - any storage failure means "not accepted"
        logger.exception("Command could not be persisted (delivery=%s).", delivery_id)
        return _json_error("Event could not be stored; GitHub may redeliver it.", 503)
    if not created:
        return jsonify({"duplicate": True, "job_id": job["id"], "status": job["status"]}), 200
    logger.info("Accepted %s command %r: delivery=%s repo=%s pr=#%s by=%s job=%s", event,
                command["name"], delivery_id, context["repo_full_name"], number, login, job["id"])
    return jsonify({"accepted": True, "job_id": job["id"], "command": command["name"]}), 202


@github_bp.route("/github/status", methods=["GET"])
def github_status():
    """Presence booleans and setting NAMES only — safe to call from anywhere."""
    enabled = config.github_integration_enabled()
    problems = config.github_config_problems() if enabled else []
    return jsonify({
        "enabled": enabled,
        "configured": enabled and not problems,
        "problems": problems,
        "autonomy_level": config.github_autonomy_level(),
        "check_name": config.GITHUB_CHECK_NAME,
        "webhook_path": f"{config.API_PREFIX}/github/webhook",
    })


def _safe_delivery(value):
    return value if isinstance(value, str) and _DELIVERY_RE.match(value) else "-"


# ─────────────────────────────────────────────────────────────────────────────
# GitHub-linked workflows (Milestones 2-3)
#
# Both routes carry <wf_id>, so services/access_service.py has already required
# a signed-in user before they run. A workflow that is not GitHub-linked is a
# 404 here — these routes describe the link, not the workflow in general.
# ─────────────────────────────────────────────────────────────────────────────

MAX_SOURCE_FILES_PER_REQUEST = 20


def _linked_workflow(wf_id):
    from db import github_pr_workflow_repository as prs
    from db.workflow_repository import get_workflow

    mapping = prs.get_mapping_for_workflow(wf_id)
    workflow = get_workflow(wf_id) if mapping else None
    return mapping, workflow


@github_bp.route("/github/workflows/<wf_id>", methods=["GET"])
def github_workflow(wf_id):
    """The PR a workflow came from, plus the stored CUQA report it was seeded with."""
    from db import github_pr_workflow_repository as prs
    from db.workflow_repository import parse_json_field
    from services.access_service import current_github_context
    from services.github_report_service import display_ids
    from services.github_review_service import review_capability

    mapping, workflow = _linked_workflow(wf_id)
    if not mapping or not workflow:
        return _json_error("No GitHub-linked workflow with this id.", 404)

    latest = prs.latest_mapping_for_pr(mapping["repository_full_name"], mapping["pr_number"])
    identity, _ = current_github_context()
    repo_url = f"https://github.com/{mapping['repository_full_name']}"
    return jsonify({
        "workflow_id": wf_id,
        "status": workflow["status"],
        "target": workflow["target"],
        "language": workflow["language"],
        "smells": parse_json_field(workflow, "smells_json") or [],
        "report": parse_json_field(workflow, "cuqa_report_json"),
        "metrics_before": parse_json_field(workflow, "metrics_before_json"),
        "github": {
            "repository": mapping["repository_full_name"],
            "pr_number": mapping["pr_number"],
            "head_sha": mapping["head_sha"],
            "analysis_status": mapping["analysis_status"],
            "smell_count": mapping.get("smell_count"),
            "files_analyzed": mapping.get("files_analyzed"),
            # False once a newer commit of the PR has been analysed or queued:
            # the UI then says the findings describe an older commit.
            "is_latest_commit": bool(latest) and latest["head_sha"] == mapping["head_sha"],
            "pull_request_url": f"{repo_url}/pull/{mapping['pr_number']}",
            "commit_url": f"{repo_url}/commit/{mapping['head_sha']}",
            # Milestone 4: where the developer review stands, and whether THIS
            # user may decide on it (repository grant + current commit).
            "review_status": mapping.get("review_status"),
            "reviewed_by": mapping.get("reviewed_by"),
            "reviewed_at": mapping.get("reviewed_at"),
            **review_capability(identity, mapping),
        },
        "plan": parse_json_field(workflow, "plan_json"),
        "plan_full": parse_json_field(workflow, "plan_full_json"),
        # State shared with the /refactoriq commands: what is selected and
        # decided on GitHub shows up here, and S-ids name the same findings.
        "smell_ids": display_ids(parse_json_field(workflow, "smells_json") or []),
        "draft_selection": prs.get_draft(mapping, "draft_selection_json", None),
        "draft_plan_decisions": prs.get_draft(mapping, "draft_plan_decisions_json", {}),
        "transformation_enabled": config.github_pr_transformation_enabled(),
        "writeback_enabled": config.github_pr_writeback_enabled(),
    })


@github_bp.route("/github/workflows/<wf_id>/sources", methods=["POST"])
def github_workflow_sources(wf_id):
    """Source of analysed files, read from GitHub at the analysed commit.

    Same response shape as POST /api/workspace/sources. Only files that are in
    the workflow's CUQA report can be read — this is a viewer for the findings,
    not a general repository proxy.
    """
    from db.workflow_repository import parse_json_field
    from services.github_app_service import GitHubAPIError, GitHubConfigError
    from services.github_review_service import fetch_commit_sources

    mapping, workflow = _linked_workflow(wf_id)
    if not mapping or not workflow:
        return _json_error("No GitHub-linked workflow with this id.", 404)
    if not config.github_integration_enabled():
        return _json_error("GitHub integration is disabled.", 503)

    body = request.get_json(silent=True) or {}
    paths = body.get("file_paths")
    if not isinstance(paths, list) or not paths or not all(isinstance(p, str) for p in paths):
        return _json_error("'file_paths' must be a non-empty list of strings.", 400)
    if len(paths) > MAX_SOURCE_FILES_PER_REQUEST:
        return _json_error(f"At most {MAX_SOURCE_FILES_PER_REQUEST} files per request.", 400)

    report = parse_json_field(workflow, "cuqa_report_json") or {}
    try:
        files, missing = fetch_commit_sources(mapping, paths, report)
    except GitHubConfigError:
        return _json_error("GitHub integration is not configured.", 503)
    except GitHubAPIError as exc:
        return _json_error(f"GitHub could not return the source: {exc.message}", 502)

    return jsonify({"files": files, "missing": missing, "imported": len(files),
                    "total": len(paths), "head_sha": mapping["head_sha"]})


# ─────────────────────────────────────────────────────────────────────────────
# Proposed changes of GitHub-linked workflows (DIWO side of the final review)
#
# The same records and rules the /refactoriq commands use
# (services/github_transformation_service.py, github_writeback_service.py), so
# a decision made here shows on GitHub and vice versa. All routes carry <wf_id>
# and therefore require sign-in; deciding and submitting also require a
# repository grant and a current commit (REVIEW_ENDPOINTS).
# ─────────────────────────────────────────────────────────────────────────────

def _transformation_view(record: dict) -> dict:
    return {
        "id": record["display_id"], "file": record["file_path"], "status": record["status"],
        "status_reason": record.get("status_reason"),
        "validated": record["validation_passed"], "coupled": record["coupled"],
        "refactorings": record.get("refactorings_json") or [],
        "validation": record.get("validation_json") or {},
        "confidence": record.get("confidence"),
        "original": record.get("original_content"), "refactored": record.get("refactored_content"),
        "diff": record.get("diff"), "head_sha": record["head_sha"],
        "decided_by": record.get("decided_by"), "decided_at": record.get("decided_at"),
        "applied_commit_sha": record.get("applied_commit_sha"),
        "applied_at": record.get("applied_at"),
    }


@github_bp.route("/github/workflows/<wf_id>/transformations", methods=["GET"])
def github_workflow_transformations(wf_id):
    from db import github_review_repository as reviews
    from db.workflow_repository import parse_json_field

    mapping, workflow = _linked_workflow(wf_id)
    if not mapping or not workflow:
        return _json_error("No GitHub-linked workflow with this id.", 404)
    writeback = reviews.latest_writeback(wf_id)
    return jsonify({
        "workflow_id": wf_id,
        "status": workflow["status"],
        "summary": parse_json_field(workflow, "transformation_result_json"),
        "transformations": [_transformation_view(r) for r in reviews.list_transformations(wf_id)],
        "writeback": writeback and {k: writeback.get(k) for k in (
            "id", "status", "commit_sha", "branch", "error", "requested_by", "committed_at",
            "transformation_ids_json")},
        "transformation_enabled": config.github_pr_transformation_enabled(),
        "writeback_enabled": config.github_pr_writeback_enabled(),
    })


@github_bp.route("/github/workflows/<wf_id>/transformations/decision", methods=["POST"])
def github_transformation_decision(wf_id):
    """Body: {"decisions": {"T001": "accept", "T002": "reject"}}"""
    from services.access_service import current_github_context
    from services.github_review_service import actor_name
    from services.github_transformation_service import DecisionError, decide

    mapping, workflow = _linked_workflow(wf_id)
    if not mapping or not workflow:
        return _json_error("No GitHub-linked workflow with this id.", 404)
    decisions = (request.get_json(silent=True) or {}).get("decisions")
    if not isinstance(decisions, dict) or not decisions:
        return _json_error("'decisions' must be an object such as {\"T001\": \"accept\"}.", 400)
    identity, _ = current_github_context()
    try:
        outcome = decide(wf_id, decisions, actor_name(identity))
    except DecisionError as exc:
        return _json_error(exc.message, exc.status)
    _publish_transformations_quietly(mapping, wf_id)
    return jsonify(outcome)


@github_bp.route("/github/workflows/<wf_id>/submit-refactoring", methods=["POST"])
def github_submit_refactoring(wf_id):
    """Body: {"confirm": true}. Queues the commit of the ACCEPTED changes."""
    from services.access_service import current_github_context
    from services.github_writeback_service import WritebackRefused, request_writeback

    mapping, workflow = _linked_workflow(wf_id)
    if not mapping or not workflow:
        return _json_error("No GitHub-linked workflow with this id.", 404)
    if (request.get_json(silent=True) or {}).get("confirm") is not True:
        return _json_error("Submitting changes to the pull request needs an explicit "
                           "confirmation: {\"confirm\": true}.", 400)
    identity, _ = current_github_context()
    try:
        result = request_writeback(mapping, wf_id, identity)
    except WritebackRefused as exc:
        return _json_error(exc.message, exc.status)
    return jsonify(result), (202 if result["status"] == "queued" else 200)


@github_bp.route("/github/workflows/<wf_id>/patch", methods=["GET"])
def github_workflow_patch(wf_id):
    """The accepted changes as a unified diff — the fallback when write-back
    is disabled or impossible (fork, protected branch, no permission)."""
    from flask import Response
    from services.github_writeback_service import build_patch

    mapping, workflow = _linked_workflow(wf_id)
    if not mapping or not workflow:
        return _json_error("No GitHub-linked workflow with this id.", 404)
    patch = build_patch(wf_id)
    if not patch:
        return _json_error("No accepted change to export.", 404)
    return Response(patch, mimetype="text/x-diff", headers={
        "Content-Disposition": f"attachment; filename=refactoriq-{wf_id}.patch"})


def _publish_transformations_quietly(mapping, wf_id):
    if not config.github_integration_enabled():
        return
    try:
        from services.github_job_service import shared_app_service
        from services.github_transformation_service import publish_transformations
        publish_transformations(shared_app_service(), mapping, wf_id)
    except Exception:  # noqa: BLE001 - the decision is saved; GitHub catches up later
        logger.warning("Transformation report not republished for %s", wf_id)
