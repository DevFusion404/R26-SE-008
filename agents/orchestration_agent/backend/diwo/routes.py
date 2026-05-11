"""
DIWO Agent REST API Routes
===========================
All endpoints consumed by the React frontend.
"""

import uuid
import json
from flask import Blueprint, request, jsonify

from db.database import (
    create_workflow, get_workflow, update_workflow, list_workflows,
    log_event, get_audit_logs, save_feedback, export_feedback_dataset,
    now_iso
)
from diwo.orchestrator import (
    generate_refactoring_plan, simulate_transformation,
    compute_metrics_before, compute_metrics_after, next_stage
)

diwo_bp = Blueprint("diwo", __name__)


def _err(msg, code=400):
    return jsonify({"error": msg}), code


def _parse_json_field(wf, field):
    val = wf.get(field)
    if not val:
        return None
    return json.loads(val) if isinstance(val, str) else val


# ─────────────────────────────────────────────────────────────────────────────
# Workflow Management
# ─────────────────────────────────────────────────────────────────────────────

@diwo_bp.route("/workflows", methods=["GET"])
def list_wf():
    workflows = list_workflows()
    result = []
    for wf in workflows:
        result.append({
            "id": wf["id"],
            "target": wf["target"],
            "language": wf["language"],
            "status": wf["status"],
            "created_at": wf["created_at"],
            "updated_at": wf["updated_at"],
        })
    return jsonify(result)


@diwo_bp.route("/workflows", methods=["POST"])
def start_workflow():
    """
    Start a new workflow.
    Body: { target, language, smells: [...] }
    """
    data = request.get_json(force=True)
    target = data.get("target", "Unknown.java")
    language = data.get("language", "java")
    smells = data.get("smells", [])

    if not smells:
        return _err("smells list is required to start a workflow")

    wf_id = f"wf_{uuid.uuid4().hex[:10]}"
    metrics_before = compute_metrics_before(smells)

    create_workflow(wf_id, target, language, smells)
    update_workflow(wf_id, metrics_before_json=json.dumps(metrics_before))

    log_event(wf_id, "smell_review", "workflow_started",
              {"target": target, "language": language, "smell_count": len(smells)})

    return jsonify({
        "workflow_id": wf_id,
        "status": "smell_review",
        "message": "Workflow started. Developer can now review detected smells.",
        "metrics_before": metrics_before,
    }), 201


@diwo_bp.route("/workflows/<wf_id>", methods=["GET"])
def get_wf(wf_id):
    wf = get_workflow(wf_id)
    if not wf:
        return _err("Workflow not found", 404)

    return jsonify({
        "id": wf["id"],
        "target": wf["target"],
        "language": wf["language"],
        "status": wf["status"],
        "created_at": wf["created_at"],
        "updated_at": wf["updated_at"],
        "smells": _parse_json_field(wf, "smells_json"),
        "selected_smells": _parse_json_field(wf, "selected_smells_json"),
        "plan": _parse_json_field(wf, "plan_json"),
        "transformation_result": _parse_json_field(wf, "transformation_result_json"),
        "metrics_before": _parse_json_field(wf, "metrics_before_json"),
        "metrics_after": _parse_json_field(wf, "metrics_after_json"),
    })


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 – Smell Selection
# ─────────────────────────────────────────────────────────────────────────────

