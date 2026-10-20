"""
Safe write-back of accepted changes to the PR branch
====================================================
R26-SE-008 | Bandara S M Y M | IT22277886

The only place DIWO writes to a repository. It commits ONLY transformations a
developer explicitly accepted, that SCTVA validated, and only after an
explicit submit — and it re-checks every one of those facts immediately
before writing, in the worker, not when the request was made:

    1. write-back enabled; requester still has write access (checked live)
    2. PR still open, same repository (no forks), head == the analysed SHA
    3. each accepted record: still accepted, validated, and its refactored
       text hashes to what was reviewed; the branch still holds the exact
       original blob it was computed from
    4. Git Data API: blobs -> tree (base = analysed commit) -> commit whose
       only parent is the analysed commit
    5. PATCH the branch ref with force=false: a fast-forward only. If anyone
       pushed in the meantime GitHub refuses, and so does DIWO — the
       developer's commits are never overwritten. Branch protection is
       respected, not bypassed.

IDEMPOTENT. One write-back row per (analysed SHA, accepted set, contents).
The commit SHA is recorded before the ref moves, and the commit message
carries a trailer naming the write-back, so a retry after a timeout either
finishes moving the ref or recognises its own commit — never a second commit.

LOOP-SAFE. The resulting push triggers a normal re-analysis of the new
commit. Nothing after analysis runs without a human, so a bot commit can
never start another bot commit.

FALLBACK. Without write-back (disabled, fork, no permission, protected
branch) the accepted changes remain downloadable as a patch.
"""

import hashlib
import json
import logging

import config
from db import github_job_repository as jobs
from db import github_pr_workflow_repository as prs
from db import github_review_repository as reviews
from db.workflow_repository import log_event, update_workflow
from services.access_service import AccessError
from services.github_app_service import GitHubAPIError
from services.github_transformation_service import mapping_context, sha256_text

logger = logging.getLogger("diwo.github.writeback")

WRITEBACK_EVENT = "writeback"
TRAILER = "RefactorIQ-Writeback"


