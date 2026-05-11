"""SCTVA Flask application entrypoint."""

from __future__ import annotations

import os

from flask import Flask, jsonify, render_template

from sctva.integration.api import create_sctva_blueprint

def create_app() -> Flask:
    app = Flask(__name__)
    app.register_blueprint(create_sctva_blueprint())

    @app.get("/")
    def index() -> str:
        return render_template("index.html")

    @app.get("/health")
    def health() -> tuple:
        return jsonify({"status": "ok", "service": "sctva"}), 200

    @app.after_request
    def add_cors_headers(response):
        response.headers["Access-Control-Allow-Origin"] = os.getenv("SCTVA_ALLOW_ORIGIN", "*")
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        return response

    return app


if __name__ == "__main__":
    port = int(os.getenv("SCTVA_PORT", "8002"))
    app = create_app()
    app.run(host="0.0.0.0", port=port, debug=False)
