"""
DIWO Agent REST API Routes
===========================
R26-SE-008 | Bandara S M Y M | IT22277886

All endpoints consumed by the React frontend.

FIXES APPLIED:
  [12] Every route validates its request body with a helper before acting.
       Missing or wrong-typed fields return 400 with a descriptive message.
  [13] Stage guard moved into a reusable _require_stage() helper to avoid
       repeating the same if/return pattern in every handler.
  [14] select_smells validates that selected_ids is a non-empty list of strings.
  [15] plan_decision validates decision enum before DB writes.
  [16] transformation_decision validates decision enum before DB writes.
  [17] start_workflow validates that smells is a list with at least one item
       and that each smell has a required 'type' field.
  [18] complete_workflow accepts an empty body (notes is optional).
  [19] All JSON responses are consistent: {status, message, ...payload}.
"""

import uuid
import json
import io
import os
import subprocess
import tempfile
import shutil
import zipfile
from pathlib import Path
from flask import Blueprint, request, jsonify, send_file

from db.database import (
    create_workflow, get_workflow, update_workflow, list_workflows,
    log_event, get_audit_logs, save_feedback, export_feedback_dataset,
    now_iso,
)
from diwo.orchestrator import (
    generate_refactoring_plan, simulate_transformation,
    compute_metrics_before, compute_metrics_after, next_stage,
    generate_updated_plan_report, build_approved_plan,
    normalize_cuqa_report, cuqa_report_to_smells, filter_cuqa_report,
    detect_primary_language, derive_target_name,
    build_rdp_plan_input, normalize_rdp_plan,
)
from diwo.cuqa_client import (
    CUQAError, cuqa_base_url, fetch_quality_report, probe_cuqa,
)
from diwo.rdp_client import (
    RDPError, generate_plan as rdp_generate_plan, probe_rdp, rdp_base_url,
)

diwo_bp = Blueprint("diwo", __name__)


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

def _err(msg: str, code: int = 400):
    return jsonify({"error": msg}), code


def _parse_json_field(wf: dict, field: str):
    val = wf.get(field)
    if not val:
        return None
    return json.loads(val) if isinstance(val, str) else val


def _require_stage(wf: dict, *expected: str):
    """Return an error response unless the workflow is in one of `expected`.

    Several stages legitimately accept a request from more than one point in the
    workflow — falling back from plan approval to smell review, for instance,
    re-enters /select-smells while the stored stage is still 'plan_approval'.
    """
    if wf["status"] not in expected:
        wanted = " or ".join(f"'{stage}'" for stage in expected)
        return _err(
            f"Expected workflow stage {wanted} but current stage is '{wf['status']}'."
            " Reload the page to sync your session."
        )
    return None


