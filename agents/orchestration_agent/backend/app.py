"""
Developer Interaction & Workflow Orchestration Agent - Flask Backend
=====================================================================
R26-SE-008 | Bandara S M Y M | IT22277886

The DIWO frontend's backend and the orchestrator of the other three agents:

    React DIWO frontend
            |
            v
    Orchestration Agent  ──>  CUQA  /  RDP  /  SCTVA

This backend manages:
  - 5-stage workflow: smell review → smell selection → plan approval →
    transformation → comparison
  - Approval / Rejection handling at every stage
  - Feedback capture for the ML Feedback Manager
  - Audit log persistence (SQLite for the prototype)
  - Rollback coordination via Git-snapshot simulation
  - Applying the accepted project to a git branch

Layout:
    config.py     every URL, port and path
    api/          four blueprints, all mounted at /api
    services/     workflow, planning, transformation, archive, git
    clients/      one HTTP client per specialized agent
    domain/       pure logic: report shapes, plan shapes, metrics, states
    db/           connection, schema, and the workflow repository
    runtime/      generated data: database, reports, archives
"""

from flask import Flask, jsonify
from flask_cors import CORS

import config
from api import register_blueprints
from db.database import init_db


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = config.SECRET_KEY
    app.config["DATABASE"] = str(config.database_path())
    CORS(app, resources={rf"{config.API_PREFIX}/*": {"origins": config.cors_origins()}})

    with app.app_context():
        init_db(app)

    register_blueprints(app, url_prefix=config.API_PREFIX)
    register_database_errors(app)

    @app.route("/")
    def health():
        return {"status": "DIWO Agent Backend Running", "version": "1.0.0"}

    return app


def register_database_errors(app):
    """Turn a PostgREST rejection into an answer that names its own cause.

    Without this, a row-level-security refusal reaches the browser as a 500
    carrying a Werkzeug traceback — so the developer sees "the button does
    nothing" and has to read a server log to find out that the wrong Supabase
    key is configured. The API error already says exactly what is wrong; this
    just forwards it with the fix attached.

    Registered only when Supabase is the backend, and imported inside the
    function so a SQLite deployment still needs no postgrest package.
    """
    if not config.uses_supabase():
        return

    try:
        from postgrest.exceptions import APIError
    except ImportError:
        return

    from db.supabase_client import key_is_public, supabase_key

    @app.errorhandler(APIError)
    def handle_postgrest_error(exc):
        detail = getattr(exc, "message", None) or str(exc)
        code = getattr(exc, "code", None)

        # 42501 is Postgres' insufficient_privilege, which through PostgREST
        # means one thing in practice: the key in use is not allowed to write.
        if code == "42501" or "row-level security" in detail.lower():
            hint = (
                "The Supabase key in use cannot write to this table. This "
                "backend writes on behalf of a workflow, not a signed-in user, "
                "so row-level security rejects a publishable/anon key. Set "
                "SUPABASE_SERVICE_ROLE_KEY to the SECRET / service_role key "
                "from Project Settings -> API, then restart."
            )
            if key_is_public(supabase_key()):
                hint += " (The key currently configured is a publishable key.)"
            return jsonify({"error": hint, "detail": detail, "code": code}), 500

        return jsonify({
            "error": f"The database rejected the request: {detail}",
            "code": code,
        }), 500


if __name__ == "__main__":
    application = create_app()
    application.run(debug=config.DEBUG, host=config.HOST, port=config.PORT)