@diwo_bp.route("/workflows/<wf_id>/select-smells", methods=["POST"])
def select_smells(wf_id):
    """
    Developer submits the smell IDs they want to address.
    Body: { selected_ids: [...], feedback: { reason, excluded_ids } }
    """
    wf = get_workflow(wf_id)
    if not wf:
        return _err("Workflow not found", 404)
    if wf["status"] != "smell_review":
        return _err(f"Cannot select smells in stage '{wf['status']}'")

    data = request.get_json(force=True)
    selected_ids = data.get("selected_ids", [])
    feedback = data.get("feedback", {})

    all_smells = _parse_json_field(wf, "smells_json") or []
    selected = [s for s in all_smells if s["id"] in selected_ids]
    excluded = [s for s in all_smells if s["id"] not in selected_ids]

    update_workflow(wf_id,
                    status="smell_selection",
                    selected_smells_json=json.dumps(selected))

    log_event(wf_id, "smell_selection", "smells_selected",
              {"selected": selected_ids, "excluded": [s["id"] for s in excluded]})

    # Feedback for excluded smells
    for s in excluded:
        save_feedback(wf_id, "smell_selection", "smell_excluded",
                      smell_type=s.get("type"), severity=s.get("severity"),
                      reason=feedback.get("reason", "Developer choice"),
                      accepted=False)

    # Now auto-generate plan and advance to plan_approval
    plan = generate_refactoring_plan(selected, wf["target"])
    update_workflow(wf_id, status="plan_approval", plan_json=json.dumps(plan))
    log_event(wf_id, "plan_approval", "plan_generated",
              {"plan_id": plan["plan_id"], "steps": plan["summary"]["total_steps"]},
              actor="system")

    return jsonify({
        "status": "plan_approval",
        "selected_count": len(selected),
        "excluded_count": len(excluded),
        "plan": plan,
        "message": "Refactoring plan generated. Please review and approve.",
    })


# ─────────────────────────────────────────────────────────────────────────────
# Stage 3 – Plan Approval
# ─────────────────────────────────────────────────────────────────────────────

@diwo_bp.route("/workflows/<wf_id>/plan-decision", methods=["POST"])
def plan_decision(wf_id):
    """
    Body: { decision: 'approve'|'reject'|'modify', modified_steps: [...], feedback: {...} }
    """
    wf = get_workflow(wf_id)
    if not wf:
        return _err("Workflow not found", 404)
    if wf["status"] != "plan_approval":
        return _err(f"Cannot make plan decision in stage '{wf['status']}'")

    data = request.get_json(force=True)
    decision = data.get("decision")
    feedback = data.get("feedback", {})

    if decision not in ("approve", "reject", "modify"):
        return _err("decision must be 'approve', 'reject', or 'modify'")

    plan = _parse_json_field(wf, "plan_json") or {}

    if decision == "reject":
        update_workflow(wf_id, status="rolled_back")
        log_event(wf_id, "plan_approval", "plan_rejected",
                  {"reason": feedback.get("reason", "No reason given")})
        save_feedback(wf_id, "plan_approval", "plan_rejected",
                      reason=feedback.get("reason"), rating=feedback.get("rating"),
                      accepted=False)
        return jsonify({"status": "rolled_back", "message": "Plan rejected. Workflow terminated."})

    if decision == "modify":
        modified_steps = data.get("modified_steps")
        if modified_steps is not None:
            plan["steps"] = modified_steps
            plan["summary"]["total_steps"] = len(modified_steps)
        update_workflow(wf_id, plan_json=json.dumps(plan))
        log_event(wf_id, "plan_approval", "plan_modified",
                  {"steps_after": len(plan["steps"])})
        save_feedback(wf_id, "plan_approval", "plan_modified",
                      reason=feedback.get("reason"), rating=feedback.get("rating"),
                      accepted=True)
        # Re-present for approval — status stays plan_approval
        return jsonify({"status": "plan_approval", "plan": plan,
                        "message": "Plan updated. Please approve to proceed."})

    # Approve → trigger transformation
    log_event(wf_id, "plan_approval", "plan_approved",
              {"plan_id": plan.get("plan_id")})
    save_feedback(wf_id, "plan_approval", "plan_approved",
                  reason=feedback.get("reason"), rating=feedback.get("rating"),
                  accepted=True)

    tr = simulate_transformation(plan, wf["language"])
    metrics_before = _parse_json_field(wf, "metrics_before_json") or {}
    selected = _parse_json_field(wf, "selected_smells_json") or []
    resolved = tr["steps_passed"]
    metrics_after = compute_metrics_after(metrics_before, resolved, len(selected))

    update_workflow(wf_id,
                    status="transformation",
                    transformation_result_json=json.dumps(tr),
                    metrics_after_json=json.dumps(metrics_after))
    log_event(wf_id, "transformation", "transformation_completed",
              {"status": tr["status"], "passed": tr["steps_passed"], "failed": tr["steps_failed"]},
              actor="system")

    return jsonify({
        "status": "transformation",
        "transformation_result": tr,
        "refactored_code": tr.get("refactored_code", ""),
        "diff_rows": tr.get("diff_rows", []),
        "files": tr.get("files", []),
        "metrics_after": metrics_after,
        "message": "Transformation applied. Please review results.",
    })


