"""
app.py
Flask application entry point for the User Management service.
Runs on port 5005 (or PORT env var). Includes comprehensive debug logging.
"""

import os
import logging
from flask import Flask, jsonify, request
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

# Configure standard logging format
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("user_management")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app() -> Flask:
    app = Flask(__name__)

    # Load secret key from env
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "super-secret-jwt-key-change-in-production")

    # CORS - allow specific origins used by the frontend services
    CORS(app, resources={
        r"/api/*": {
            "origins": [
                "http://localhost:5173",   # Vite dev server
                "http://localhost:3000",   # React / Next.js
                "http://localhost:5000",   # Other Flask services
                "http://localhost:5005",   # User Management service
                "http://localhost:6000",   # Testing port
            ],
            "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            "allow_headers": ["Content-Type", "Authorization"],
        }
    })

    # Debug Request & Response Logging
    @app.before_request
    def log_request_info():
        if request.path.startswith("/api/"):
            auth_header = request.headers.get("Authorization")
            has_auth = "Yes (Bearer)" if auth_header else "No"
            json_body = request.get_json(silent=True)
            sanitized_body = {}
            if isinstance(json_body, dict):
                sanitized_body = {
                    k: ("***" if k.lower() in ("password", "secret", "token") else v)
                    for k, v in json_body.items()
                }
            logger.info(
                f"--> {request.method} {request.path} | Auth Header: {has_auth} | Payload: {sanitized_body}"
            )

    @app.after_request
    def log_response_info(response):
        if request.path.startswith("/api/"):
            status_code = response.status_code
            status_symbol = "OK" if status_code < 400 else ("BAD REQUEST / AUTH FAIL" if status_code < 500 else "SERVER ERROR")
            logger.info(
                f"<-- {request.method} {request.path} | Status: {status_code} ({status_symbol})"
            )
        return response

    # Register blueprints
    from routes.user_routes import user_bp
    app.register_blueprint(user_bp)

    # Root health endpoint
    @app.route("/", methods=["GET"])
    def root():
        return jsonify({
            "status": "ok",
            "service": "user-management",
            "version": "1.0.0",
            "endpoints": {
                "health":    "GET  /api/auth/health",
                "register":  "POST /api/auth/register",
                "login":     "POST /api/auth/login",
                "logout":    "POST /api/auth/logout",
                "profile":   "GET|PUT /api/auth/profile",
                "account":   "DELETE /api/auth/account",
                "users":     "GET /api/auth/users  [admin]",
                "user_role": "PUT /api/auth/users/<id>/role  [admin]",
            }
        }), 200

    # Generic 404 handler
    @app.errorhandler(404)
    def not_found(e):
        logger.warning(f"404 Not Found: {request.method} {request.path}")
        return jsonify({"success": False, "error": "Endpoint not found."}), 404

    # Generic 405 handler
    @app.errorhandler(405)
    def method_not_allowed(e):
        logger.warning(f"405 Method Not Allowed: {request.method} {request.path}")
        return jsonify({"success": False, "error": "Method not allowed."}), 405

    # Generic 500 handler
    @app.errorhandler(500)
    def internal_error(e):
        logger.error(f"500 Internal Error: {e}", exc_info=True)
        return jsonify({"success": False, "error": "Internal server error."}), 500

    return app


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

app = create_app()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5005))
    debug = os.getenv("FLASK_ENV", "development") == "development"
    print(f"[user-management] Starting on http://0.0.0.0:{port}  (debug={debug})")
    app.run(host="0.0.0.0", port=port, debug=debug)
