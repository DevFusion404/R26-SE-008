"""
Workflow routes
===============
R26-SE-008 | Bandara S M Y M | IT22277886

The DIWO workflow lifecycle and its human-decision stages:

    create / list / read  ->  smell selection  ->  plan approval
                          ->  transformation decision  ->  completion

Split out of the single diwo/routes.py. Every URL, method, request body and
response body is exactly what it was; the handlers now validate the request
and delegate, and the workflow rules live in services/workflow_service.py.

Stage 1 also carries the selection-impact endpoints, which answer "what does
picking this smell buy me, and what does skipping it cost?" before the
developer commits. They are read-only and never advance the workflow.
"""

import io
import json

from flask import Blueprint, jsonify, request, send_file

from api import cuqa_error_response, err
from clients.cuqa_client import CUQAError, cuqa_base_url, fetch_quality_report
from db.workflow_repository import log_event, parse_json_field, update_workflow
from domain.cuqa_normalizer import (
    cuqa_report_to_smells, derive_target_name, detect_primary_language,
    normalize_cuqa_report,
)
from domain.impact_model import MODEL_VERSION as IMPACT_MODEL_VERSION
from domain.plan_normalizer import build_rdp_plan_input
from domain.selection_optimizer import DEFAULT_BUDGET_MINUTES, PRESETS
from services.archive_service import archive_path
from services.planning_service import generate_updated_plan_report
from services.impact_service import (
    analyse_selection, compute_workflow_impacts, optimise_selection,
)
from services.transformation_service import run_transformation
from services.workflow_service import (
    apply_plan_decision, apply_transformation_decision,
    build_smell_selection_payload, commit_smell_selection, get_workflow,
    list_workflows, persist_new_workflow, require_stage, resolve_selection,
    save_updated_report as save_updated_report_to_disk,
)

workflow_bp = Blueprint("workflow", __name__)


@workflow_bp.route("/workflows", methods=["GET"])
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

@workflow_bp.route("/workflows", methods=["POST"])
def start_workflow():
    """
    Start a new workflow.
    Body: { target: str, language: str, smells: [{id, type, severity, ...}] }

    FIX [17]: Validates smells list structure before creating the workflow.
    """
    data = request.get_json(force=True, silent=True)
    if not data:
        return err("Request body must be valid JSON.")

    target   = data.get("target", "Unknown.java")
    language = data.get("language", "java")
    smells   = data.get("smells")

    # FIX [17]: Validate smells
    if not smells or not isinstance(smells, list):
        return err("'smells' must be a non-empty list.")
    for idx, s in enumerate(smells):
        if not isinstance(s, dict):
            return err(f"smells[{idx}] must be an object.")
        if not s.get("type"):
            return err(f"smells[{idx}] is missing required field 'type'.")

    wf_id, metrics_before = persist_new_workflow(target, language, smells)

    return jsonify({
        "workflow_id":     wf_id,
        "status":          "smell_review",
        "message":         "Workflow started. Developer can now review detected smells.",
        "metrics_before":  metrics_before,
    }), 201