# ─────────────────────────────────────────────────────────────────────────────
# Stage 4 – Transformation Decision (Accept / Rollback)
# ─────────────────────────────────────────────────────────────────────────────

@diwo_bp.route("/workflows/<wf_id>/transformation-decision", methods=["POST"])
def transformation_decision(wf_id):
    """
    Body: { decision: 'accept'|'rollback', feedback: {...} }
    """
    wf = get_workflow(wf_id)
    if not wf:
        return _err("Workflow not found", 404)
    if wf["status"] != "transformation":
        return _err(f"Cannot make transformation decision in stage '{wf['status']}'")

    data = request.get_json(force=True)
    decision = data.get("decision")
    feedback = data.get("feedback", {})

    if decision not in ("accept", "rollback"):
        return _err("decision must be 'accept' or 'rollback'")

    if decision == "rollback":
        tr = _parse_json_field(wf, "transformation_result_json") or {}
        snapshot_id = tr.get("snapshot_id", "unknown")
        update_workflow(wf_id, status="rolled_back")
        log_event(wf_id, "transformation", "rollback_triggered",
                  {"snapshot_id": snapshot_id, "reason": feedback.get("reason")})
        save_feedback(wf_id, "transformation", "rollback_triggered",
                      reason=feedback.get("reason"), rating=feedback.get("rating"),
                      accepted=False)
        return jsonify({"status": "rolled_back",
                        "message": f"Rolled back to snapshot {snapshot_id}."})

    # Accept → move to comparison
    update_workflow(wf_id, status="comparison")
    log_event(wf_id, "comparison", "transformation_accepted",
              {"rating": feedback.get("rating")})
    save_feedback(wf_id, "comparison", "transformation_accepted",
                  reason=feedback.get("reason"), rating=feedback.get("rating"),
                  accepted=True)

    metrics_before = _parse_json_field(wf, "metrics_before_json")
    metrics_after = _parse_json_field(wf, "metrics_after_json")

    return jsonify({
        "status": "comparison",
        "metrics_before": metrics_before,
        "metrics_after": metrics_after,
        "message": "Changes accepted. View comparison report.",
    })


# ─────────────────────────────────────────────────────────────────────────────
# Stage 5 – Complete Workflow
# ─────────────────────────────────────────────────────────────────────────────

@diwo_bp.route("/workflows/<wf_id>/complete", methods=["POST"])
def complete_workflow(wf_id):
    wf = get_workflow(wf_id)
    if not wf:
        return _err("Workflow not found", 404)
    if wf["status"] != "comparison":
        return _err(f"Cannot complete from stage '{wf['status']}'")

    data = request.get_json(force=True)
    update_workflow(wf_id, status="completed")
    log_event(wf_id, "completed", "workflow_completed",
              {"final_notes": data.get("notes", "")})

    return jsonify({"status": "completed", "message": "Workflow successfully completed."})


# ─────────────────────────────────────────────────────────────────────────────
# Audit Logs
# ─────────────────────────────────────────────────────────────────────────────

@diwo_bp.route("/workflows/<wf_id>/audit-logs", methods=["GET"])
def audit_logs(wf_id):
    logs = get_audit_logs(wf_id)
    result = []
    for log in logs:
        result.append({
            "id": log["id"],
            "stage": log["stage"],
            "action": log["action"],
            "actor": log["actor"],
            "details": json.loads(log["details_json"]) if log["details_json"] else {},
            "timestamp": log["timestamp"],
        })
    return jsonify(result)


# ─────────────────────────────────────────────────────────────────────────────
# Feedback Dataset Export (for ML model training)
# ─────────────────────────────────────────────────────────────────────────────

@diwo_bp.route("/feedback/export", methods=["GET"])
def export_feedback():
    dataset = export_feedback_dataset()
    return jsonify({"count": len(dataset), "data": dataset})


# ─────────────────────────────────────────────────────────────────────────────
# Health / Utility
# ─────────────────────────────────────────────────────────────────────────────

@diwo_bp.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "agent": "DIWO", "version": "1.0.0"})
