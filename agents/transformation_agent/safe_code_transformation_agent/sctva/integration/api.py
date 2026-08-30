"""Flask integration surface for SCTVA."""

from __future__ import annotations

import os
import tempfile
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify, request

from ..constants import SUPPORTED_ACTIONS
from ..agent import ContractValidationError, SafeCodeTransformationValidationAgent
from .planner_adapter import (
    PlannerAdapter,
    PlannerAdapterError,
    normalize_sctva_request_payload,
)


def _new_request_id() -> str:
    return "sctva_" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")


def _normalize_execute_from_rdp_payload(
    payload: dict[str, Any],
    *,
    adapter: PlannerAdapter,
) -> dict[str, Any]:
    """Backward-compatible wrapper for the shared request normalizer."""

    normalized_payload, _ = normalize_sctva_request_payload(
        payload,
        adapter=adapter,
    )
    return normalized_payload


def _move_method_integrity_response(
    issues: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a safe, report-shaped response for a lost RDP target."""

    return {
        "success": False,
        "status": "REVIEW_REQUIRED",
        "reason": "RDP_MOVE_METHOD_PARAMETERS_LOST",
        "rollback_occurred": False,
        "transformation_applied": False,
        "normalization_diagnostics": issues,
        "safety_report": {
            "summary": "Transformation requires review before execution.",
            "risk_flags": ["RDP_MOVE_METHOD_PARAMETERS_LOST"],
            "human_messages": [
                "Move Method planner parameters were lost before AST resolution; no source code was changed.",
            ],
            "transformation_log": [
                {
                    "action_type": "move_python_method",
                    "status": "review_required",
                    "reason": "RDP_MOVE_METHOD_PARAMETERS_LOST",
                    "metadata": {"diagnostics": issues},
                    "replacements_count": 0,
                }
            ],
        },
    }


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


def _safe_relative_source_path(value: Any) -> str:
    text = str(value or "").replace("\\", "/").strip()
    if not text or Path(text).is_absolute():
        return ""
    parts = [part for part in text.split("/") if part and part != "."]
    if not parts or any(part == ".." for part in parts):
        return ""
    return "/".join(parts)


def _language_from_source_path(file_path: str) -> str:
    suffix = Path(file_path).suffix.lower()
    if suffix == ".py":
        return "python"
    if suffix in {".c", ".h"}:
        return "c"
    return "java"


def _candidate_temp_roots() -> list[Path]:
    roots = [
        Path(tempfile.gettempdir()),
        *(Path(value) for value in (os.getenv("TEMP"), os.getenv("TMP")) if value),
    ]

    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        roots.append(Path(local_app_data) / "Temp")

    user_profile = os.getenv("USERPROFILE")
    if user_profile:
        roots.append(Path(user_profile) / "AppData" / "Local" / "Temp")

    unique: dict[str, Path] = {}
    for root in roots:
        try:
            unique[str(root.resolve())] = root
        except OSError:
            continue
    return list(unique.values())


def _cuqa_workspace_candidates() -> list[Path]:
    roots: list[Path] = []

    temp_entries: list[Path] = []
    for temp_root in _candidate_temp_roots():
        try:
            temp_entries.extend(temp_root.iterdir())
        except OSError:
            continue

    for base in temp_entries:
        try:
            if not base.is_dir() or not base.name.startswith(("cuqa_", "cuqa_gh_")):
                continue
        except OSError:
            continue
        extracted = base / "extracted"
        candidates = [base]
        try:
            extracted_is_dir = extracted.is_dir()
        except OSError:
            extracted_is_dir = False
        if extracted_is_dir:
            candidates.append(extracted)
            try:
                candidates.extend(child for child in extracted.iterdir() if child.is_dir())
            except OSError:
                pass
        roots.extend(candidates)

    def mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    unique_roots: dict[str, Path] = {}
    for root in roots:
        try:
            unique_roots[str(root.resolve())] = root
        except OSError:
            continue

    return sorted(unique_roots.values(), key=mtime, reverse=True)


def _find_cuqa_workspace_file_by_suffix(root: Path, safe_path: str) -> Path | None:
    basename = safe_path.rsplit("/", 1)[-1]
    suffix = f"/{safe_path.lower()}"

    try:
        candidates = root.rglob(basename)
    except OSError:
        return None

    for candidate in candidates:
        try:
            if not candidate.is_file():
                continue
            resolved_root = root.resolve()
            resolved_candidate = candidate.resolve()
        except OSError:
            continue
        if resolved_root not in resolved_candidate.parents and resolved_candidate != resolved_root:
            continue
        normalized_candidate = str(resolved_candidate).replace("\\", "/").lower()
        if normalized_candidate.endswith(suffix):
            return resolved_candidate

    return None


def _find_cuqa_workspace_file(relative_path: str) -> Path | None:
    safe_path = _safe_relative_source_path(relative_path)
    if not safe_path:
        return None

    safe_parts = safe_path.split("/")
    for root in _cuqa_workspace_candidates():
        candidate = root.joinpath(*safe_parts)
        try:
            resolved_root = root.resolve()
            resolved_candidate = candidate.resolve()
        except OSError:
            continue
        if resolved_root not in resolved_candidate.parents and resolved_candidate != resolved_root:
            continue
        if resolved_candidate.is_file():
            return resolved_candidate

    for root in _cuqa_workspace_candidates():
        found = _find_cuqa_workspace_file_by_suffix(root, safe_path)
        if found is not None:
            return found

    return None


def _find_cuqa_workspace_files(
    relative_paths: list[Any],
    *,
    workspace_roots: list[Path] | None = None,
) -> dict[str, Path]:
    """Resolve many CUQA workspace paths with one workspace scan.

    Large CUQA imports can include hundreds of files. Calling rglob once per
    requested file makes import time grow quickly, so this bulk resolver does
    direct path checks first and scans each candidate workspace only once for
    suffix matches.
    """

    safe_paths: list[str] = []
    seen: set[str] = set()
    for raw_path in relative_paths:
        safe_path = _safe_relative_source_path(raw_path)
        if not safe_path or safe_path in seen:
            continue
        seen.add(safe_path)
        safe_paths.append(safe_path)

    if not safe_paths:
        return {}

    roots = workspace_roots if workspace_roots is not None else _cuqa_workspace_candidates()
    found: dict[str, Path] = {}

    for safe_path in safe_paths:
        safe_parts = safe_path.split("/")
        for root in roots:
            candidate = root.joinpath(*safe_parts)
            try:
                resolved_root = root.resolve()
                resolved_candidate = candidate.resolve()
            except OSError:
                continue
            if resolved_root not in resolved_candidate.parents and resolved_candidate != resolved_root:
                continue
            if resolved_candidate.is_file():
                found[safe_path] = resolved_candidate
                break

    unresolved = [safe_path for safe_path in safe_paths if safe_path not in found]
    if not unresolved:
        return found

    suffixes_by_basename: dict[str, list[tuple[str, str]]] = {}
    for safe_path in unresolved:
        basename = safe_path.rsplit("/", 1)[-1]
        suffixes_by_basename.setdefault(basename.lower(), []).append(
            (safe_path, f"/{safe_path.lower()}")
        )

    for root in roots:
        try:
            candidates = root.rglob("*")
        except OSError:
            continue

        for candidate in candidates:
            basename_entries = suffixes_by_basename.get(candidate.name.lower())
            if not basename_entries:
                continue
            try:
                if not candidate.is_file():
                    continue
                resolved_root = root.resolve()
                resolved_candidate = candidate.resolve()
            except OSError:
                continue
            if resolved_root not in resolved_candidate.parents and resolved_candidate != resolved_root:
                continue

            normalized_candidate = str(resolved_candidate).replace("\\", "/").lower()
            for safe_path, suffix in basename_entries:
                if safe_path not in found and normalized_candidate.endswith(suffix):
                    found[safe_path] = resolved_candidate

            if len(found) == len(safe_paths):
                return found

    return found


def create_sctva_blueprint() -> Blueprint:
    """Create and return a Blueprint with SCTVA endpoints."""
    bp = Blueprint("sctva_api", __name__)
    agent = SafeCodeTransformationValidationAgent()
    adapter = PlannerAdapter()

    @bp.route("/sctva/health", methods=["GET", "OPTIONS"])
    def sctva_health() -> Any:
        if request.method == "OPTIONS":
            return jsonify({"status": "ok"}), 200
        return jsonify(
            {
                "status": "ok",
                "service": "sctva",
                "implementation": "sctva-real-transformers",
                "execution_contract_version": 2,
                "supported_actions": sorted(SUPPORTED_ACTIONS),
                "supported_capabilities": [
                    "line_based_remove_dead_code",
                    "source_range_extract_method",
                    "c_safe_unsafe_function_replacement",
                    "c_global_variable_encapsulation",
                    "cuqa_temp_workspace_source_import",
                    "sctva_internal_refactoring_detector",
                    "multiline_statement_normalization",
                ],
            }
        ), 200

    @bp.route("/sctva/cuqa-sources", methods=["POST", "OPTIONS"])
    def sctva_cuqa_sources() -> Any:
        if request.method == "OPTIONS":
            return jsonify({"status": "ok"}), 200
        try:
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict):
                return jsonify({"error": "Invalid JSON payload."}), 400

            raw_paths = payload.get("file_paths") or payload.get("files") or []
            if not isinstance(raw_paths, list):
                return jsonify({"error": "Field 'file_paths' must be a list."}), 400

            files = []
            missing = []
            max_file_bytes = 5 * 1024 * 1024
            requested_paths = raw_paths[:1000]
            workspace_roots = _cuqa_workspace_candidates()
            resolved_paths = _find_cuqa_workspace_files(
                requested_paths,
                workspace_roots=workspace_roots,
            )

            for raw_path in requested_paths:
                safe_path = _safe_relative_source_path(raw_path)
                if not safe_path:
                    missing.append(str(raw_path or ""))
                    continue

                source_path = resolved_paths.get(safe_path)
                if source_path is None:
                    missing.append(safe_path)
                    continue

                try:
                    if source_path.stat().st_size > max_file_bytes:
                        missing.append(safe_path)
                        continue
                    source_code = source_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    missing.append(safe_path)
                    continue

                files.append(
                    {
                        "file_name": safe_path,
                        "source_code": source_code,
                        "language": _language_from_source_path(safe_path),
                        "source_mode": "raw",
                        "origin": "cuqa_temp_workspace",
                    }
                )

            return jsonify(
                {
                    "files": files,
                    "missing": missing,
                    "imported": len(files),
                    "total": len(raw_paths),
                    "source": "cuqa_temp_workspace",
                    "workspace_candidates_scanned": len(workspace_roots),
                }
            ), 200
        except Exception as exc:
            return jsonify({"error": f"Unable to import CUQA workspace sources: {exc}"}), 500

    @bp.route("/sctva/execute", methods=["POST", "OPTIONS"])
    def sctva_execute() -> Any:
        if request.method == "OPTIONS":
            return jsonify({"status": "ok"}), 200
        try:
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict):
                return jsonify({"error": "Invalid JSON payload."}), 400
            # Both public execution routes accept raw RDP ``steps`` as well as
            # the normalized SCTVA contract.  Normalize before entering the
            # agent so the live route cannot discard nested Move Method data.
            payload, integrity_issues = normalize_sctva_request_payload(
                payload,
                adapter=adapter,
            )
            if integrity_issues:
                return jsonify(_move_method_integrity_response(integrity_issues)), 200
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

    @bp.route("/sctva/execute_from_rdp", methods=["POST", "OPTIONS"])
    def sctva_execute_from_rdp() -> Any:
        if request.method == "OPTIONS":
            return jsonify({"status": "ok"}), 200
        try:
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict):
                return jsonify({"error": "Invalid JSON payload."}), 400

            # Use the exact same normalization and boundary integrity check as
            # /sctva/execute.  The route name must not determine the contract
            # shape seen by the transformation engine.
            sctva_payload, integrity_issues = normalize_sctva_request_payload(
                payload,
                adapter=adapter,
            )
            if integrity_issues:
                return jsonify(_move_method_integrity_response(integrity_issues)), 200

            _remove_legacy_result_artifacts()
            result = agent.execute(sctva_payload)
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
