"""Flask integration surface for SCTVA."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify, request

from ..agent import ContractValidationError, SafeCodeTransformationValidationAgent
from .planner_adapter import PlannerAdapter, PlannerAdapterError


def _new_request_id() -> str:
    return "sctva_" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")


def _remove_legacy_result_artifacts() -> None:
    """Remove old SCTVA disk artifacts without touching unrelated files."""
    results_dir = Path(__file__).resolve().parents[2] / "results"
    if not results_dir.exists() or not results_dir.is_dir():
        return

    for item in results_dir.iterdir():
        if not item.name.startswith("refactored_code_"):
            continue
        if item.is_dir():
            shutil.rmtree(item, ignore_errors=True)
        else:
            item.unlink(missing_ok=True)

    try:
        results_dir.rmdir()
    except OSError:
        pass


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
            _remove_legacy_result_artifacts()
            result = agent.execute(payload)
            _remove_legacy_result_artifacts()
            result["artifact_persistence"] = {
                "mode": "browser_storage",
                "backend_results_folder_disabled": True,
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

            _remove_legacy_result_artifacts()
            result = agent.execute(payload)
            _remove_legacy_result_artifacts()
            result["artifact_persistence"] = {
                "mode": "browser_storage",
                "backend_results_folder_disabled": True,
            }
            return jsonify(result), 200

        except PlannerAdapterError as exc:
            return jsonify({"error": str(exc)}), 422
        except ContractValidationError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": f"Internal execution error: {exc}"}), 500

    return bp
