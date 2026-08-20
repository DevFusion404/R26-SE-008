"""
DIWO REST API
=============
R26-SE-008 | Bandara S M Y M | IT22277886

The single large diwo/routes.py is now four blueprints, split by
responsibility:

    workflow_routes.py     workflow lifecycle and the five approval stages
    integration_routes.py  CUQA / RDP / SCTVA reachability and the CUQA proxy
    git_routes.py          clone, branch, apply, commit, push
    feedback_routes.py     audit logs and the feedback training export

Every one of them is still mounted at /api, and no endpoint URL, method,
request body or response body changed in the split — the frontend talks to
exactly the same API it did before.

Route handlers do four things only: read the request, validate it, call a
service, and shape the response. The error types the services raise are
translated to the same JSON error bodies the handlers used to return inline.
"""

from flask import jsonify

__all__ = ["err", "cuqa_error_response", "register_blueprints"]


def err(msg: str, code: int = 400):
    """The API's one error shape: {"error": "..."} with an HTTP status."""
    return jsonify({"error": msg}), code


def cuqa_error_response(exc):
    """Pass CUQA's own status through so the UI can tell 'not running' from
    'running but no repository loaded'."""
    from clients.cuqa_client import cuqa_base_url

    status = exc.status if 400 <= exc.status < 600 else 502
    payload = {
        "error":     exc.message,
        "cuqa_url":  cuqa_base_url(),
        "reachable": exc.status != 503,
    }
    return jsonify(payload), status


def register_blueprints(app, url_prefix: str = "/api"):
    """Mount every DIWO blueprint and the service-error handlers.

    Imports are local so importing this package never pulls in Flask routes
    (and therefore the database) as a side effect.
    """
    from services.git_service import GitOperationError
    from services.transformation_service import TransformationError
    from services.workflow_service import StageError

    from api.feedback_routes import feedback_bp
    from api.git_routes import git_bp
    from api.integration_routes import integration_bp
    from api.workflow_routes import workflow_bp

    for blueprint in (workflow_bp, integration_bp, git_bp, feedback_bp):
        app.register_blueprint(blueprint, url_prefix=url_prefix)

    # A service raising is answered exactly as the old inline `return _err(...)`
    # was, so moving the logic out of the handlers changed no status code.
    @app.errorhandler(StageError)
    def _stage_error(exc):
        return err(exc.message, exc.status)

    @app.errorhandler(GitOperationError)
    def _git_error(exc):
        return err(exc.message, exc.status)

    # SCTVA failures carry the agent URL and the missing paths, so the
    # Transformation stage can say exactly what to start or re-run.
    @app.errorhandler(TransformationError)
    def _transformation_error(exc):
        payload = {"error": exc.message}
        if isinstance(exc.detail, dict):
            payload.update(exc.detail)
        return jsonify(payload), exc.status

    return app
