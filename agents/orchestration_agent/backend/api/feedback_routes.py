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


from flask import Blueprint, jsonify

from db.workflow_repository import (
    export_feedback_dataset, get_audit_logs, parse_json_field,
)

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
            # parse_json_field, NOT json.loads: this column is TEXT under SQLite
            # and jsonb under Supabase, so it arrives as a string from one
            # backend and as a dict from the other. json.loads() on the dict
            # raised TypeError and took the whole audit trail down with it.
            "details":   parse_json_field(log, "details_json") or {},
            "timestamp": log["timestamp"],
        }
        for log in logs
    ])

@feedback_bp.route("/feedback/export", methods=["GET"])
def export_feedback():
    dataset = export_feedback_dataset()
    return jsonify({"count": len(dataset), "data": dataset})
