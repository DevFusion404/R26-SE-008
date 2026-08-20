"""
Git routes
==========
R26-SE-008 | Bandara S M Y M | IT22277886

Repository clone / fetch, branch creation, applying the refactored project,
commit and push. One endpoint, unchanged:

    POST /api/diwo/apply-and-push

Everything it does lives in services/git_service.py, so this module is only
the HTTP shell.
"""

from flask import Blueprint, jsonify, request

from services.git_service import apply_and_push as git_apply_and_push

git_bp = Blueprint("git", __name__)


@git_bp.route("/diwo/apply-and-push", methods=["POST"])
def apply_and_push():
    """
    Write the refactored project into a git repository, on its own branch.

    Body:
      {
        "files":           [{ path, after|content }, ...],   # the WHOLE project
        "branch_name":     "refactoring/diwo-changes",
        "repository_path": "https://github.com/user/repo"  |  "C:/path/to/repo",
        "commit_message":  "...",       optional
        "commit":          true,        optional (default true)
        "push":            true         optional (default true when a remote exists)
      }

    `files` is the same entry list the "Download Project (.zip)" action packs:
    every project file, with the accepted refactorings replacing the originals
    in place. Sending only the refactored files would leave a freshly cloned
    repository holding nothing else.

    A failure inside the service raises GitOperationError, which api/__init__
    answers with the same {"error": ...} body and status this handler used to
    return inline.
    """
    data = request.get_json(force=True, silent=True) or {}
    return jsonify(git_apply_and_push(data)), 200
