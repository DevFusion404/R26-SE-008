"""
Flask Web UI for the Refactoring Decision & Planning Agent.
============================================================

A web interface for uploading JSON quality reports and generating
refactoring plans with full pipeline trace visualization.

Usage:
    python app.py
    # Open http://localhost:5000 in your browser.
"""

import json
import os
import sys

from flask import Flask, render_template, request, jsonify

# Add src directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from models import QualityReport
from pipeline import RDPAgent
from config import load_config, setup_logging
from dependency_analyzer import SEVERITY_ORDER

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB max upload


@app.route("/")
def index():
    """Render the upload form."""
    return render_template("index.html")


@app.route("/generate", methods=["POST"])
def generate():
    """Accept a JSON quality report and return the refactoring plan + trace."""
    # --- Handle file upload ---
    if "file" in request.files:
        uploaded = request.files["file"]
        if uploaded.filename == "":
            return jsonify({"error": "No file selected."}), 400
        if not uploaded.filename.lower().endswith(".json"):
            return jsonify({"error": "Please upload a .json file."}), 400
        try:
            content = uploaded.read().decode("utf-8")
            data = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return jsonify({"error": f"Invalid JSON file: {exc}"}), 400

    # --- Handle raw JSON body ---
    elif request.is_json:
        data = request.get_json()
    else:
        return jsonify({"error": "No file or JSON body provided."}), 400

    # --- Generate plan with trace ---
    try:
        config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
        if not os.path.isfile(config_path):
            config_path = None

        config = load_config(config_path)
        setup_logging(config.get("log_level", "INFO"))

        report = QualityReport.from_dict(data)
        agent = RDPAgent(
            weights=config.get("weights", {}),
            severity_order=config.get("severity_order", SEVERITY_ORDER),
        )
        result = agent.process_report_with_trace(report)

        return jsonify({
            "success": True,
            "plan": result["plan"],
            "trace": result["trace"],
        })
    except Exception as exc:
        return jsonify({"error": f"Plan generation failed: {exc}"}), 500


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
