"""
Access to GitHub-linked workflows
=================================
R26-SE-008 | Bandara S M Y M | IT22277886

A workflow created from a GitHub pull request holds findings about code in a
repository that may be private. Its id appears in a GitHub link, so the id
alone must not grant access: every request that names such a workflow needs a
signed-in DIWO user.

    Authorization: Bearer <Supabase access token>

That is the token user_management issues at login and the frontend already
keeps (localStorage "user_auth_token"). It is verified

  * locally, HS256, when SUPABASE_JWT_SECRET is set — the same check
    user_management/middleware/auth_middleware.py performs; or
  * by asking Supabase Auth (auth.get_user) when it is not, with the answer
    cached for a minute so a page load is not a dozen round trips.

FAILS CLOSED. No way to verify a token means 503 for GitHub-linked workflows,
never "let it through". Workflows created in the DIWO UI are not affected —
their routes behave exactly as before.

GitHub identity is not mapped to DIWO users yet, so authorization is "any
signed-in user", optionally narrowed by GITHUB_WORKFLOW_ALLOWED_EMAILS.
"""

import hashlib
import logging
import threading
import time

import jwt
from flask import g, request

import config
from db import github_pr_workflow_repository as prs

logger = logging.getLogger("diwo.access")

_CACHE_SECONDS = 60
_CACHE_MAX = 1000
_cache: dict = {}
_cache_lock = threading.Lock()


class AccessError(Exception):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.message = message
        self.status = status


def _bearer_token() -> str | None:
    header = request.headers.get("Authorization", "")
    parts = header.split()
    if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1]:
        return parts[1]
    return None


def _verify_locally(token: str, secret: str) -> dict:
    try:
        claims = jwt.decode(token, secret, algorithms=["HS256"], options={"verify_aud": False})
    except jwt.ExpiredSignatureError:
        raise AccessError("Your DIWO session has expired. Sign in again.", 401) from None
    except jwt.PyJWTError:
        raise AccessError("Invalid DIWO session token.", 401) from None
    if not claims.get("sub"):
        raise AccessError("Invalid DIWO session token.", 401)
    return {"user_id": claims["sub"], "email": (claims.get("email") or "").lower()}


def _verify_with_supabase(token: str) -> dict:
    key = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[1] > now:
            return hit[0]

    from db.supabase_client import get_supabase
    try:
        response = get_supabase().auth.get_user(token)
    except Exception:  # noqa: BLE001 - gotrue raises for invalid/expired tokens
        raise AccessError("Invalid or expired DIWO session token.", 401) from None
    user = getattr(response, "user", None)
    if user is None or not getattr(user, "id", None):
        raise AccessError("Invalid or expired DIWO session token.", 401)
    identity = {"user_id": user.id, "email": (getattr(user, "email", "") or "").lower()}

    with _cache_lock:
        if len(_cache) >= _CACHE_MAX:
            _cache.clear()
        _cache[key] = (identity, now + _CACHE_SECONDS)
    return identity


def authenticate() -> dict:
    """The signed-in user making this request, or AccessError."""
    token = _bearer_token()
    if not token:
        raise AccessError(
            "This workflow was created from a GitHub pull request. Sign in to DIWO to open it.", 401)

    secret = config.supabase_jwt_secret()
    if secret:
        identity = _verify_locally(token, secret)
    elif config.uses_supabase():
        identity = _verify_with_supabase(token)
    else:
        raise AccessError(
            "Access to GitHub-linked workflows cannot be verified: set SUPABASE_JWT_SECRET "
            "or the Supabase credentials on the DIWO backend.", 503)

    allowed = config.github_workflow_allowed_emails()
    if allowed and identity["email"] not in allowed:
        raise AccessError("Your DIWO account is not allowed to open GitHub-linked workflows.", 403)
    return identity


def _missing_table(exc: Exception) -> bool:
    """True when the mapping table simply does not exist yet (migration pending)."""
    code = getattr(exc, "code", None)
    text = str(getattr(exc, "message", "") or exc).lower()
    return code in ("42P01", "PGRST205") or "does not exist" in text \
        or "could not find the table" in text


def linked_mapping(workflow_id: str) -> dict | None:
    """The PR-commit mapping of a GitHub-linked workflow, or None.

    Raises AccessError if it cannot be determined.
    """
    try:
        return prs.get_mapping_for_workflow(workflow_id) if workflow_id else None
    except Exception as exc:  # noqa: BLE001
        if _missing_table(exc):
            # No mapping table means no linked workflow can exist yet.
            return None
        logger.warning("Could not check whether %s is GitHub-linked: %s",
                       workflow_id, type(exc).__name__)
        raise AccessError("Could not verify access to this workflow. Try again.", 503) from None


def is_linked(workflow_id: str) -> bool:
    """Whether a workflow is GitHub-linked. Raises AccessError if unknowable."""
    return linked_mapping(workflow_id) is not None


def require_github_workflow_access():
    """before_request hook: guard every route that names a GitHub-linked workflow.

    Every such route needs a signed-in user. Routes that record a decision also
    need repository authorization and a current PR commit, and routes that
    would read CUQA's shared workspace are refused — see
    services/github_review_service.py. Returns None to continue, or
    (json, status) to stop the request.
    """
    from api import err
    from services.github_review_service import guard_review_request

    workflow_id = (request.view_args or {}).get("wf_id")
    if not workflow_id or request.method == "OPTIONS":
        return None
    try:
        mapping = linked_mapping(workflow_id)
        if mapping is None:
            return None
        g.diwo_user = authenticate()
        g.github_mapping = mapping
        guard_review_request(request.endpoint, g.diwo_user, mapping)
    except AccessError as exc:
        return err(exc.message, exc.status)
    return None


def current_github_context():
    """(identity, mapping) set by the guard for this request, or (None, None)."""
    return getattr(g, "diwo_user", None), getattr(g, "github_mapping", None)


def filter_visible_workflows(workflows: list) -> list:
    """Hide GitHub-linked workflows from callers who are not signed in.

    Listing ids would otherwise hand out exactly the capability the guard
    protects.
    """
    try:
        linked = prs.github_linked_workflow_ids()
    except Exception as exc:  # noqa: BLE001
        if _missing_table(exc):
            return workflows
        logger.warning("Could not read GitHub-linked workflow ids: %s", type(exc).__name__)
        linked = None   # unknown: show only what is certainly not linked — nothing
    if linked is not None and not linked:
        return workflows
    try:
        authenticate()
        return workflows
    except AccessError:
        if linked is None:
            return []
        return [wf for wf in workflows if wf["id"] not in linked]


def reset_cache() -> None:
    with _cache_lock:
        _cache.clear()
