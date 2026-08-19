"""
Integration routes
==================
R26-SE-008 | Bandara S M Y M | IT22277886

Reachability of the three specialized agents, and the CUQA quality-report
proxy the Code Smell Review stage reads from:

    GET      /api/cuqa/status          is CUQA up, and is a repository loaded?
    GET      /api/rdp/status           is the RDP agent up?
    GET      /api/sctva/status         is the SCTVA agent up?          (added)
    GET|POST /api/cuqa/quality-report  proxy Agent 1's report
    GET      /api/health               this backend

/api/sctva/status is the one addition in this refactor: the orchestrator now
has a client for all three agents, so it can answer for all three. No existing
endpoint changed, and no frontend caller depends on the new one.
"""

from flask import Blueprint, jsonify, request

from api import cuqa_error_response, err
from clients.cuqa_client import (
    CUQAError, cuqa_base_url, fetch_quality_report, probe_cuqa,
)
from clients.rdp_client import probe_rdp
from clients.sctva_client import probe_sctva
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

@integration_bp.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status":  "ok",
        "agent":   "DIWO",
        "version": "1.1.0",
    })