class WritebackRefused(Exception):
    """A precondition does not hold; nothing was written."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.message = message
        self.status = status


def _accepted(workflow_id: str) -> list:
    return [r for r in reviews.list_transformations(workflow_id) if r["status"] == reviews.ACCEPTED]


def _check_integrity(record: dict) -> None:
    if not record["validation_passed"]:
        raise WritebackRefused(f"{record['display_id']} is not validated.")
    if sha256_text(record.get("refactored_content")) != record.get("refactored_sha256"):
        raise WritebackRefused(f"{record['display_id']} no longer matches the reviewed change.")
    if sha256_text(record.get("original_content")) != record.get("original_sha256"):
        raise WritebackRefused(f"{record['display_id']} original does not match the analysed source.")


def request_writeback(mapping: dict, workflow_id: str, identity: dict | None) -> dict:
    """Queue the commit of the currently accepted changes. Idempotent."""
    if not config.github_pr_writeback_enabled():
        raise WritebackRefused(
            "Write-back to pull-request branches is disabled (GITHUB_PR_WRITEBACK_ENABLED=false). "
            "Download the accepted changes as a patch from DIWO instead.")
    accepted = _accepted(workflow_id)
    if not accepted:
        latest = reviews.latest_writeback(workflow_id)
        if latest and latest["status"] == reviews.WB_COMMITTED:
            # Already applied: answer with that commit rather than a second one.
            return {"status": "committed", "writeback_id": latest["id"],
                    "commit_sha": latest["commit_sha"], "duplicate": True}
        raise WritebackRefused("No change has been accepted, so there is nothing to apply.")
    for record in accepted:
        _check_integrity(record)

    ids = sorted(r["display_id"] for r in accepted)
    key = hashlib.sha256(json.dumps([mapping["head_sha"], ids,
                                     sorted(r["refactored_sha256"] for r in accepted)]).encode()
                         ).hexdigest()
    writeback, created = reviews.get_or_create_writeback({
        "workflow_id": workflow_id, "mapping_id": mapping["id"], "idempotency_key": key,
        "base_sha": mapping["head_sha"], "transformation_ids_json": ids,
        "requested_by": _actor(identity)})
    if writeback["status"] == reviews.WB_COMMITTED:
        return {"status": "committed", "writeback_id": writeback["id"],
                "commit_sha": writeback["commit_sha"], "duplicate": True}
    if not created and writeback["status"] in (reviews.WB_PENDING, reviews.WB_COMMITTING):
        return {"status": writeback["status"], "writeback_id": writeback["id"], "duplicate": True}
    if not created:                          # a refused/failed attempt being retried
        reviews.update_writeback(writeback["id"], status=reviews.WB_PENDING, error=None,
                                 requested_by=_actor(identity))
    job, _ = jobs.enqueue_task(
        WRITEBACK_EVENT, f"writeback:{writeback['id']}:{jobs.stamp()}", mapping_context(mapping),
        {"name": "writeback", "writeback_id": writeback["id"],
         "identity": {k: v for k, v in (identity or {}).items() if k in ("email", "github_login")}})
    log_event(workflow_id, "transformation", "github_writeback_requested",
              {"writeback_id": writeback["id"], "transformations": ids, "job_id": job["id"]})
    return {"status": "queued", "writeback_id": writeback["id"], "job_id": job["id"]}


def _actor(identity: dict | None) -> str | None:
    from services.github_review_service import actor_name
    return actor_name(identity)


def _authorize(identity: dict, mapping: dict) -> None:
    from services.github_review_service import authorize_github_login, authorize_repository
    if identity.get("github_login"):
        authorize_github_login(mapping, identity["github_login"])
    elif identity.get("email"):
        authorize_repository(identity, mapping)
    else:
        raise AccessError("The write-back request carries no verified developer identity.", 403)


def _gh(app, method, path, mapping, body=None, expected=(200,), what="GitHub request"):
    return app.request(method, f"repos/{mapping['repository_owner']}/{mapping['repository_name']}/"
                       f"{path}", mapping["installation_id"], json=body, expected=expected,
                       what=what) or {}


def run_writeback_job(job: dict, app) -> dict:
    """Commit the accepted changes. Returns the write-back row (committed or refused).

    Raises GitHubAPIError for transient GitHub failures (the worker retries);
    every definitive refusal is recorded on the write-back row instead.
    """
    command = json.loads(job.get("command_json") or "{}")
    writeback = reviews.get_writeback(command.get("writeback_id") or "")
    if writeback is None:
        raise WritebackRefused("The write-back request no longer exists.")
    if writeback["status"] == reviews.WB_COMMITTED:
        return writeback
    mapping = prs.get_mapping_by_id(writeback["mapping_id"])
    try:
        return _commit(app, writeback, mapping, command.get("identity") or {})
    except GitHubAPIError as exc:
        if exc.retryable:
            raise                      # the worker retries; a made commit is reused
        reason = f"GitHub refused the update: {exc.message}"
        reviews.update_writeback(writeback["id"], status=reviews.WB_REFUSED, error=reason)
        log_event(writeback["workflow_id"], "transformation", "github_writeback_refused",
                  {"writeback_id": writeback["id"], "reason": reason}, actor="system")
        return reviews.get_writeback(writeback["id"])
    except (WritebackRefused, AccessError) as exc:
        reviews.update_writeback(writeback["id"], status=reviews.WB_REFUSED, error=exc.message)
        log_event(writeback["workflow_id"], "transformation", "github_writeback_refused",
                  {"writeback_id": writeback["id"], "reason": exc.message}, actor="system")
        return reviews.get_writeback(writeback["id"])


def _commit(app, writeback: dict, mapping: dict, identity: dict) -> dict:
    if not config.github_pr_writeback_enabled():
        raise WritebackRefused("Write-back was disabled before the commit was made.")
    _authorize(identity, mapping)

    base = writeback["base_sha"]
    pr = _gh(app, "GET", f"pulls/{mapping['pr_number']}", mapping, what="Pull request lookup")
    head = pr.get("head") or {}
    if pr.get("state") != "open":
        raise WritebackRefused("The pull request is no longer open.")
    if (head.get("repo") or {}).get("full_name") != mapping["repository_full_name"]:
        raise WritebackRefused("The pull request branch is in a fork; RefactorIQ cannot write to it. "
                               "Download the patch instead.")
    branch = head.get("ref")
    if not branch:
        raise WritebackRefused("The pull request has no head branch.")

    if head.get("sha") != base:
        # Our own commit, from an interrupted earlier attempt?
        if writeback.get("commit_sha") and head.get("sha") == writeback["commit_sha"]:
            return _finish(app, writeback, mapping, writeback["commit_sha"], branch)
        raise WritebackRefused(
            "The pull request has new commits since the analysed commit "
            f"{base[:7]}; applying the changes could overwrite them. Re-run the analysis.")

    ids = writeback["transformation_ids_json"] or []
    records = [r for r in reviews.list_transformations(writeback["workflow_id"])
               if r["display_id"] in ids]
    if sorted(r["display_id"] for r in records) != sorted(ids):
        raise WritebackRefused("Some accepted changes no longer exist.")
    for record in records:
        if record["status"] not in (reviews.ACCEPTED, reviews.APPLIED):
            raise WritebackRefused(f"{record['display_id']} is no longer accepted.")
        if record["head_sha"] != base:
            raise WritebackRefused(f"{record['display_id']} was computed for another commit.")
        _check_integrity(record)

    commit_sha = writeback.get("commit_sha")
    if not commit_sha:
        base_commit = _gh(app, "GET", f"git/commits/{base}", mapping, what="Commit lookup")
        base_tree = (base_commit.get("tree") or {}).get("sha")
        tree = _gh(app, "GET", f"git/trees/{base_tree}?recursive=1", mapping, what="Tree lookup")
        entries = {e.get("path"): e for e in tree.get("tree") or []}
        new_entries = []
        for record in records:
            entry = entries.get(record["file_path"])
            if entry is None and not tree.get("truncated"):
                raise WritebackRefused(f"{record['file_path']} is not in the analysed commit.")
            if entry and record.get("original_blob_sha") and entry.get("sha") != record["original_blob_sha"]:
                raise WritebackRefused(f"{record['file_path']} differs from the reviewed original.")
            blob = _gh(app, "POST", "git/blobs", mapping, expected=(201,), what="Blob creation",
                       body={"content": record["refactored_content"], "encoding": "utf-8"})
            new_entries.append({"path": record["file_path"], "type": "blob",
                                "mode": (entry or {}).get("mode") or "100644", "sha": blob["sha"]})
        new_tree = _gh(app, "POST", "git/trees", mapping, expected=(201,), what="Tree creation",
                       body={"base_tree": base_tree, "tree": new_entries})
        message = _commit_message(records, writeback)
        commit = _gh(app, "POST", "git/commits", mapping, expected=(201,), what="Commit creation",
                     body={"message": message, "tree": new_tree["sha"], "parents": [base]})
        commit_sha = commit["sha"]
        # Recorded BEFORE the ref moves: a retry reuses this commit.
        reviews.update_writeback(writeback["id"], status=reviews.WB_COMMITTING,
                                 commit_sha=commit_sha, branch=branch)

    try:
        _gh(app, "PATCH", f"git/refs/heads/{branch}", mapping, what="Branch update",
            body={"sha": commit_sha, "force": False})
    except GitHubAPIError as exc:
        text = (exc.message or "").lower()
        if exc.status == 422 and "fast forward" in text:
            raise WritebackRefused("The branch moved while the commit was prepared; nothing was "
                                   "overwritten. Re-run the analysis.") from None
        if exc.status in (403, 422) and ("protected" in text or "rule" in text):
            raise WritebackRefused("The branch is protected; RefactorIQ does not bypass branch "
                                   "protection. Download the patch instead.") from None
        if exc.status in (403, 404):
            raise WritebackRefused("The GitHub App cannot update this branch (Contents: Read and "
                                   "write permission is required).") from None
        raise
    return _finish(app, writeback, mapping, commit_sha, branch)


def _commit_message(records: list, writeback: dict) -> str:
    lines = [f"RefactorIQ: apply {len(records)} accepted refactoring change(s)", ""]
    for record in records:
        refs = ", ".join(str(x.get("refactoring")) for x in record.get("refactorings_json") or [])
        lines.append(f"- {record['display_id']} {record['file_path']}: {refs or 'refactoring'}")
    lines += ["", "Validated by SCTVA (syntax, structural, behavioural) and explicitly accepted.",
              "", f"Accepted-by: {writeback.get('requested_by') or 'unknown'}",
              f"RefactorIQ-Workflow: {writeback['workflow_id']}",
              f"{TRAILER}: {writeback['id']}"]
    return "\n".join(lines)


def _finish(app, writeback: dict, mapping: dict, commit_sha: str, branch: str) -> dict:
    now = jobs.stamp()
    reviews.update_writeback(writeback["id"], status=reviews.WB_COMMITTED, commit_sha=commit_sha,
                             branch=branch, committed_at=now, error=None)
    for record in reviews.list_transformations(writeback["workflow_id"]):
        if record["display_id"] in (writeback["transformation_ids_json"] or []):
            reviews.update_transformation(record["id"], status=reviews.APPLIED,
                                          applied_commit_sha=commit_sha, applied_at=now)
    update_workflow(writeback["workflow_id"], status="completed")
    log_event(writeback["workflow_id"], "transformation", "github_writeback_committed",
              {"writeback_id": writeback["id"], "commit_sha": commit_sha, "branch": branch,
               "transformations": writeback["transformation_ids_json"],
               "accepted_by": writeback.get("requested_by")}, actor="system")
    prs.update_mapping(mapping["id"], review_status="applied")
    logger.info("Write-back %s committed %s to %s#%s", writeback["id"], commit_sha[:7],
                mapping["repository_full_name"], mapping["pr_number"])
    return reviews.get_writeback(writeback["id"])


def build_patch(workflow_id: str) -> str:
    """Unified diff of the accepted (or applied) changes — the safe fallback."""
    records = [r for r in reviews.list_transformations(workflow_id)
               if r["status"] in (reviews.ACCEPTED, reviews.APPLIED) and r.get("diff")]
    return "".join(r["diff"] if r["diff"].endswith("\n") else r["diff"] + "\n" for r in records)
