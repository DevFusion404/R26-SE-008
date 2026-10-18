"""SCTVA Flask application entrypoint."""

from __future__ import annotations

import os
import socket

from flask import Flask, jsonify, render_template, request, make_response

try:
    from flask_cors import CORS
    HAS_FLASK_CORS = True
except ImportError:
    HAS_FLASK_CORS = False

from sctva.integration.api import create_sctva_blueprint


def _port_is_in_use(host: str, port: int) -> bool:
    """Prevent a stale local SCTVA process from sharing the configured port."""

    probe_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    try:
        with socket.create_connection((probe_host, port), timeout=0.25):
            return True
    except OSError:
        return False

def create_app() -> Flask:
    app = Flask(__name__)

    if HAS_FLASK_CORS:
        CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=False)

    app.register_blueprint(create_sctva_blueprint())

    @app.get("/")
    def index() -> str:
        return render_template("index.html")

    @app.get("/health")
    def health() -> tuple:
        return jsonify({"status": "ok", "service": "sctva"}), 200

    @app.before_request
    def handle_preflight():
        if request.method == "OPTIONS":
            res = make_response()
            res.headers["Access-Control-Allow-Origin"] = "*"
            res.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS, PUT, DELETE"
            res.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With, Accept"
            res.headers["Access-Control-Max-Age"] = "86400"
            return res, 200

    @app.after_request
    def add_cors_headers(response):
        response.headers["Access-Control-Allow-Origin"] = os.getenv("SCTVA_ALLOW_ORIGIN", "*")
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With, Accept"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS, PUT, DELETE"
        return response

    return app


if __name__ == "__main__":
    port = int(os.getenv("SCTVA_PORT", "8002"))
    host = "0.0.0.0"
    if _port_is_in_use(host, port):
        raise SystemExit(
            f"SCTVA startup refused: port {port} is already in use. "
            "Stop the existing SCTVA process before starting a new backend."
        )
    app = create_app()
    app.run(host=host, port=port, debug=False)

