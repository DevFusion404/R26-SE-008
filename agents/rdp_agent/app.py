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
import re
import sys

from flask import Flask, render_template, request, jsonify
from flask_cors import CORS

# Add current directory to path so we can import src package
sys.path.insert(0, os.path.dirname(__file__))

from src.models import QualityReport
from src.pipeline import RDPAgent
from src.config import load_config, setup_logging
from src.dependency_analyzer import SEVERITY_ORDER

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB max upload

# Enable CORS for frontend communication
CORS(app, resources={
    r"/generate": {"origins": ["http://localhost:5173", "http://localhost:3000"]},
    r"/health": {"origins": ["http://localhost:5173", "http://localhost:3000"]},
    r"/config": {"origins": ["http://localhost:5173", "http://localhost:3000"]},
})


# ---------------------------------------------------------------------------
# Format translation: CUQA → RDP
# ---------------------------------------------------------------------------

def _translate_cuqa_to_rdp(data: dict) -> dict:
    """Convert a CUQA quality report into the RDP QualityReport schema.

    CUQA shape:
        {
          "files": [
            {
              "file": "path/to/Foo.java",
              "language": "java",
              "code_smells": [
                {"type": "LongMethod", "severity": "high", "line": 42, "message": "..."}
              ],
              "metrics": {"lines_of_code": 300, "functions": 20, ...}
            }
          ],
          "summary": {"total_files": 1, "average_quality_score": 65.0, ...}
        }

    RDP shape:
        {
          "target": "Foo.java",
          "smells": [
            {
              "id": "smell_001",
              "type": "Long Method",
              "location": {"file": "Foo.java", "class": "Foo", "method": "unknown", "lines": [42]},
              "metrics": {"lines_of_code": 300, "complexity": 1},
              "severity": "high",
              "details": "..."
            }
          ],
          "metrics_summary": {"total_lines_of_code": 300, "average_quality_score": 65.0}
        }
    """

    # --- Smell type normalisation: CUQA camelCase → RDP Title Case ---
    # CUQA emits: "LongMethod", "LargeClass", "TooManyParameters", "MagicNumber", "BareExcept"
    # RDP knowledge base keys: "Long Method", "God Class", "Long Parameter List", etc.
    SMELL_TYPE_MAP = {
        # Python smells
        "LongMethod":          "Long Method",
        "LargeClass":          "God Class",
        "TooManyParameters":   "Long Parameter List",
        "MagicNumber":         "Magic Numbers",
        "MagicNumbers":        "Magic Numbers",
        "BareExcept":          "Dead Code",          # closest analogue: defensive code smell
        "DeadCode":            "Dead Code",
        "DuplicateCode":       "Duplicate Code",
        "DataClumps":          "Data Clumps",
        "FeatureEnvy":         "Feature Envy",
        "ShotgunSurgery":      "Shotgun Surgery",
        "SwitchStatements":    "Switch Statements",
        "LazyClass":           "Lazy Class",
        "PrimitiveObsession":  "Primitive Obsession",
        "MessageChains":       "Message Chains",
        "InappropriateIntimacy": "Inappropriate Intimacy",
        "SpeculativeGenerality": "Speculative Generality",
        # Java smells (some overlap)
        "LongClass":           "God Class",
        "ComplexMethod":       "Long Method",
        "LongParameterList":   "Long Parameter List",
        "GodClass":            "God Class",
        "FunctionTooLong":     "Long Method",
        "MethodTooLong":       "Long Method",
        # Already correctly formatted (pass-through)
        "Long Method":         "Long Method",
        "God Class":           "God Class",
        "Feature Envy":        "Feature Envy",
        "Duplicate Code":      "Duplicate Code",
        "Data Clumps":         "Data Clumps",
        "Shotgun Surgery":     "Shotgun Surgery",
        "Switch Statements":   "Switch Statements",
        "Lazy Class":          "Lazy Class",
        "Speculative Generality": "Speculative Generality",
        "Primitive Obsession": "Primitive Obsession",
        "Long Parameter List": "Long Parameter List",
        "Message Chains":      "Message Chains",
        "Comments":            "Comments",
        "Magic Numbers":       "Magic Numbers",
        "Inappropriate Intimacy": "Inappropriate Intimacy",
        "Dead Code":           "Dead Code",
    }

    files = data.get("files", [])
    summary = data.get("summary", {})

    # Use first file name as target, or repo name from summary, or "unknown"
    if files:
        first_file = files[0].get("file", "unknown")
        target = first_file.split("/")[-1].split("\\")[-1]
    else:
        target = summary.get("repo_name", "unknown")

    smells = []
    smell_idx = 1

    for file_entry in files:
        file_path = file_entry.get("file", "unknown")
        file_name = file_path.split("/")[-1].split("\\")[-1]
        file_metrics = file_entry.get("metrics", {})
        language = file_entry.get("language", "unknown")
        code_smells = file_entry.get("code_smells", [])

        for raw in code_smells:
            smell_id = f"smell_{smell_idx:03d}"
            smell_idx += 1

            # Normalise smell type: CUQA camelCase → RDP Title Case
            raw_type = raw.get("type", "Unknown Smell")
            normalised_type = SMELL_TYPE_MAP.get(raw_type, raw_type)

            # Resolve the entity (function / class / method name)
            # Priority: explicit "entity" field → parse message → file base name
            entity = raw.get("entity")
            if not entity:
                # CUQA messages look like: "Function 'foo' has ..." or "Class 'Bar' has ..."
                _m = re.search(r"(?:Function|Method|Class)\s+'([^']+)'", raw.get("message", ""))
                entity = _m.group(1) if _m else None

            # Build location dict
            line = raw.get("line")
            base_name = file_name.replace(".java", "").replace(".py", "")
            location = {
                "file": file_name,
                "language": language,
                "class": entity if normalised_type in ("God Class", "Large Class", "Lazy Class") else base_name,
                "method": entity if normalised_type not in ("God Class", "Large Class", "Lazy Class") else None,
                "lines": [line] if line else [],
            }
            # Clean up None method
            if location["method"] is None:
                location["method"] = base_name


            # Build metrics dict — file-level + any numeric smell-level fields
            metrics = {
                "lines_of_code": file_metrics.get("lines_of_code", 0),
                "complexity": file_metrics.get("complexity", 1),
                "functions": file_metrics.get("functions", 0),
                "classes": file_metrics.get("classes", 0),
            }
            for k, v in raw.items():
                if k not in ("type", "severity", "line", "message", "entity", "method") \
                        and isinstance(v, (int, float)):
                    metrics[k] = v

            smells.append({
                "id": smell_id,
                "type": normalised_type,
                "location": location,
                "metrics": metrics,
                "severity": raw.get("severity", "medium"),
                "details": raw.get("message", ""),
            })

    # Aggregate metrics_summary
    metrics_summary = {
        "total_files": len(files),
        "total_smells": len(smells),
        "average_quality_score": summary.get("average_quality_score", 0.0),
        "total_lines_of_code": summary.get("total_lines_of_code", 0),
    }

    return {
        "target": target,
        "smells": smells,
        "metrics_summary": metrics_summary,
    }





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

        # --- Auto-translate CUQA format → RDP format ---
        # CUQA report shape: { files: [{ file, code_smells, metrics }], summary }
        # RDP report shape:  { target, smells: [{ id, type, location, metrics, severity }] }
        if "files" in data and "smells" not in data:
            data = _translate_cuqa_to_rdp(data)

        report = QualityReport.from_dict(data)
        agent = RDPAgent(
            weights=config.get("weights", {}),
            severity_order=config.get("severity_order", SEVERITY_ORDER),
            ml_config=config.get("ml_scoring", {}),
        )
        result = agent.process_report_with_trace(report)

        return jsonify({
            "success": True,
            "plan": result["plan"],
            "trace": result["trace"],
        })
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Plan generation failed: {exc}"}), 500


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