@workflow_bp.route("/workflows/from-cuqa", methods=["POST"])
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
        return cuqa_error_response(exc)

    try:
        report = normalize_cuqa_report(payload)
    except ValueError as exc:
        return err(f"Unexpected CUQA response shape: {exc}", 502)

    smells = cuqa_report_to_smells(report)
    if not smells:
        return err(
            "The CUQA quality report contains no code smells, so there is nothing "
            "to refactor. Load a repository with detectable smells in the CUQA agent.",
            400,
        )

    target   = data.get("target") or derive_target_name(report)
    language = data.get("language") or detect_primary_language(report)

    wf_id, metrics_before = persist_new_workflow(
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

@workflow_bp.route("/workflows/<wf_id>", methods=["GET"])
def get_wf(wf_id):
    wf = get_workflow(wf_id)
    if not wf:
        return err("Workflow not found.", 404)

    return jsonify({
        "id":                    wf["id"],
        "target":                wf["target"],
        "language":              wf["language"],
        "status":                wf["status"],
        "created_at":            wf["created_at"],
        "updated_at":            wf["updated_at"],
        "smells":                parse_json_field(wf, "smells_json"),
        "selected_smells":       parse_json_field(wf, "selected_smells_json"),
        "updated_smells":        parse_json_field(wf, "updated_smells_json"),
        "planning_input":        parse_json_field(wf, "planning_input_json"),
        "plan":                  parse_json_field(wf, "plan_json"),
        "transformation_result": parse_json_field(wf, "transformation_result_json"),
        "metrics_before":        parse_json_field(wf, "metrics_before_json"),
        "metrics_after":         parse_json_field(wf, "metrics_after_json"),
    })

@workflow_bp.route("/workflows/<wf_id>/smell-selection-pass", methods=["POST"])
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
        return err("Workflow not found.", 404)

    data = request.get_json(force=True, silent=True) or {}

    selected_ids = data.get("selected_ids") or []
    selected_files = data.get("selected_files") or data.get("selected_file_paths") or []
    selected_smells = data.get("selected_smells") or []
    selection_mode = data.get("selection_mode") or ("smell" if selected_ids and not selected_files else "file")

    if selected_ids and not isinstance(selected_ids, list):
        return err("'selected_ids' must be a list of smell ID strings.")
    if selected_files and not isinstance(selected_files, list):
        return err("'selected_files' must be a list of file paths if provided.")
    if selected_smells and not isinstance(selected_smells, list):
        return err("'selected_smells' must be a list if provided.")

    selected_ids = resolve_selection(
        parse_json_field(wf, "smells_json") or [],
        selected_ids,
        selected_files,
        selected_smells,
    )

    if not selected_ids:
        return err("No smell IDs could be resolved from the selection. Send 'selected_ids', 'selected_files', or 'selected_smells'.")

    payload = build_smell_selection_payload(wf, selected_ids, selected_files)

    # What /select-smells would forward to the RDP agent, so the developer can
    # confirm the selection before committing to it. Built, not sent.
    payload["rdp_plan_input"] = build_rdp_plan_input(payload["updated_report"])
    payload["status"] = "smell_review"
    payload["selection_mode"] = selection_mode
    payload["selected_ids"] = selected_ids
    return jsonify(payload)

@workflow_bp.route("/workflows/<wf_id>/save-updated-report", methods=["POST"])
def save_updated_report(wf_id):
    """Save the updated code smell report to disk as a JSON file."""
    wf = get_workflow(wf_id)
    if not wf:
        return err("Workflow not found.", 404)

    data = request.get_json(force=True, silent=True) or {}
    updated_report = data.get("updated_report")
    
    if not updated_report:
        return err("'updated_report' object is required in request body.", 400)
    if not isinstance(updated_report, dict):
        return err("'updated_report' must be a JSON object.", 400)

    try:
        return jsonify(save_updated_report_to_disk(wf_id, updated_report)), 200
    except OSError as exc:
        return err(f"Failed to save report: {exc}", 500)

@workflow_bp.route("/workflows/<wf_id>/reset-to-smell-review", methods=["POST"])
def reset_to_smell_review(wf_id):
    """Send the workflow back to Stage 1 after a fallback from plan approval.

    The plan review screen's "Fallback to Smell Review" only moved the frontend;
    the workflow row stayed at 'plan_approval', so the next /select-smells was
    refused by the stage guard. This clears the plan and the stored selection so
    the developer starts the stage from the full CUQA report again.
    """
    wf = get_workflow(wf_id)
    if not wf:
        return err("Workflow not found.", 404)

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

@workflow_bp.route("/workflows/<wf_id>/smell-impacts", methods=["GET"])
def smell_impacts(wf_id):
    """Per-smell Selection Impact Records.

    Independent of the current selection, so this is computed once per workflow
    and cached in `smell_impacts`. Stage 1 fetches it when it mounts and then
    aggregates locally on every checkbox click, which is what keeps the panel
    instant.

    Each record answers both branches of the decision — what fixing the smell
    is projected to recover, and what deferring it carries forward — and says
    whether the pipeline can fix it at all.
    """
    wf = get_workflow(wf_id)
    if not wf:
        return err("Workflow not found.", 404)

    records = compute_workflow_impacts(wf, refresh=_wants_refresh())

    executable = sum(1 for r in records if r["capability"]["status"] == "executable")
    return jsonify({
        "workflow_id":   wf_id,
        "model_version": IMPACT_MODEL_VERSION,
        "tier":          "static",
        "count":         len(records),
        "executable":    executable,
        "advisory":      len(records) - executable,
        "records":       records,
    })


@workflow_bp.route("/workflows/<wf_id>/selection-impact", methods=["POST"])
def selection_impact(wf_id):
    """Project the consequences of a candidate selection. Read-only.

    Body: { selected_ids?, selected_files?, selected_smells? }

    Deliberately does NOT advance the workflow or call RDP — it is the what-if
    sibling of /smell-selection-pass, which is itself read-only but returns the
    filtered report rather than its consequences.
    """
    wf = get_workflow(wf_id)
    if not wf:
        return err("Workflow not found.", 404)

    data = request.get_json(force=True, silent=True) or {}
    for field in ("selected_ids", "selected_files", "selected_smells"):
        if data.get(field) is not None and not isinstance(data[field], list):
            return err(f"'{field}' must be a list if provided.")

    selected_ids = resolve_selection(
        parse_json_field(wf, "smells_json") or [],
        data.get("selected_ids") or [],
        data.get("selected_files") or [],
        data.get("selected_smells") or [],
    )
    return jsonify(analyse_selection(wf, selected_ids))


@workflow_bp.route("/workflows/<wf_id>/optimise-selection", methods=["POST"])
def optimise_selection_route(wf_id):
    """Propose a selection under a review-time budget.

    Body: { preset?: "best_value"|"safe_wins"|"stop_bleeding", budget_minutes?: int }

    Exact 0/1 knapsack over the executable smells only — value is projected
    quality points, weight is review minutes. Advisory findings are never
    proposed, because spending budget on a no-op is the defect this feature
    exists to remove. The result is a SUGGESTION: it is returned for the
    developer to apply or ignore, and nothing is persisted.
    """
    wf = get_workflow(wf_id)
    if not wf:
        return err("Workflow not found.", 404)

    data = request.get_json(force=True, silent=True) or {}

    preset = data.get("preset") or "best_value"
    if preset not in PRESETS:
        return err(f"'preset' must be one of: {', '.join(sorted(PRESETS))}.")

    budget = data.get("budget_minutes", DEFAULT_BUDGET_MINUTES)
    if not isinstance(budget, int) or isinstance(budget, bool) or budget <= 0:
        return err("'budget_minutes' must be a positive integer.")

    return jsonify(optimise_selection(wf, preset=preset, budget_minutes=budget))


def _wants_refresh() -> bool:
    """?refresh=1 recomputes instead of serving the cached records."""
    return str(request.args.get("refresh") or "").lower() in ("1", "true", "yes")


@workflow_bp.route("/workflows/<wf_id>/select-smells", methods=["POST"])
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
        return err("Workflow not found.", 404)

    require_stage(wf, "smell_review", "plan_approval")

    replanning = wf["status"] == "plan_approval"

    data = request.get_json(force=True, silent=True) or {}

    # FIX [14]: Validate selected_ids, but also allow selected file paths as a fallback.
    selected_ids = data.get("selected_ids")
    selected_files = data.get("selected_files") or data.get("selected_file_paths") or []
    selected_smells = data.get("selected_smells") or []
    selection_mode = data.get("selection_mode") or ("file" if selected_files else "smell")

    if selected_ids is not None:
        if not isinstance(selected_ids, list):
            return err("'selected_ids' must be a list of smell ID strings.")
        if not all(isinstance(sid, str) for sid in selected_ids):
            return err("Each element of 'selected_ids' must be a string.")
    else:
        selected_ids = []

    if selected_files and not isinstance(selected_files, list):
        return err("'selected_files' must be a list of file paths if provided.")

    if selected_smells and not isinstance(selected_smells, list):
        return err("'selected_smells' must be a list if provided.")

    # Explicit ids win; anything unresolved falls back to the selected files or
    # the per-smell descriptors (smell-wise selection sends no files, so the
    # deselected smells of a partially selected file are not pulled back in).
    selected_ids = resolve_selection(
        parse_json_field(wf, "smells_json") or [],
        selected_ids,
        selected_files,
        selected_smells,
    )

    if not selected_ids:
        return err("No smell IDs could be resolved from the selection. Send 'selected_ids', 'selected_files', or 'selected_smells'.")

    feedback = data.get("feedback", {})

    payload = commit_smell_selection(
        wf, selected_ids, selected_files, selection_mode, feedback, replanning
    )
    return jsonify(payload)

@workflow_bp.route("/workflows/<wf_id>/plan-preference-update", methods=["POST"])
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
        return err("Workflow not found.", 404)

    require_stage(wf, "plan_approval")

    data = request.get_json(force=True, silent=True) or {}
    decisions = data.get("decisions") or {}
    preferences = data.get("preferences") or {}

    if not isinstance(decisions, dict):
        return err("'decisions' must be an object keyed by step_id.")
    if not isinstance(preferences, dict):
        return err("'preferences' must be an object.")

    current_plan = parse_json_field(wf, "plan_json") or {}
    if not current_plan or not isinstance(current_plan.get("steps"), list):
        return err("No plan found for this workflow.", 400)

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

@workflow_bp.route("/workflows/<wf_id>/reset-to-plan-approval", methods=["POST"])
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
        return err("Workflow not found.", 404)

    previous = wf["status"]
    full_plan = parse_json_field(wf, "plan_full_json")
    plan = full_plan or parse_json_field(wf, "plan_json")

    if not plan:
        return err("This workflow has no plan to go back to.", 400)

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

@workflow_bp.route("/workflows/<wf_id>/plan-decision", methods=["POST"])
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
        return err("Workflow not found.", 404)

    require_stage(wf, "plan_approval", "transformation")

    data = request.get_json(force=True, silent=True) or {}

    # FIX [15]: Validate decision enum
    decision = data.get("decision")
    if decision not in ("approve", "reject", "modify"):
        return err("'decision' must be one of: 'approve', 'reject', 'modify'.")

    return jsonify(apply_plan_decision(wf, decision, data))

@workflow_bp.route("/workflows/<wf_id>/transform", methods=["POST"])
def transform(wf_id):
    """
    Run the APPROVED plan through the SCTVA agent.

    Body (all optional):
      {
        "plan":              {...},   defaults to the workflow's stored plan
        "language":          "java",  defaults to the workflow's language
        "request_id":        "...",
        "execution_options": {...}
      }

    The stored plan_json is the approved-only plan - plan-decision reduced it
    to the steps the developer accepted - so a rejected step cannot reach
    SCTVA even when the caller omits `plan`.

    This replaced the browser's direct POST to
    http://localhost:8002/sctva/execute. The response carries the same
    normalized shape the Transformation stage already rendered, so the stage
    reads the fields it always did.
    """
    wf = get_workflow(wf_id)
    if not wf:
        return err("Workflow not found.", 404)

    require_stage(wf, "plan_approval", "transformation")

    data = request.get_json(force=True, silent=True) or {}

    plan = data.get("plan") or parse_json_field(wf, "plan_json")
    if not plan:
        return err("This workflow has no approved plan to transform.", 400)

    execution_options = data.get("execution_options")
    if execution_options is not None and not isinstance(execution_options, dict):
        return err("'execution_options' must be an object if provided.")

    outcome = run_transformation(
        plan,
        language=data.get("language") or wf["language"],
        request_id=data.get("request_id"),
        execution_options=execution_options,
        wf_id=wf_id,
    )

    return jsonify({
        "status":      "transformation",
        "result":      outcome["result"],
        "request":     outcome["request"],
        "mapping":     outcome["mapping"],
        "sources": {
            "imported": outcome["sources"]["imported"],
            "missing":  outcome["sources"]["missing"],
            "total":    outcome["sources"]["total"],
        },
        "sctva_url":   outcome["sctva_url"],
        "executed_at": outcome["executed_at"],
    })


@workflow_bp.route("/workflows/<wf_id>/transformation-decision", methods=["POST"])
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
        return err("Workflow not found.", 404)

    require_stage(wf, "transformation")

    data = request.get_json(force=True, silent=True) or {}

    # FIX [16]: Validate decision enum
    decision = data.get("decision")
    if decision not in ("accept", "rollback"):
        return err("'decision' must be 'accept' or 'rollback'.")

    payload, archive_bytes = apply_transformation_decision(wf, decision, data)

    # `download: "zip"` returns the archive itself, for callers that want the
    # file straight from this request rather than a follow-up GET.
    if str(data.get("download") or "").lower() == "zip":
        if archive_bytes is None:
            return err(
                payload.get("archive_error") or
                "Send the final source of each file in 'files' to download an archive.",
                400,
            )
        archive = payload.get("archive")
        filename = archive.get("filename") if isinstance(archive, dict) else None
        return send_file(
            io.BytesIO(archive_bytes),
            mimetype="application/zip",
            as_attachment=True,
            download_name=filename or f"diwo_refactored_{wf_id}.zip",
        )

    return jsonify(payload)

@workflow_bp.route("/workflows/<wf_id>/refactored-archive", methods=["GET"])
def download_refactored_archive(wf_id):
    """Download the ZIP built when the transformation was accepted.

    Entry names are the repo-relative paths, so extracting the archive
    reproduces the project's folder structure. Rejected files are present as
    their original source, matching what was recorded in file_decisions.
    """
    wf = get_workflow(wf_id)
    if not wf:
        return err("Workflow not found.", 404)

    path = archive_path(wf_id)
    if not path.exists():
        return err(
            "No archive has been built for this workflow. Accept the transformation with the "
            "file contents in 'files' first.",
            404,
        )

    tr = parse_json_field(wf, "transformation_result_json") or {}
    filename = (tr.get("archive") or {}).get("filename") or f"diwo_refactored_{wf_id}.zip"

    return send_file(path, mimetype="application/zip",
                     as_attachment=True, download_name=filename)

@workflow_bp.route("/workflows/<wf_id>/complete", methods=["POST"])
def complete_workflow(wf_id):
    """
    FIX [13,18]: Stage guard; body is optional (notes may be absent).
    """
    wf = get_workflow(wf_id)
    if not wf:
        return err("Workflow not found.", 404)

    require_stage(wf, "comparison")

    # FIX [18]: Accept empty body gracefully
    data  = request.get_json(force=True, silent=True) or {}
    notes = data.get("notes", "")

    update_workflow(wf_id, status="completed")
    log_event(wf_id, "completed", "workflow_completed", {"final_notes": notes})

    return jsonify({"status": "completed", "message": "Workflow successfully completed."})
