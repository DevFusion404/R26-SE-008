"""
Developer Interaction & Workflow Orchestration Agent - Flask Backend
=====================================================================
R26-SE-008 | Bandara S M Y M | IT22277886

This backend manages:
  - 5-stage workflow: smell review → smell selection → plan approval → transformation → comparison
  - Approval / Rejection handling
  - Feedback capture for the ML Feedback Manager
  - Audit log persistence (SQLite for prototype; swap to PostgreSQL in production)
  - Rollback coordination via Git-snapshot simulation
"""

from flask import Flask
from flask_cors import CORS
from db.database import init_db
from diwo.routes import diwo_bp

def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "diwo-prototype-secret-2026"
    app.config["DATABASE"] = "diwo_audit.db"
    CORS(app, resources={r"/api/*": {"origins": "*"}})

    with app.app_context():
        init_db(app)

    app.register_blueprint(diwo_bp, url_prefix="/api")

    @app.route("/")
    def health():
        return {"status": "DIWO Agent Backend Running", "version": "1.0.0"}

    return app


if __name__ == "__main__":
    application = create_app()
    application.run(debug=True, host="0.0.0.0", port=5001)
