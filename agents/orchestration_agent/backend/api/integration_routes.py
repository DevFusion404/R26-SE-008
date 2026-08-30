"""
Integration routes
==================
R26-SE-008 | Bandara S M Y M | IT22277886

Reachability of the three specialized agents, and the CUQA quality-report
proxy the Code Smell Review stage reads from:

    GET      /api/cuqa/status            is CUQA up, and is a repository loaded?
    GET      /api/rdp/status             is the RDP agent up?
    GET      /api/sctva/status           is the SCTVA agent up?
    GET|POST /api/cuqa/quality-report    proxy Agent 1's report
    GET      /api/cuqa/project-structure proxy Agent 1's repository file tree
    POST     /api/workspace/sources      read source text out of the workspace
    GET      /api/health                 this backend

The last three are proxies on purpose. The browser used to call CUQA :8080 and
SCTVA :8002 itself to assemble the whole-project archive; routing them here
keeps every agent hand-off on one path,

    DIWO frontend -> Orchestration Agent -> specialized agent

so the frontend needs exactly one base URL and one CORS origin.
"""

from flask import Blueprint, jsonify, request

from api import cuqa_error_response, err
from clients.cuqa_client import (
    CUQAError, cuqa_base_url, fetch_project_structure, fetch_quality_report, probe_cuqa,
)
from clients.rdp_client import probe_rdp
from clients.sctva_client import SCTVAError, probe_sctva, sctva_base_url
from services.source_service import fetch_workspace_sources
from domain.cuqa_normalizer import (
    cuqa_report_to_smells, detect_primary_language, normalize_cuqa_report,
)

integration_bp = Blueprint("integration", __name__)


@integration_bp.route("/cuqa/status", methods=["GET"])
def cuqa_status():
    """Is the CUQA agent up, and does it have a repository loaded?"""
    return jsonify(probe_cuqa())

@integration_bp.route("/rdp/status", methods=["GET"])
def rdp_status():
    """Is the RDP agent up? Planning is forwarded to it at POST /generate."""
    return jsonify(probe_rdp())

@integration_bp.route("/sctva/status", methods=["GET"])
def sctva_status():
    """Is the Safe Code Transformation & Validation agent up?

    Added alongside clients/sctva_client.py so the orchestrator can report on
    all three specialized agents. The approved plan is still executed by the
    browser against SCTVA directly, which is unchanged.
    """
    return jsonify(probe_sctva())

@integration_bp.route("/cuqa/quality-report", methods=["GET", "POST"])
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
        return cuqa_error_response(exc)

    try:
        report = normalize_cuqa_report(payload)
    except ValueError as exc:
        return err(f"Unexpected CUQA response shape: {exc}", 502)

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

@integration_bp.route("/cuqa/project-structure", methods=["GET"])
def cuqa_project_structure():
    """The loaded repository's file tree, proxied from the CUQA agent.

    Names and paths only. The Results stage uses it to build the WHOLE-project
    archive: without the tree it would only know about the handful of files the
    agents touched, and the download would silently be a partial project.
    """
    try:
        payload = fetch_project_structure()
    except CUQAError as exc:
        return cuqa_error_response(exc)

    return jsonify({
        "repo_name":          payload.get("repo_name"),
        "source":             payload.get("source"),
        "total_source_files": payload.get("total_source_files"),
        "tree":               payload.get("tree"),
        "cuqa_url":           cuqa_base_url(),
    })


@integration_bp.route("/workspace/sources", methods=["POST"])
def workspace_sources():
    """Read the raw text of CUQA-analysed files out of the CUQA temp workspace.

    Body: { file_paths: ["src/Order.java", ...] }

    CUQA owns the workspace, so the text is read from it over HTTP and batched
    here rather than in the browser. Files that could not be located come back
    in `missing`; that is not an error, because a project spanning hundreds of
    files should not fail on one stale path.
    """
    data = request.get_json(force=True, silent=True) or {}
    file_paths = data.get("file_paths") or data.get("paths") or []

    if not isinstance(file_paths, list):
        return err("'file_paths' must be a list of repository-relative paths.")
    if not file_paths:
        return jsonify({"files": [], "missing": [], "imported": 0, "total": 0})

    try:
        payload = fetch_workspace_sources(file_paths)
    except SCTVAError as exc:
        status = exc.status if 400 <= exc.status < 600 else 502
        return jsonify({
            "error":     exc.message,
            "sctva_url": sctva_base_url(),
            "reachable": exc.status != 503,
        }), status

    return jsonify(payload)


@integration_bp.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status":  "ok",
        "agent":   "DIWO",
        "version": "1.1.0",
    })
