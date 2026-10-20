"""
The RefactorIQ status comment
=============================
R26-SE-008 | Bandara S M Y M | IT22277886

ONE bot-managed comment per pull request, edited in place for every command
reply and state change, so a review conversation does not bury the PR under
bot posts. Its id is kept on the PR-commit mapping and carried forward to the
next commit's mapping, so a new push keeps updating the same comment.

    POST  /repos/{o}/{r}/issues/{n}/comments        first time
    PATCH /repos/{o}/{r}/issues/comments/{id}        afterwards

PR comments go through the Issues API; a GitHub App needs
"Pull requests: Read and write" (or "Issues: Read and write") for it.
"""

import logging

from db import github_pr_workflow_repository as prs
from services.github_app_service import GitHubAPIError

logger = logging.getLogger("diwo.github.comment")

MARKER = "<!-- refactoriq:status -->"
MAX_BODY = 60_000


def _comment_id_for_pr(mapping: dict) -> str | None:
    if mapping.get("status_comment_id"):
        return str(mapping["status_comment_id"])
    for row in prs._backend().select({"repository_full_name": mapping["repository_full_name"],
                                      "pr_number": mapping["pr_number"]},
                                     order_desc="created_at"):
        if row.get("status_comment_id"):
            return str(row["status_comment_id"])
    return None


def upsert_status_comment(app, mapping: dict, body: str) -> str | None:
    """Create or edit the PR's status comment. Returns its id, or None on failure.

    Best effort: a comment that cannot be posted never fails the action it
    reports on — the Check Run and DIWO carry the same state.
    """
    owner, repo = mapping["repository_owner"], mapping["repository_name"]
    text = f"{MARKER}\n{body}"[:MAX_BODY]
    comment_id = _comment_id_for_pr(mapping)
    try:
        if comment_id:
            try:
                app.request("PATCH", f"repos/{owner}/{repo}/issues/comments/{comment_id}",
                            mapping["installation_id"], json={"body": text}, expected=(200,),
                            what="Status comment update")
            except GitHubAPIError as exc:
                if exc.status != 404:
                    raise
                comment_id = None            # deleted by someone: post a new one
        if not comment_id:
            created = app.request("POST", f"repos/{owner}/{repo}/issues/{mapping['pr_number']}/comments",
                                  mapping["installation_id"], json={"body": text},
                                  expected=(201,), what="Status comment creation") or {}
            comment_id = str(created.get("id")) if created.get("id") else None
        if comment_id and str(mapping.get("status_comment_id") or "") != comment_id:
            prs.update_mapping(mapping["id"], status_comment_id=comment_id)
        return comment_id
    except GitHubAPIError as exc:
        logger.warning("Status comment not updated for %s#%s: %s",
                       mapping["repository_full_name"], mapping["pr_number"], exc.message)
        return None
