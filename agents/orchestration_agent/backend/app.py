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

from flask import Flask
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

    @app.route("/")
    def health():
        return {"status": "DIWO Agent Backend Running", "version": "1.0.0"}

    return app


if __name__ == "__main__":
    application = create_app()
    application.run(debug=config.DEBUG, host=config.HOST, port=config.PORT)
