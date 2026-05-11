"""Flask integration surface for SCTVA."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, request

from ..agent import ContractValidationError, SafeCodeTransformationValidationAgent
from .planner_adapter import PlannerAdapter, PlannerAdapterError


def _new_request_id() -> str:
    return "sctva_" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")


def _results_dir() -> Path:
    base_dir = Path(__file__).resolve().parents[2]
    results = base_dir / "results"
    results.mkdir(parents=True, exist_ok=True)
    return results


def _sanitize_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return cleaned or "sctva_run"


def _extract_java_public_type_name(source_code: str) -> Optional[str]:
    match = re.search(
        r"\bpublic\s+(?:class|interface|enum|record)\s+([A-Za-z_][A-Za-z0-9_]*)\b",
        source_code,
    )
    if not match:
        return None
    return match.group(1)


def _save_execution_artifacts(result: Dict[str, Any]) -> Dict[str, Any]:
    results = _results_dir()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    stem = f"refactored_code_{stamp}"

    result_json_path = results / f"{stem}.result.json"
    result_json_path.write_text(
        json.dumps(result, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    artifact_paths: Dict[str, Any] = {
        "results_folder": str(results),
        "result_json": str(result_json_path),
    }

    file_results = result.get("file_results")
    if isinstance(file_results, list) and file_results:
        artifact_paths["file_artifacts"] = []
        for idx, file_result in enumerate(file_results, start=1):
            artifact_paths["file_artifacts"].append(
                _save_artifacts_for_file(results, stem, file_result, idx)
            )
        return artifact_paths

    artifact_paths.update(_save_artifacts_for_file(results, stem, result, 1))
    return artifact_paths


def _save_artifacts_for_file(
    results: Path,
    stem: str,
    file_result: Dict[str, Any],
    index: int,
) -> Dict[str, str]:
    language = str(file_result.get("language", "")).strip().lower()
    file_name = str(file_result.get("file_name") or f"file_{index}").strip()
    file_label = _sanitize_name(Path(file_name).stem or file_name)
    file_stem = f"{stem}_{file_label}"

    refactored_code_text = str(file_result.get("refactored_code", ""))
    refactored_code_text = refactored_code_text.lstrip("\ufeff")

    artifact_paths: Dict[str, str] = {
        "file_name": file_name,
    }

    if language == "python":
        refactored_code_path = results / f"{file_stem}.refactored.py"
        refactored_code_path.write_text(
            refactored_code_text,
            encoding="utf-8",
        )
        artifact_paths["refactored_code"] = str(refactored_code_path)

    # Java: save only compile-ready file where class name matches file name.
    elif language == "java":
        public_type_name = _extract_java_public_type_name(refactored_code_text)
        compile_ready_dir = results / f"{file_stem}.compile_ready"
        compile_ready_dir.mkdir(parents=True, exist_ok=True)
        class_name = public_type_name or "RefactoredOutput"
        compile_ready_path = compile_ready_dir / f"{class_name}.java"
        compile_ready_path.write_text(refactored_code_text, encoding="utf-8")
        artifact_paths["compile_ready_java"] = str(compile_ready_path)
        artifact_paths["refactored_code"] = str(compile_ready_path)

    return artifact_paths


def create_sctva_blueprint() -> Blueprint:
    """Create and return a Blueprint with SCTVA endpoints."""
    bp = Blueprint("sctva_api", __name__)
    agent = SafeCodeTransformationValidationAgent()
    adapter = PlannerAdapter()

    @bp.get("/sctva/health")
    def sctva_health() -> Any:
        return jsonify({"status": "ok", "service": "sctva"}), 200

    @bp.post("/sctva/execute")
    def sctva_execute() -> Any:
        try:
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict):
                return jsonify({"error": "Invalid JSON payload."}), 400
            result = agent.execute(payload)
            try:
                result["saved_artifacts"] = _save_execution_artifacts(result)
            except Exception as exc:
                result["saved_artifacts"] = {
                    "error": f"Failed to persist artifacts: {exc}",
                }
            return jsonify(result), 200
        except ContractValidationError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": f"Internal execution error: {exc}"}), 500

    @bp.post("/sctva/execute_from_rdp")
    def sctva_execute_from_rdp() -> Any:
        try:
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict):
                return jsonify({"error": "Invalid JSON payload."}), 400

            # RDP adapter flow (disabled for manual plan input). Keep for future use.
            # planner_output = payload.get("plan")
            # language = str(payload.get("language", "")).strip().lower()
            # source_code = payload.get("source_code")
            # if not isinstance(planner_output, dict):
            #     return jsonify({"error": "Field 'plan' must be an object."}), 400
            # if language not in {"python", "java"}:
            #     return jsonify({"error": "Field 'language' must be 'python' or 'java'."}), 400
            # if not isinstance(source_code, str) or not source_code.strip():
            #     return jsonify({"error": "Field 'source_code' must be a non-empty string."}), 400
            # request_id = str(payload.get("request_id") or _new_request_id())
            # execution_options = payload.get("execution_options")
            # correlation_id = payload.get("correlation_id") or planner_output.get("plan_id")
            # sctva_request = adapter.build_request_from_rdp(
            #     request_id=request_id,
            #     language=language,
            #     source_code=source_code,
            #     planner_output=planner_output,
            #     execution_options=execution_options,
            #     correlation_id=str(correlation_id),
            # )
            # result = agent.execute(sctva_request)

            result = agent.execute(payload)
            try:
                result["saved_artifacts"] = _save_execution_artifacts(result)
            except Exception as exc:
                result["saved_artifacts"] = {
                    "error": f"Failed to persist artifacts: {exc}",
                }
            return jsonify(result), 200

        except PlannerAdapterError as exc:
            return jsonify({"error": str(exc)}), 422
        except ContractValidationError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": f"Internal execution error: {exc}"}), 500

    return bp
