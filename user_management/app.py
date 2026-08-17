"""
app.py
Flask application entry point for the User Management service.
Runs on port 6000.
"""

import os
from flask import Flask, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

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
                "http://localhost:6000",   # Self (for testing)
            ],
            "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            "allow_headers": ["Content-Type", "Authorization"],
        }
    })

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
        return jsonify({"success": False, "error": "Endpoint not found."}), 404

    # Generic 405 handler
    @app.errorhandler(405)
    def method_not_allowed(e):
        return jsonify({"success": False, "error": "Method not allowed."}), 405

    # Generic 500 handler
    @app.errorhandler(500)
    def internal_error(e):
        return jsonify({"success": False, "error": "Internal server error."}), 500

    return app


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

app = create_app()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 6000))
    debug = os.getenv("FLASK_ENV", "development") == "development"
    print(f"[user-management] Starting on http://0.0.0.0:{port}  (debug={debug})")
    app.run(host="0.0.0.0", port=port, debug=debug)