def _build_report_from_smells(smells: list, repo_name: str, selected_ids=None):
    """Build a cquaAgent.json-style report, keeping all files and filtering smells.

    If selected_ids is provided, smells not in selected_ids are excluded from each
    file's code_smells list, but the file itself remains in the report.
    """
    selected_ids = set(selected_ids or [])
    file_map = {}
    file_order = []
    severity_totals = {"high": 0, "medium": 0, "low": 0}

    for smell in smells:
        loc = smell.get("location", {}) or {}
        file_path = loc.get("file") or smell.get("relative_path") or "unknown"
        metrics = smell.get("metrics", {}) or {}

        if file_path not in file_map:
            quality_score = metrics.get("quality_score", smell.get("quality_score", 0))
            file_map[file_path] = {
                "file": Path(file_path).name,
                "language": smell.get("language") or (file_path.split(".")[-1] or "unknown").lower(),
                "metrics": {
                    "filename": Path(file_path).name,
                    "lines_of_code": metrics.get("lines_of_code", 0),
                    "blank_lines": metrics.get("blank_lines", 0),
                    "comment_lines": metrics.get("comment_lines", 0),
                    "functions": metrics.get("functions", 0),
                    "classes": metrics.get("classes", 0),
                },
                "code_smells": [],
                "smell_summary": {"high": 0, "medium": 0, "low": 0},
                "quality_score": quality_score,
                "relative_path": file_path,
            }
            file_order.append(file_path)

        smell_id = smell.get("id")
        include_smell = not selected_ids or smell_id in selected_ids
        if not include_smell:
            continue

        severity = (smell.get("severity") or "low").lower()
        if severity not in ("high", "medium", "low"):
            severity = "low"

        line = smell.get("line")
        if line is None:
            line = (loc.get("lines") or [0, 0])[0]

        file_map[file_path]["code_smells"].append({
            "type": smell.get("type"),
            "message": smell.get("message", ""),
            "line": line,
            "severity": severity,
        })
        file_map[file_path]["smell_summary"][severity] += 1
        severity_totals[severity] += 1

    files = [file_map[path] for path in file_order]
    total_loc = sum(f["metrics"]["lines_of_code"] for f in files)
    total_smells = sum(severity_totals.values())
    avg_quality = (sum(f["quality_score"] for f in files) / max(len(files), 1)) if files else 0

    return {
        "summary": {
            "files_analyzed": len(files),
            "total_lines_of_code": total_loc,
            "total_code_smells": total_smells,
            "smell_severity": severity_totals,
            "average_quality_score": round(avg_quality, 2),
        },
        "files": files,
        "repo_name": repo_name,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Refactored-source archive
# ─────────────────────────────────────────────────────────────────────────────

# Guard rails so a malformed payload cannot exhaust memory building an archive.
MAX_ARCHIVE_FILES = 2000
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024


def _archives_dir() -> Path:
    directory = Path(__file__).parent.parent / "reports" / "archives"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _archive_path(wf_id: str) -> Path:
    """One archive per workflow; a new accept overwrites the previous one."""
    return _archives_dir() / f"{wf_id}.zip"


def _safe_archive_path(value, fallback: str = "file") -> str:
    """Normalize a repo-relative path so extraction cannot escape its folder.

    Drops drive letters, leading slashes and every '..' segment, and keeps the
    remaining folders so 'src/util/Helper.java' extracts back into src/util/.
    """
    text = str(value or "").replace("\\", "/")
    if len(text) > 1 and text[1] == ":":
        text = text[2:]
    parts = [p for p in text.split("/") if p and p not in (".", "..")]
    return "/".join(parts) or fallback


def _build_refactored_archive(wf_id: str, files: list, meta: dict):
    """Zip the final source of each file, preserving its folder structure.

    `files` is a list of {path, content, state} objects — `content` is already
    the code the developer settled on, so a rejected file arrives holding its
    original source and lands in the archive that way.

    Returns (bytes, manifest) or raises ValueError with a reportable message.
    """
    if not isinstance(files, list):
        raise ValueError("'files' must be a list of {path, content} objects.")
    if len(files) > MAX_ARCHIVE_FILES:
        raise ValueError(f"Too many files for one archive (limit {MAX_ARCHIVE_FILES}).")

    entries = []
    used_names = set()
    total_bytes = 0

    for index, item in enumerate(files, start=1):
        if not isinstance(item, dict):
            continue

        content = item.get("content")
        if content is None:
            content = item.get("after") or item.get("refactored_code") or ""
        if not isinstance(content, str) or content == "":
            continue

        total_bytes += len(content.encode("utf-8"))
        if total_bytes > MAX_ARCHIVE_BYTES:
            raise ValueError(f"Archive exceeds the {MAX_ARCHIVE_BYTES // (1024 * 1024)} MB limit.")

        name = _safe_archive_path(
            item.get("path") or item.get("file") or item.get("relative_path"),
            f"file-{index}",
        )

        # Two entries cannot share a name or the archive silently loses one.
        if name in used_names:
            stem, dot, ext = name.rpartition(".")
            base, suffix = (stem, f".{ext}") if dot else (name, "")
            counter = 2
            while f"{base}({counter}){suffix}" in used_names:
                counter += 1
            name = f"{base}({counter}){suffix}"
        used_names.add(name)

        entries.append({
            "path": name,
            "content": content,
            "state": item.get("state") or ("reverted_to_original"
                                           if item.get("decision") == "reject" else "refactored"),
        })

    if not entries:
        raise ValueError("None of the supplied files carried any content to archive.")

    manifest = {
        "workflow_id": wf_id,
        "generated_at": now_iso(),
        **{k: v for k, v in (meta or {}).items() if v is not None},
        "files": [{"path": e["path"], "state": e["state"]} for e in entries],
        "file_count": len(entries),
    }

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for entry in entries:
            archive.writestr(entry["path"], entry["content"])
        archive.writestr("REFACTORING_MANIFEST.json", json.dumps(manifest, indent=2))

    return buffer.getvalue(), manifest


def _store_refactored_archive(wf_id: str, files: list, meta: dict):
    """Build the archive and keep it on disk so it can be downloaded later."""
    payload, manifest = _build_refactored_archive(wf_id, files, meta)
    target = _archive_path(wf_id)
    target.write_bytes(payload)

    return payload, {
        "filename": f"diwo_refactored_{wf_id}.zip",
        "file_count": manifest["file_count"],
        "bytes": len(payload),
        "generated_at": manifest["generated_at"],
        "url": f"/api/workflows/{wf_id}/refactored-archive",
        "files": manifest["files"],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Planning hand-off to the RDP agent
# ─────────────────────────────────────────────────────────────────────────────

def _plan_from_rdp(updated_report: dict, selected: list, target: str, wf_id: str = None):
    """Generate the refactoring plan for the developer's smell selection.

    The updated report — every analysed file, but only the smells the developer
    kept — is forwarded to the RDP agent's POST /generate, which is the agent
    that owns planning. The local generator in orchestrator.py stays only as
    the offline fallback, and the response always says which one produced the
    plan so a fallback is never mistaken for real RDP output.

    Returns (plan, trace, source, warning).
    """
    rdp_input = build_rdp_plan_input(updated_report)

    if not rdp_input["files"]:
        warning = (
            "The selection contains no code smells, so the RDP agent was not called."
        )
        return generate_refactoring_plan(selected, target), {}, "diwo_local_fallback", warning

    try:
        result = rdp_generate_plan(rdp_input)
    except RDPError as exc:
        if wf_id:
            log_event(wf_id, "plan_approval", "rdp_plan_failed",
                      {"rdp_url": rdp_base_url(), "status": exc.status, "reason": exc.message},
                      actor="system")
        return (
            generate_refactoring_plan(selected, target),
            {},
            "diwo_local_fallback",
            exc.message,
        )

    plan = normalize_rdp_plan(result["plan"], rdp_input)

    if wf_id:
        log_event(wf_id, "plan_approval", "rdp_plan_generated", {
            "rdp_url":       rdp_base_url(),
            "plan_id":       plan.get("plan_id"),
            "target":        plan.get("target"),
            "steps":         plan["summary"]["total_steps"],
            "files_sent":    len(rdp_input["files"]),
            "smells_sent":   rdp_input["summary"]["total_code_smells"],
            "smells_skipped": len(result["trace"].get("plan_generation", {}).get("skipped_smells", [])),
        }, actor="system")

    return plan, result["trace"], "rdp_agent", None


def _resolve_selected_ids(all_smells: list, selected_files: list, selected_smells: list):
    """Turn a file-wise and/or smell-wise selection into workflow smell ids.

    File-wise selection takes every smell in the listed files. Smell-wise
    selection is per smell: an entry carrying the smell's own `id` resolves
    directly (that is what the UI sends), and an entry without one is matched on
    file + type + line, falling back to file + type + entity. A smell's `line`
    and its `location.lines[0]` can differ — CUQA reports both `line` and
    `start_line` — so both are indexed, otherwise a descriptor built from the
    report's `line` would never match a smell keyed on `start_line`.
    """
    ids = []

    if selected_files:
        ids.extend([
            smell["id"]
            for smell in all_smells
            if smell.get("location", {}).get("file") in selected_files
        ])

    if selected_smells:
        known_ids = {smell.get("id") for smell in all_smells if smell.get("id")}
        line_map = {}     # (file, type, line)   -> [ids]
        entity_map = {}   # (file, type, entity) -> [ids]

        for smell in all_smells:
            loc = smell.get("location", {}) or {}
            file_path = loc.get("file") or smell.get("relative_path")
            smell_type = smell.get("type")
            lines = loc.get("lines") or []
            candidates = {smell.get("line") or 0, (lines[0] if lines else 0) or 0}
            for line in candidates:
                line_map.setdefault((file_path, smell_type, line), []).append(smell.get("id"))
            if smell.get("entity"):
                entity_map.setdefault(
                    (file_path, smell_type, smell["entity"]), []
                ).append(smell.get("id"))

        for s in selected_smells:
            if not isinstance(s, dict):
                continue

            explicit = s.get("id") or s.get("smell_id")
            if explicit in known_ids:
                ids.append(explicit)
                continue

            file_path = s.get("file") or s.get("relative_path") or s.get("path")
            smell_type = s.get("type")
            matched = line_map.get((file_path, smell_type, s.get("line") or 0))
            if not matched and s.get("entity"):
                matched = entity_map.get((file_path, smell_type, s["entity"]))
            if matched:
                ids.extend(matched)

    return list(dict.fromkeys(ids))


def _resolve_selection(all_smells: list, selected_ids: list,
                       selected_files: list, selected_smells: list):
    """Resolve the developer's selection, preferring explicit smell ids.

    Ids that are not part of this workflow are dropped and the selection is
    re-derived from the files / smell descriptors instead, so a smell-wise
    selection made against a re-filtered report — where a smell's index inside
    its file, and therefore its id, has shifted — is still honoured rather than
    silently resolving to nothing.
    """
    known_ids = {smell.get("id") for smell in all_smells if smell.get("id")}
    resolved = [sid for sid in (selected_ids or []) if sid in known_ids]

    if len(resolved) < len(selected_ids or []) or not resolved:
        resolved.extend(_resolve_selected_ids(all_smells, selected_files, selected_smells))

    return list(dict.fromkeys(resolved))


# ─────────────────────────────────────────────────────────────────────────────
# Workflow Management
# ─────────────────────────────────────────────────────────────────────────────

@diwo_bp.route("/workflows", methods=["GET"])
def list_wf():
    workflows = list_workflows()
    return jsonify([
        {
            "id":         wf["id"],
            "target":     wf["target"],
            "language":   wf["language"],
            "status":     wf["status"],
            "created_at": wf["created_at"],
            "updated_at": wf["updated_at"],
        }
        for wf in workflows
    ])


@diwo_bp.route("/workflows", methods=["POST"])
def start_workflow():
    """
    Start a new workflow.
    Body: { target: str, language: str, smells: [{id, type, severity, ...}] }

    FIX [17]: Validates smells list structure before creating the workflow.
    """
    data = request.get_json(force=True, silent=True)
    if not data:
        return _err("Request body must be valid JSON.")

    target   = data.get("target", "Unknown.java")
    language = data.get("language", "java")
    smells   = data.get("smells")

    # FIX [17]: Validate smells
    if not smells or not isinstance(smells, list):
        return _err("'smells' must be a non-empty list.")
    for idx, s in enumerate(smells):
        if not isinstance(s, dict):
            return _err(f"smells[{idx}] must be an object.")
        if not s.get("type"):
            return _err(f"smells[{idx}] is missing required field 'type'.")

    wf_id, metrics_before = _persist_new_workflow(target, language, smells)

    return jsonify({
        "workflow_id":     wf_id,
        "status":          "smell_review",
        "message":         "Workflow started. Developer can now review detected smells.",
        "metrics_before":  metrics_before,
    }), 201


def _persist_new_workflow(target: str, language: str, smells: list,
                          source: str = "client", cuqa_report: dict = None):
    """Create the workflow row, seed metrics_before, and log the start event.

    `cuqa_report` is stored verbatim when the workflow was seeded from the CUQA
    agent, so the updated report can later be produced by filtering it instead
    of rebuilding one from the flattened smells.
    """
    wf_id          = f"wf_{uuid.uuid4().hex[:10]}"
    metrics_before = compute_metrics_before(smells)

    create_workflow(wf_id, target, language, smells)
    update_workflow(
        wf_id,
        metrics_before_json=json.dumps(metrics_before),
        cuqa_report_json=json.dumps(cuqa_report) if cuqa_report else None,
    )
    log_event(wf_id, "smell_review", "workflow_started",
              {"target": target, "language": language,
               "smell_count": len(smells), "source": source})

    return wf_id, metrics_before


# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 – CUQA Agent ingestion (live quality report)
# ─────────────────────────────────────────────────────────────────────────────

def _cuqa_error_response(exc: CUQAError):
    """Pass CUQA's own status through so the UI can tell 'not running' from
    'running but no repository loaded'."""
    status = exc.status if 400 <= exc.status < 600 else 502
    payload = {
        "error":     exc.message,
        "cuqa_url":  cuqa_base_url(),
        "reachable": exc.status != 503,
    }
    return jsonify(payload), status


@diwo_bp.route("/cuqa/status", methods=["GET"])
def cuqa_status():
    """Is the CUQA agent up, and does it have a repository loaded?"""
    return jsonify(probe_cuqa())


@diwo_bp.route("/rdp/status", methods=["GET"])
def rdp_status():
    """Is the RDP agent up? Planning is forwarded to it at POST /generate."""
    return jsonify(probe_rdp())


@diwo_bp.route("/cuqa/quality-report", methods=["GET", "POST"])
def cuqa_quality_report():
    """
    Proxy the CUQA agent's POST /api/quality-report (default localhost:8080)
    and return it in the shape the DIWO frontend renders.

    Body / query (optional): { file_path: "relative/path/File.py" }
    Omit file_path to report on the whole loaded workspace.
    """
    data = request.get_json(force=True, silent=True) or {}
    file_path = data.get("file_path") or request.args.get("file_path")

    try:
        payload = fetch_quality_report(file_path)
    except CUQAError as exc:
        return _cuqa_error_response(exc)

    try:
        report = normalize_cuqa_report(payload)
    except ValueError as exc:
        return _err(f"Unexpected CUQA response shape: {exc}", 502)

    smells = cuqa_report_to_smells(report)

    return jsonify({
        "status":      "ok",
        "source":      "cuqa",
        "cuqa_url":    cuqa_base_url(),
        "report_type": report.get("report_type", "repository"),
        "report":      report,
        "smells":      smells,
        "smell_count": len(smells),
        "language":    detect_primary_language(report),
    })


@diwo_bp.route("/workflows/from-cuqa", methods=["POST"])
def start_workflow_from_cuqa():
    """
    Start a DIWO workflow from the CUQA agent's live quality report instead of
    a client-supplied smell list.

    Body (all optional):
      { file_path?: str, target?: str, language?: str }

    Returns the same fields as POST /workflows plus the CUQA report itself, so
    the frontend can render the real Agent 1 output in the Code Smell Review
    stage without a second round trip.
    """
    data = request.get_json(force=True, silent=True) or {}

    try:
        payload = fetch_quality_report(data.get("file_path"))
    except CUQAError as exc:
        return _cuqa_error_response(exc)

    try:
        report = normalize_cuqa_report(payload)
    except ValueError as exc:
        return _err(f"Unexpected CUQA response shape: {exc}", 502)

    smells = cuqa_report_to_smells(report)
    if not smells:
        return _err(
            "The CUQA quality report contains no code smells, so there is nothing "
            "to refactor. Load a repository with detectable smells in the CUQA agent.",
            400,
        )

    target   = data.get("target") or derive_target_name(report)
    language = data.get("language") or detect_primary_language(report)

    wf_id, metrics_before = _persist_new_workflow(
        target, language, smells, source="cuqa", cuqa_report=report
    )
    log_event(wf_id, "smell_review", "cuqa_report_ingested", {
        "cuqa_url":       cuqa_base_url(),
        "report_type":    report.get("report_type"),
        "repo_name":      report.get("repo_name"),
        "files_analyzed": report.get("summary", {}).get("files_analyzed", 0),
        "smell_count":    len(smells),
    }, actor="system")

    return jsonify({
        "workflow_id":    wf_id,
        "status":         "smell_review",
        "source":         "cuqa",
        "cuqa_url":       cuqa_base_url(),
        "target":         target,
        "language":       language,
        "report":         report,
        "smells":         smells,
        "smell_count":    len(smells),
        "metrics_before": metrics_before,
        "message": (
            f"Workflow started from the CUQA quality report: "
            f"{report.get('summary', {}).get('files_analyzed', 0)} file(s), {len(smells)} smell(s)."
        ),
    }), 201


@diwo_bp.route("/workflows/<wf_id>", methods=["GET"])
def get_wf(wf_id):
    wf = get_workflow(wf_id)
    if not wf:
        return _err("Workflow not found.", 404)

    return jsonify({
        "id":                    wf["id"],
        "target":                wf["target"],
        "language":              wf["language"],
        "status":                wf["status"],
        "created_at":            wf["created_at"],
        "updated_at":            wf["updated_at"],
        "smells":                _parse_json_field(wf, "smells_json"),
        "selected_smells":       _parse_json_field(wf, "selected_smells_json"),
        "updated_smells":        _parse_json_field(wf, "updated_smells_json"),
        "planning_input":        _parse_json_field(wf, "planning_input_json"),
        "plan":                  _parse_json_field(wf, "plan_json"),
        "transformation_result": _parse_json_field(wf, "transformation_result_json"),
        "metrics_before":        _parse_json_field(wf, "metrics_before_json"),
        "metrics_after":         _parse_json_field(wf, "metrics_after_json"),
    })


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 – Smell Selection
# ─────────────────────────────────────────────────────────────────────────────

def _build_smell_selection_payload(wf, selected_ids, selected_files=None):
    """Build the updated smell report and planning input without mutating the DB."""
    all_smells = _parse_json_field(wf, "smells_json") or []
    selected = [s for s in all_smells if s["id"] in selected_ids]
    excluded = [s for s in all_smells if s["id"] not in selected_ids]

    updated_smells = []
    for s in all_smells:
        upd = dict(s)
        upd["selected"] = s["id"] in selected_ids
        if upd["selected"]:
            upd["selected_at"] = now_iso()
        updated_smells.append(upd)

    # Prefer filtering the stored CUQA report: it keeps the exact shape
    # /api/cuqa/quality-report serves, so the report that reaches the RDP agent
    # carries the same metrics, quality scores and per-smell fields CUQA
    # produced. _build_report_from_smells is the fallback for workflows created
    # from a client-supplied smell list, which never had a report to store.
    stored_report = _parse_json_field(wf, "cuqa_report_json")
    if stored_report:
        updated_report = filter_cuqa_report(stored_report, selected_ids)
    else:
        updated_report = _build_report_from_smells(
            all_smells, wf.get("target"), selected_ids=selected_ids
        )
    updated_report["workflow_id"] = wf.get("id")
    updated_report["generated_at"] = now_iso()

    planning_input = {
        "workflow_id": wf.get("id"),
        "target": wf.get("target"),
        "language": wf.get("language"),
        "updated_smells": updated_smells,
        "selected_smells": selected,
        "generated_at": now_iso(),
    }

    return {
        "all_smells": all_smells,
        "selected": selected,
        "excluded": excluded,
        "updated_smells": updated_smells,
        "updated_report": updated_report,
        "planning_input": planning_input,
    }


@diwo_bp.route("/workflows/<wf_id>/smell-selection-pass", methods=["POST"])
def smell_selection_pass(wf_id):
    """Preview the updated code smell JSON without advancing the workflow.

    Report only — no plan. The Code Smell Review stage calls this to show what
    the selection looks like, then the same selection goes through
    /select-smells, which is where the RDP agent is called exactly once. This
    endpoint deliberately does not plan: doing so would put two POSTs to
    /generate in every smell-selection flow.
    """
    wf = get_workflow(wf_id)
    if not wf:
        return _err("Workflow not found.", 404)

    data = request.get_json(force=True, silent=True) or {}

    selected_ids = data.get("selected_ids") or []
    selected_files = data.get("selected_files") or data.get("selected_file_paths") or []
    selected_smells = data.get("selected_smells") or []
    selection_mode = data.get("selection_mode") or ("smell" if selected_ids and not selected_files else "file")

    if selected_ids and not isinstance(selected_ids, list):
        return _err("'selected_ids' must be a list of smell ID strings.")
    if selected_files and not isinstance(selected_files, list):
        return _err("'selected_files' must be a list of file paths if provided.")
    if selected_smells and not isinstance(selected_smells, list):
        return _err("'selected_smells' must be a list if provided.")

    selected_ids = _resolve_selection(
        _parse_json_field(wf, "smells_json") or [],
        selected_ids,
        selected_files,
        selected_smells,
    )

    if not selected_ids:
        return _err("No smell IDs could be resolved from the selection. Send 'selected_ids', 'selected_files', or 'selected_smells'.")

    payload = _build_smell_selection_payload(wf, selected_ids, selected_files)

    # What /select-smells would forward to the RDP agent, so the developer can
    # confirm the selection before committing to it. Built, not sent.
    payload["rdp_plan_input"] = build_rdp_plan_input(payload["updated_report"])
    payload["status"] = "smell_review"
    payload["selection_mode"] = selection_mode
    payload["selected_ids"] = selected_ids
    return jsonify(payload)


@diwo_bp.route("/workflows/<wf_id>/save-updated-report", methods=["POST"])
def save_updated_report(wf_id):
    """Save the updated code smell report to disk as a JSON file."""
    wf = get_workflow(wf_id)
    if not wf:
        return _err("Workflow not found.", 404)

    data = request.get_json(force=True, silent=True) or {}
    updated_report = data.get("updated_report")
    
    if not updated_report:
        return _err("'updated_report' object is required in request body.", 400)
    if not isinstance(updated_report, dict):
        return _err("'updated_report' must be a JSON object.", 400)

    try:
        # Create reports directory if it doesn't exist
        backend_dir = Path(__file__).parent.parent
        reports_dir = backend_dir / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)

        # Generate filename with workflow ID and timestamp
        timestamp = now_iso().replace(":", "-").replace(".", "-")
        filename = f"updated_report_{wf_id}_{timestamp}.json"
        file_path = reports_dir / filename

        # Write the report to disk
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(updated_report, f, indent=2)

        return jsonify({
            "status": "success",
            "message": f"Report saved successfully",
            "file_path": str(file_path),
            "file_name": filename,
            "workflow_id": wf_id,
        }), 200

    except Exception as e:
        return _err(f"Failed to save report: {str(e)}", 500)


@diwo_bp.route("/workflows/<wf_id>/reset-to-smell-review", methods=["POST"])
def reset_to_smell_review(wf_id):
    """Send the workflow back to Stage 1 after a fallback from plan approval.

    The plan review screen's "Fallback to Smell Review" only moved the frontend;
    the workflow row stayed at 'plan_approval', so the next /select-smells was
    refused by the stage guard. This clears the plan and the stored selection so
    the developer starts the stage from the full CUQA report again.
    """
    wf = get_workflow(wf_id)
    if not wf:
        return _err("Workflow not found.", 404)

    previous = wf["status"]
    if previous == "smell_review":
        return jsonify({"status": "smell_review", "changed": False,
                        "message": "Workflow is already in smell review."})

    data = request.get_json(force=True, silent=True) or {}
    reason = (data.get("feedback") or {}).get("reason") or data.get("reason") or "Developer fell back to smell review"

    update_workflow(
        wf_id,
        status="smell_review",
        plan_json=None,
        selected_smells_json=None,
        updated_smells_json=None,
        planning_input_json=None,
    )
    log_event(wf_id, "smell_review", "reset_to_smell_review",
              {"from_stage": previous, "reason": reason})

    return jsonify({
        "status": "smell_review",
        "changed": True,
        "from_stage": previous,
        "message": "Workflow reset to smell review. The full CUQA report is selectable again.",
    })


@diwo_bp.route("/workflows/<wf_id>/select-smells", methods=["POST"])
def select_smells(wf_id):
    """
    Developer submits the smell IDs they want to address.
    Body: { selected_ids: [str], feedback?: { reason?: str } }

    FIX [13,14]: Stage guard + list validation before any DB write.

    'plan_approval' is accepted as well as 'smell_review': the plan approval
    screen can fall back to smell review, and re-submitting a selection from
    there simply replaces the plan. Rejecting it would strand the developer with
    a stage error on a screen that offers the fallback button in the first place.
    """
    wf = get_workflow(wf_id)
    if not wf:
        return _err("Workflow not found.", 404)

    err = _require_stage(wf, "smell_review", "plan_approval")
    if err:
        return err

    replanning = wf["status"] == "plan_approval"

    data = request.get_json(force=True, silent=True) or {}

    # FIX [14]: Validate selected_ids, but also allow selected file paths as a fallback.
    selected_ids = data.get("selected_ids")
    selected_files = data.get("selected_files") or data.get("selected_file_paths") or []
    selected_smells = data.get("selected_smells") or []
    selection_mode = data.get("selection_mode") or ("file" if selected_files else "smell")

    if selected_ids is not None:
        if not isinstance(selected_ids, list):
            return _err("'selected_ids' must be a list of smell ID strings.")
        if not all(isinstance(sid, str) for sid in selected_ids):
            return _err("Each element of 'selected_ids' must be a string.")
    else:
        selected_ids = []

    if selected_files and not isinstance(selected_files, list):
        return _err("'selected_files' must be a list of file paths if provided.")

    if selected_smells and not isinstance(selected_smells, list):
        return _err("'selected_smells' must be a list if provided.")

    # Explicit ids win; anything unresolved falls back to the selected files or
    # the per-smell descriptors (smell-wise selection sends no files, so the
    # deselected smells of a partially selected file are not pulled back in).
    selected_ids = _resolve_selection(
        _parse_json_field(wf, "smells_json") or [],
        selected_ids,
        selected_files,
        selected_smells,
    )

    if not selected_ids:
        return _err("No smell IDs could be resolved from the selection. Send 'selected_ids', 'selected_files', or 'selected_smells'.")

    feedback = data.get("feedback", {})

    payload = _build_smell_selection_payload(wf, selected_ids, selected_files)
    all_smells = payload["all_smells"]
    selected = payload["selected"]
    excluded = payload["excluded"]
    updated_smells = payload["updated_smells"]
    planning_input = payload["planning_input"]

    if not selected:
        return _err("None of the provided selected_ids matched known smells in this workflow.")

    update_workflow(wf_id, status="smell_selection",
                    selected_smells_json=json.dumps(selected))
    log_event(wf_id, "smell_selection", "smells_selected",
              {"selected": selected_ids, "excluded": [s["id"] for s in excluded],
               "selection_mode": selection_mode,
               "replanned_after_fallback": replanning,
               "selected_files": sorted({
                   s.get("location", {}).get("file") for s in selected
                   if s.get("location", {}).get("file")
               })})

    for s in excluded:
        save_feedback(wf_id, "smell_selection", "smell_excluded",
                      smell_type=s.get("type"), severity=s.get("severity"),
                      reason=feedback.get("reason", "Developer choice"),
                      accepted=False)

    # Forward the updated report — every file, but only the smells the
    # developer kept — to the RDP agent, which owns plan generation.
    plan, trace, plan_source, plan_warning = _plan_from_rdp(
        payload["updated_report"], selected, wf["target"], wf_id=wf_id
    )

    # plan_full_json keeps the agent's plan before approval trims it down to the
    # approved steps, so a rollback from transformation can offer every step for
    # re-selection instead of only the ones approved last time.
    plan_serialized = json.dumps(plan)
    update_workflow(wf_id, status="plan_approval",
                    plan_json=plan_serialized, plan_full_json=plan_serialized)
    log_event(wf_id, "plan_approval", "plan_generated",
              {"plan_id": plan.get("plan_id"),
               "steps": plan["summary"]["total_steps"],
               "source": plan_source},
              actor="system")

    return jsonify({
        "status":          "plan_approval",
        "selected_count":  len(selected),
        "excluded_count":  len(excluded),
        "plan":            plan,
        # RDP's decision trace: impact / risk / MCDA scores live here, not on
        # the steps, so the approval page needs it to explain each choice.
        "trace":           trace,
        "plan_source":     plan_source,
        "plan_warning":    plan_warning,
        "rdp_url":         rdp_base_url(),
        "selected_ids":    selected_ids,
        "selected_files":   selected_files,
        "selection_mode":   selection_mode,
        "updated_report":   payload["updated_report"],
        "planning_input":   planning_input,
        "message": (
            "Refactoring plan generated by the RDP agent. Please review and approve."
            if plan_source == "rdp_agent"
            else f"RDP agent unavailable ({plan_warning}); showing a local fallback plan."
        ),
    })


# ─────────────────────────────────────────────────────────────────────────────
# Stage 3 – Plan Approval
# ─────────────────────────────────────────────────────────────────────────────

@diwo_bp.route("/workflows/<wf_id>/plan-preference-update", methods=["POST"])
def plan_preference_update(wf_id):
    """
    Regenerate the planning report based on developer decisions/preferences.
    Body: {
      decisions: {"1": "approve", "2": "reject", ...},
      preferences?: {
        risk_tolerance?: "conservative"|"balanced"|"aggressive",
        impact_focus?: "low"|"medium"|"high",
        preferred_refactorings?: [str]
      }
    }
    """
    wf = get_workflow(wf_id)
    if not wf:
        return _err("Workflow not found.", 404)

    err = _require_stage(wf, "plan_approval")
    if err:
        return err

    data = request.get_json(force=True, silent=True) or {}
    decisions = data.get("decisions") or {}
    preferences = data.get("preferences") or {}

    if not isinstance(decisions, dict):
        return _err("'decisions' must be an object keyed by step_id.")
    if not isinstance(preferences, dict):
        return _err("'preferences' must be an object.")

    current_plan = _parse_json_field(wf, "plan_json") or {}
    if not current_plan or not isinstance(current_plan.get("steps"), list):
        return _err("No plan found for this workflow.", 400)

    updated_plan = generate_updated_plan_report(current_plan, decisions, preferences)

    update_workflow(wf_id, plan_json=json.dumps(updated_plan))
    log_event(
        wf_id,
        "plan_approval",
        "plan_regenerated_by_preferences",
        {
            "decisions_count": len(decisions),
            "risk_tolerance": preferences.get("risk_tolerance", "balanced"),
            "impact_focus": preferences.get("impact_focus", "high"),
            "steps_after": len(updated_plan.get("steps", [])),
        },
    )

    return jsonify({
        "status": "plan_approval",
        "message": "Updated planning report generated using developer preferences.",
        "updated_planning_report": updated_plan,
    })

@diwo_bp.route("/workflows/<wf_id>/reset-to-plan-approval", methods=["POST"])
def reset_to_plan_approval(wf_id):
    """Send the workflow back to Stage 2 after a rollback from transformation.

    Approval reduces plan_json to the approved steps, so simply flipping the
    stage back would re-open plan review with the rejected steps already gone
    and no way to reinstate them. plan_full_json — the plan as the RDP agent
    produced it — is restored instead, and the simulated transformation result
    is dropped so nothing stale is carried into the next run.
    """
    wf = get_workflow(wf_id)
    if not wf:
        return _err("Workflow not found.", 404)

    previous = wf["status"]
    full_plan = _parse_json_field(wf, "plan_full_json")
    plan = full_plan or _parse_json_field(wf, "plan_json")

    if not plan:
        return _err("This workflow has no plan to go back to.", 400)

    if previous == "plan_approval":
        return jsonify({"status": "plan_approval", "changed": False, "plan": plan,
                        "message": "Workflow is already in plan approval."})

    data = request.get_json(force=True, silent=True) or {}
    reason = (data.get("feedback") or {}).get("reason") or data.get("reason") or "Developer rolled back to plan approval"

    update_workflow(
        wf_id,
        status="plan_approval",
        plan_json=json.dumps(plan),
        transformation_result_json=None,
        metrics_after_json=None,
    )
    log_event(wf_id, "plan_approval", "reset_to_plan_approval",
              {"from_stage": previous, "reason": reason,
               "restored_steps": len(plan.get("steps") or []),
               "restored_full_plan": bool(full_plan)})

    return jsonify({
        "status": "plan_approval",
        "changed": True,
        "from_stage": previous,
        "plan": plan,
        "restored_full_plan": bool(full_plan),
        "message": "Workflow reset to plan approval. Re-select the steps and forward them again.",
    })


@diwo_bp.route("/workflows/<wf_id>/plan-decision", methods=["POST"])
def plan_decision(wf_id):
    """
    Body: { decision: 'approve'|'reject'|'modify', modified_steps?: [...], feedback?: {...} }

    FIX [13,15]: Stage guard + decision enum validation.

    'transformation' is accepted as well as 'plan_approval': Stage 3 offers a
    rollback to plan review, and re-approving from there must not be refused
    just because the stage reset did not reach the backend.
    """
    wf = get_workflow(wf_id)
    if not wf:
        return _err("Workflow not found.", 404)

    err = _require_stage(wf, "plan_approval", "transformation")
    if err:
        return err

    data = request.get_json(force=True, silent=True) or {}

    # FIX [15]: Validate decision enum
    decision = data.get("decision")
    if decision not in ("approve", "reject", "modify"):
        return _err("'decision' must be one of: 'approve', 'reject', 'modify'.")

    feedback = data.get("feedback", {})
    plan     = _parse_json_field(wf, "plan_json") or {}

    # ── Reject ───────────────────────────────────────────────────────────────
    if decision == "reject":
        update_workflow(wf_id, status="rolled_back")
        log_event(wf_id, "plan_approval", "plan_rejected",
                  {"reason": feedback.get("reason", "No reason given")})
        save_feedback(wf_id, "plan_approval", "plan_rejected",
                      reason=feedback.get("reason"), rating=feedback.get("rating"),
                      accepted=False)
        return jsonify({"status": "rolled_back", "message": "Plan rejected. Workflow terminated."})

    # ── Modify ───────────────────────────────────────────────────────────────
    if decision == "modify":
        decisions = data.get("decisions")
        modified_steps = data.get("modified_steps")

        if isinstance(decisions, dict) and decisions:
            # Preferred: the backend derives the approved plan itself, so the
            # report the Transformation Agent receives cannot disagree with the
            # verdicts recorded in the audit trail.
            plan = build_approved_plan(plan, decisions)
        elif modified_steps is not None:
            if not isinstance(modified_steps, list):
                return _err("'modified_steps' must be a list of step objects.")
            kept = {s.get("step_id") for s in modified_steps if isinstance(s, dict)}
            plan = build_approved_plan(
                plan,
                {s.get("step_id"): ("approve" if s.get("step_id") in kept else "reject")
                 for s in plan.get("steps") or []},
            )

        update_workflow(wf_id, plan_json=json.dumps(plan))
        log_event(wf_id, "plan_approval", "plan_modified", {
            "steps_after":  len(plan.get("steps") or []),
            "approved_ids": (plan.get("approval") or {}).get("approved_step_ids"),
            "rejected_ids": (plan.get("approval") or {}).get("rejected_step_ids"),
        })
        save_feedback(wf_id, "plan_approval", "plan_modified",
                      reason=feedback.get("reason"), rating=feedback.get("rating"),
                      accepted=True)

        # One feedback row per rejected step — a step-level rejection is a
        # stronger training signal than the session-level approval.
        for step in (plan.get("approval") or {}).get("rejected_step_ids") or []:
            save_feedback(wf_id, "plan_approval", "plan_step_rejected",
                          reason=f"Developer rejected plan step {step}.",
                          accepted=False)

        return jsonify({
            "status":  "plan_approval",
            "plan":    plan,
            "message": "Plan reduced to the approved steps. Please approve to proceed.",
        })

    # ── Approve → trigger transformation ─────────────────────────────────────
    # An approve can carry the decisions directly, so a caller that never sent
    # a separate 'modify' still gets an approved-only plan persisted.
    approve_decisions = data.get("decisions")
    if isinstance(approve_decisions, dict) and approve_decisions:
        plan = build_approved_plan(plan, approve_decisions)
        update_workflow(wf_id, plan_json=json.dumps(plan))

    log_event(wf_id, "plan_approval", "plan_approved",
              {"plan_id": plan.get("plan_id"),
               "steps": len(plan.get("steps") or []),
               "approved_ids": (plan.get("approval") or {}).get("approved_step_ids")})
    save_feedback(wf_id, "plan_approval", "plan_approved",
                  reason=feedback.get("reason"), rating=feedback.get("rating"),
                  accepted=True)

    tr             = simulate_transformation(plan, wf["language"])
    metrics_before = _parse_json_field(wf, "metrics_before_json") or {}
    selected       = _parse_json_field(wf, "selected_smells_json") or []
    resolved       = tr["steps_passed"]
    metrics_after  = compute_metrics_after(metrics_before, resolved, len(selected))

    update_workflow(wf_id,
                    status="transformation",
                    transformation_result_json=json.dumps(tr),
                    metrics_after_json=json.dumps(metrics_after))
    log_event(wf_id, "transformation", "transformation_completed",
              {"status": tr["status"], "passed": tr["steps_passed"], "failed": tr["steps_failed"]},
              actor="system")

    return jsonify({
        "status":               "transformation",
        # The approved-only plan report. This is what the Safe Transformation
        # Agent must execute — Stage 3 posts it to /sctva/execute, so a
        # rejected step never reaches the transformer.
        "approved_plan":        plan,
        "transformation_result": tr,
        "refactored_code":      tr.get("refactored_code", ""),
        "diff_rows":            tr.get("diff_rows", []),
        "files":                tr.get("files", []),
        "metrics_after":        metrics_after,
        "message":              "Plan approved. Forward the approved plan to the Transformation Agent.",
    })


# ─────────────────────────────────────────────────────────────────────────────
# Stage 4 – Transformation Decision (Accept / Rollback)
# ─────────────────────────────────────────────────────────────────────────────

@diwo_bp.route("/workflows/<wf_id>/transformation-decision", methods=["POST"])
def transformation_decision(wf_id):
    """
    Body: {
      decision: 'accept'|'rollback',
      accepted_files?: [path],   # kept as refactored
      rejected_files?: [path],   # reverted to their original source
      written_files?:  [path],   # everything actually written out
      files?: [{ path, content, state? }],   # final source of each file
      download?: 'zip',          # respond with the archive instead of JSON
      feedback?: {...}
    }

    The per-file lists come from the developer's Accept/Reject verdict in the
    transformation stage. A rejected file is written back as its original
    source, so recording the split is what makes the audit trail say which
    files were actually refactored and which were reverted.

    When `files` carries the final source of each file, the archive is built
    and kept on disk. The JSON response then points at it via
    `archive.url` (GET /api/workflows/<id>/refactored-archive), or send
    `download: "zip"` to get the archive bytes straight back from this call.

    FIX [13,16]: Stage guard + decision enum validation.
    """
    wf = get_workflow(wf_id)
    if not wf:
        return _err("Workflow not found.", 404)

    err = _require_stage(wf, "transformation")
    if err:
        return err

    data = request.get_json(force=True, silent=True) or {}

    # FIX [16]: Validate decision enum
    decision = data.get("decision")
    if decision not in ("accept", "rollback"):
        return _err("'decision' must be 'accept' or 'rollback'.")

    feedback = data.get("feedback", {})

    if decision == "rollback":
        tr          = _parse_json_field(wf, "transformation_result_json") or {}
        snapshot_id = tr.get("snapshot_id", "unknown")
        update_workflow(wf_id, status="rolled_back")
        log_event(wf_id, "transformation", "rollback_triggered",
                  {"snapshot_id": snapshot_id, "reason": feedback.get("reason")})
        save_feedback(wf_id, "transformation", "rollback_triggered",
                      reason=feedback.get("reason"), rating=feedback.get("rating"),
                      accepted=False)
        return jsonify({
            "status":  "rolled_back",
            "message": f"Rolled back to snapshot {snapshot_id}.",
        })

    # Accept → move to comparison
    def _paths(key):
        value = data.get(key) or []
        return [str(p) for p in value if isinstance(p, (str, int))] if isinstance(value, list) else []

    accepted_files = _paths("accepted_files")
    rejected_files = _paths("rejected_files")
    written_files  = _paths("written_files") or accepted_files

    tr = _parse_json_field(wf, "transformation_result_json") or {}
    tr["file_decisions"] = {
        "accepted": accepted_files,
        "rejected_reverted": rejected_files,
        "written": written_files,
    }

    # Build the downloadable archive when the caller supplied file contents.
    archive_bytes = None
    archive_info = None
    archive_error = None
    supplied_files = data.get("files")

    if supplied_files:
        try:
            archive_bytes, archive_info = _store_refactored_archive(
                wf_id,
                supplied_files,
                {
                    "target": wf.get("target"),
                    "language": wf.get("language"),
                    "accepted_files": accepted_files,
                    "rejected_files": rejected_files,
                },
            )
            tr["archive"] = archive_info
        except ValueError as exc:
            archive_error = str(exc)
        except OSError as exc:
            archive_error = f"Could not write the archive to disk: {exc}"

    update_workflow(wf_id,
                    status="comparison",
                    transformation_result_json=json.dumps(tr))

    log_event(wf_id, "comparison", "transformation_accepted",
              {"rating": feedback.get("rating"),
               "accepted_files": accepted_files,
               "rejected_files": rejected_files,
               "written_files": written_files})
    save_feedback(wf_id, "comparison", "transformation_accepted",
                  reason=feedback.get("reason"), rating=feedback.get("rating"),
                  accepted=True)

    # One feedback row per reverted file: the Feedback Manager trains on
    # rejections, and a file-level reject is a stronger signal than the
    # session-level accept above.
    for path in rejected_files:
        log_event(wf_id, "comparison", "refactoring_reverted", {"file": path})
        save_feedback(wf_id, "comparison", "refactoring_reverted",
                      reason=f"Developer rejected the refactoring of {path}; "
                             "file reverted to its original source.",
                      accepted=False)

    if archive_info:
        log_event(wf_id, "comparison", "archive_built",
                  {"file_count": archive_info["file_count"], "bytes": archive_info["bytes"]},
                  actor="system")
    elif archive_error:
        log_event(wf_id, "comparison", "archive_failed", {"reason": archive_error}, actor="system")

    # `download: "zip"` returns the archive itself, for callers that want the
    # file straight from this request rather than a follow-up GET.
    if str(data.get("download") or "").lower() == "zip":
        if archive_bytes is None:
            return _err(
                archive_error or
                "Send the final source of each file in 'files' to download an archive.",
                400,
            )
        return send_file(
            io.BytesIO(archive_bytes),
            mimetype="application/zip",
            as_attachment=True,
            download_name=archive_info["filename"],
        )

    metrics_before = _parse_json_field(wf, "metrics_before_json")
    metrics_after  = _parse_json_field(wf, "metrics_after_json")

    return jsonify({
        "status":          "comparison",
        "metrics_before":  metrics_before,
        "metrics_after":   metrics_after,
        "accepted_files":  accepted_files,
        "rejected_files":  rejected_files,
        "written_files":   written_files,
        "archive":         archive_info,
        "archive_error":   archive_error,
        "message":         "Changes accepted. View comparison report.",
    })


@diwo_bp.route("/workflows/<wf_id>/refactored-archive", methods=["GET"])
def download_refactored_archive(wf_id):
    """Download the ZIP built when the transformation was accepted.

    Entry names are the repo-relative paths, so extracting the archive
    reproduces the project's folder structure. Rejected files are present as
    their original source, matching what was recorded in file_decisions.
    """
    wf = get_workflow(wf_id)
    if not wf:
        return _err("Workflow not found.", 404)

    path = _archive_path(wf_id)
    if not path.exists():
        return _err(
            "No archive has been built for this workflow. Accept the transformation with the "
            "file contents in 'files' first.",
            404,
        )

    tr = _parse_json_field(wf, "transformation_result_json") or {}
    filename = (tr.get("archive") or {}).get("filename") or f"diwo_refactored_{wf_id}.zip"

    return send_file(path, mimetype="application/zip",
                     as_attachment=True, download_name=filename)


# ─────────────────────────────────────────────────────────────────────────────
# Stage 5 – Complete Workflow
# ─────────────────────────────────────────────────────────────────────────────

@diwo_bp.route("/workflows/<wf_id>/complete", methods=["POST"])
def complete_workflow(wf_id):
    """
    FIX [13,18]: Stage guard; body is optional (notes may be absent).
    """
    wf = get_workflow(wf_id)
    if not wf:
        return _err("Workflow not found.", 404)

    err = _require_stage(wf, "comparison")
    if err:
        return err

    # FIX [18]: Accept empty body gracefully
    data  = request.get_json(force=True, silent=True) or {}
    notes = data.get("notes", "")

    update_workflow(wf_id, status="completed")
    log_event(wf_id, "completed", "workflow_completed", {"final_notes": notes})

    return jsonify({"status": "completed", "message": "Workflow successfully completed."})


# ─────────────────────────────────────────────────────────────────────────────
# Audit Logs
# ─────────────────────────────────────────────────────────────────────────────

@diwo_bp.route("/workflows/<wf_id>/audit-logs", methods=["GET"])
def audit_logs(wf_id):
    logs = get_audit_logs(wf_id)
    return jsonify([
        {
            "id":        log["id"],
            "stage":     log["stage"],
            "action":    log["action"],
            "actor":     log["actor"],
            "details":   json.loads(log["details_json"]) if log["details_json"] else {},
            "timestamp": log["timestamp"],
        }
        for log in logs
    ])


# ─────────────────────────────────────────────────────────────────────────────
# Git Integration: Apply Files & Push to GitHub
# ─────────────────────────────────────────────────────────────────────────────

@diwo_bp.route("/diwo/apply-and-push", methods=["POST"])
def apply_and_push():
    """
    Apply refactored files to local repository, create a branch, and open GitHub Desktop.
    
    Request body:
    {
      "files": [{ "path": "file/path.java", "after": "code content" }, ...],
      "branch_name": "refactoring/my-changes",
      "repository_path": "/path/to/repo"
    }
    """
    data = request.get_json() or {}
    
    # Validate inputs
    files = data.get("files", [])
    branch_name = data.get("branch_name", "").strip()
    repo_path = data.get("repository_path", "").strip()
    
    if not files:
        return _err("No files provided", 400)
    if not branch_name:
        return _err("Branch name required", 400)
    if not repo_path:
        return _err("Repository path required", 400)
    if not isinstance(files, list):
        return _err("Files must be a list", 400)
    
    try:
        # If the user provided a remote URL (https://, http://, git@, or contains github.com),
        # clone it into a temporary directory so we can apply changes locally.
        repo_path_str = repo_path
        is_remote = False
        if isinstance(repo_path_str, str):
            lp = repo_path_str.lower()
            if lp.startswith("http://") or lp.startswith("https://") or lp.startswith("git@") or ("github.com" in lp and ":" in repo_path_str) or ("github.com" in lp and "/" in repo_path_str):
                is_remote = True

        temp_clone_dir = None
        if is_remote:
            try:
                temp_clone_dir = Path(tempfile.mkdtemp(prefix="diwo_repo_"))
                subprocess.run(["git", "clone", repo_path_str, str(temp_clone_dir)], check=True, capture_output=True)
                repo_path = temp_clone_dir
            except subprocess.CalledProcessError as e:
                # Cleanup on failure
                try:
                    if temp_clone_dir and temp_clone_dir.exists():
                        shutil.rmtree(temp_clone_dir)
                except Exception:
                    pass
                return _err(f"Failed to clone remote repository: {e.stderr.decode('utf-8', errors='ignore')}", 400)
        else:
            repo_path = Path(repo_path).resolve()

        # Ensure git repo exists
        if not (repo_path / ".git").exists():
            # If we cloned a remote, inform the user; otherwise it's a bad path
            if is_remote:
                return _err(f"Cloned repository did not contain a .git folder: {repo_path}", 400)
            return _err(f"Not a git repository: {repo_path}", 400)
        
        # Write files to repository and track what's written
        written_files = []
        for file_obj in files:
            file_path = file_obj.get("path", "")
            content = file_obj.get("after", "")

            if not file_path:
                continue

            # Normalize file path to avoid absolute paths interfering with join
            file_path = str(file_path).lstrip("/\\")

            # Prevent path traversal
            target_file = (repo_path / file_path).resolve()
            if not str(target_file).startswith(str(repo_path)):
                return _err(f"Path traversal detected: {file_path}", 400)

            # Create parent directories
            target_file.parent.mkdir(parents=True, exist_ok=True)

            # Write file
            with open(target_file, "w", encoding="utf-8") as f:
                f.write(content)

            written_files.append(str(target_file.relative_to(repo_path)))
        
        # Create or checkout the branch
        # If branch exists, checkout; otherwise create new branch
        try:
            # Check if branch exists
            res = subprocess.run(["git", "rev-parse", "--verify", branch_name], cwd=str(repo_path), capture_output=True)
            if res.returncode == 0:
                subprocess.run(["git", "checkout", branch_name], cwd=str(repo_path), check=True, capture_output=True)
            else:
                subprocess.run(["git", "checkout", "-b", branch_name], cwd=str(repo_path), check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            return _err(f"Failed to create or checkout branch: {e.stderr.decode('utf-8', errors='ignore')}", 500)

        # Stage all changes
        try:
            # Use git add -A to ensure all changes (including deletes) are staged
            add_proc = subprocess.run(["git", "add", "-A"], cwd=str(repo_path), check=True, capture_output=True)
            add_stdout = add_proc.stdout.decode('utf-8', errors='ignore')
            add_stderr = add_proc.stderr.decode('utf-8', errors='ignore')
        except subprocess.CalledProcessError as e:
            return _err(f"Failed to stage changes: {e.stderr.decode('utf-8', errors='ignore')}", 500)

        # Get staged files via git diff --cached --name-only
        staged_files = []
        try:
            diff_proc = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=str(repo_path), check=True, capture_output=True)
            diff_out = diff_proc.stdout.decode('utf-8', errors='ignore').strip()
            if diff_out:
                staged_files = [line.strip() for line in diff_out.splitlines() if line.strip()]
        except subprocess.CalledProcessError:
            staged_files = []
        
        # Open GitHub Desktop asynchronously (don't block on this)
        github_desktop_opened = False
        try:
            if os.name == "nt":  # Windows
                # Use start command to open GitHub Desktop with the repository
                subprocess.Popen(
                    f'start github -- -r "{repo_path}"',
                    shell=True,
                    creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
                )
                github_desktop_opened = True
            else:  # macOS / Linux
                # Try github command directly
                subprocess.Popen(["github", str(repo_path)])
                github_desktop_opened = True
        except Exception as launch_error:
            # GitHub Desktop may not be installed or in PATH
            print(f"Warning: Could not open GitHub Desktop: {launch_error}")
        
        resp = {
            "status": "success",
            "message": f"Files applied to branch '{branch_name}' and staged for commit",
            "branch": branch_name,
            "repository": str(repo_path),
            "github_desktop_opened": github_desktop_opened,
            "staged_files": staged_files,
        }
        # Include written files and git add output if available for diagnostics
        try:
            resp["written_files"] = written_files
        except Exception:
            resp["written_files"] = []
        try:
            resp["git_add_stdout"] = add_stdout
            resp["git_add_stderr"] = add_stderr
        except Exception:
            resp["git_add_stdout"] = resp["git_add_stderr"] = ""

        return jsonify(resp), 200
    
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.decode("utf-8") if e.stderr else str(e)
        return _err(f"Git operation failed: {error_msg}", 500)
    except Exception as e:
        return _err(f"Error: {str(e)}", 500)


# ─────────────────────────────────────────────────────────────────────────────
# Feedback Dataset Export
# ─────────────────────────────────────────────────────────────────────────────

@diwo_bp.route("/feedback/export", methods=["GET"])
def export_feedback():
    dataset = export_feedback_dataset()
    return jsonify({"count": len(dataset), "data": dataset})


# ─────────────────────────────────────────────────────────────────────────────
# Health
# ─────────────────────────────────────────────────────────────────────────────

@diwo_bp.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status":  "ok",
        "agent":   "DIWO",
        "version": "1.1.0",
    })
