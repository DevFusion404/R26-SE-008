"""
Feedback and audit routes
=========================
R26-SE-008 | Bandara S M Y M | IT22277886

The audit trail the Comparison stage renders, and the training-data export the
ML Feedback Manager consumes:

    GET /api/workflows/<id>/audit-logs
    GET /api/feedback/export

Feedback rows themselves are written by the workflow services at each decision
point (a rejected smell, a rejected plan step, a reverted file), which is why
there is no submission endpoint here - the verdicts arrive with the stage
decisions rather than as a separate call.
"""

import json

from flask import Blueprint, jsonify

from db.workflow_repository import export_feedback_dataset, get_audit_logs

feedback_bp = Blueprint("feedback", __name__)


@feedback_bp.route("/workflows/<wf_id>/audit-logs", methods=["GET"])
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

@feedback_bp.route("/feedback/export", methods=["GET"])
def export_feedback():
    dataset = export_feedback_dataset()
    return jsonify({"count": len(dataset), "data": dataset})
