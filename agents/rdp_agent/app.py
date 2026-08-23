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
rdp_cors_origins = os.getenv("RDP_CORS_ORIGINS", "*")
if rdp_cors_origins.strip() == "*":
    CORS(app, resources={r"/*": {"origins": "*"}})
else:
    CORS(app, resources={r"/*": {"origins": [o.strip() for o in rdp_cors_origins.split(",") if o.strip()]}})


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
        "LargeClass":          "Large Class",         # Changed from "God Class" for semantic accuracy
        "TooManyParameters":   "Long Parameter List",
        "MagicNumber":         "Magic Numbers",
        "MagicNumbers":        "Magic Numbers",
        "BareExcept":          "Bare Except",        # exception handling anti-pattern → correct KB entry
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
        # C smells
        "LongFunction":           "Long Function",
        "DeepNesting":            "Deep Nesting",
        "UnsafeFunctionUsage":    "Unsafe Function Usage",
        "GlobalVariable":         "Global Variable",
        "LargeHeaderFile":        "Large Header File",
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
            # Priority: explicit "entity" field → parse message → None for module-level smells
            entity = raw.get("entity")

            if not entity:
                # CUQA messages look like: "Function 'foo' has ..." or "Class 'Bar' has ..."
                _m = re.search(r"(?:Function|Method|Class)\s+'([^']+)'", raw.get("message", ""))
                entity = _m.group(1) if _m else None

            # RDP-FIX-03: Don't force file basename as class/method for module-level smells
            # (e.g. MagicNumber with entity=null lives at module scope, not inside a class).
            # Only fall back to basename for method/class smells where it makes sense.
            is_module_level_smell = normalised_type in (
                "Magic Numbers", "Comments", "Dead Code",
                # C module-level smells (no class/method context in C)
                "Deep Nesting", "Global Variable", "Large Header File",
                "Unsafe Function Usage",
            )
            if (not entity or entity == "unknown") and not is_module_level_smell:
                entity = file_name.replace(".java", "").replace(".py", "").replace(".c", "").replace(".h", "")

            # Build location dict based on smell type
            line = raw.get("line")
            base_name = file_name.replace(".java", "").replace(".py", "").replace(".c", "").replace(".h", "")
            
            # Determine whether entity is a class or method based on smell type
            is_class_smell = normalised_type in ("Large Class", "Lazy Class", "God Class")
            is_method_smell = normalised_type in (
                "Long Method", "Long Parameter List", "Too Many Parameters",
                # C function-level smells
                "Long Function", "Unsafe Function Usage",
            )
            
            # RDP-FIX-02: Build line range [start, end] when CUQA provides both endpoints.
            # has_code_block precondition requires len(lines) >= 2 to evaluate properly.
            # Falls back to [line] (single point) for older CUQA output without end_line.
            start_line = raw.get("start_line", line)
            end_line   = raw.get("end_line", line)
            if start_line and end_line and start_line != end_line:
                lines_range = [start_line, end_line]
            else:
                lines_range = [line] if line else []

            location = {
                "file": file_name,
                "language": language,
                "class": entity if is_class_smell else (None if is_module_level_smell else base_name),
                "method": entity if is_method_smell else (None if (is_class_smell or is_module_level_smell) else base_name),
                "lines": lines_range,
            }
            # Clean up None values
            if location["method"] is None or location["method"] == "unknown":
                location["method"] = base_name if not (is_class_smell or is_module_level_smell) else None

            # RDP-FIX: SpeculativeGenerality — parse base class from message so that
            # has_parent_class precondition passes and "Collapse Hierarchy" can be selected.
            # CUQA message format: "Class 'Foo' extends ['ABC', 'Mixin'] — ..."
            if normalised_type == "Speculative Generality":
                _base_m = re.search(r"extends\s+(\[.*?\])", raw.get("message", ""))
                if _base_m:
                    location["parent_class"] = _base_m.group(1)


            # Build metrics dict — per-method size when available, file-level as fallback.
            # RDP-FIX-01: Use per-method body LOC (end_line - start_line) when CUQA provides
            # start_line/end_line. Using whole-file LOC inflates severity for small methods
            # inside large files (e.g. a 39-line method in a 438-line file got LOC=438).
            if start_line and end_line and start_line != end_line:
                method_loc = end_line - start_line
            else:
                method_loc = file_metrics.get("lines_of_code", 0)

            metrics = {
                "lines_of_code": method_loc,
                "complexity": file_metrics.get("complexity", 1),
                "functions": file_metrics.get("functions", 0),
                "classes": file_metrics.get("classes", 0),
            }
            for k, v in raw.items():
                if k not in ("type", "severity", "line", "message", "entity",
                             "method", "start_line", "end_line") \
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


@app.route("/health", methods=["GET"])
@app.route("/api/health", methods=["GET"])
def health():
    """Simple readiness/liveness endpoint for Azure Container Apps and monitoring."""
    return jsonify({
        "status": "ok",
        "service": "rdp-agent",
        "message": "RDP agent is healthy"
    }), 200


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
