"""
DIWO workflow coordination
==========================
R26-SE-008 | Bandara S M Y M | IT22277886

The workflow's own rules: creating it, resolving what the developer selected,
and building the updated smell report that Stage 2 hands to the planner.

The critical hand-off lives in build_smell_selection_payload(): the stored
CUQA report is *filtered* down to the selected smell ids, so the report the
RDP agent receives contains only the smells the developer kept. Rebuilding it
from the flattened smell list is the fallback for workflows that were created
from a client-supplied list and so never had a report to store.

Moved out of diwo/routes.py; behaviour is unchanged.
"""

import json
import uuid

from config import reports_dir
from db.workflow_repository import (
    create_workflow, get_workflow, list_workflows, update_workflow,
    log_event, save_feedback, parse_json_field, now_iso,
)
from domain.cuqa_normalizer import build_report_from_smells, filter_cuqa_report
from domain.metrics import compute_metrics_before
from services.archive_service import store_refactored_archive
from services.planning_service import build_approved_plan, plan_from_rdp
from services.transformation_service import simulate_transformation
from clients.rdp_client import rdp_base_url
from domain.metrics import compute_metrics_after

__all__ = [
    "resolve_selected_ids", "resolve_selection", "persist_new_workflow",
    "build_smell_selection_payload", "commit_smell_selection",
    "apply_plan_decision", "apply_transformation_decision",
    "save_updated_report", "require_stage", "StageError",
    "get_workflow", "list_workflows", "update_workflow", "parse_json_field",
]


class StageError(RuntimeError):
    """The workflow is not at a stage that accepts this request."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def require_stage(wf: dict, *expected: str):
    """Raise unless the workflow is in one of `expected`.

    Several stages legitimately accept a request from more than one point in
    the workflow — falling back from plan approval to smell review, for
    instance, re-enters /select-smells while the stored stage is still
    'plan_approval'.
    """
    if wf["status"] not in expected:
        wanted = " or ".join(f"'{stage}'" for stage in expected)
        raise StageError(
            f"Expected workflow stage {wanted} but current stage is '{wf['status']}'."
            " Reload the page to sync your session."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Selection resolution
# ─────────────────────────────────────────────────────────────────────────────

def resolve_selected_ids(all_smells: list, selected_files: list, selected_smells: list):
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


def resolve_selection(all_smells: list, selected_ids: list,
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
        resolved.extend(resolve_selected_ids(all_smells, selected_files, selected_smells))

    return list(dict.fromkeys(resolved))


# ─────────────────────────────────────────────────────────────────────────────
# Workflow creation
# ─────────────────────────────────────────────────────────────────────────────

def persist_new_workflow(target: str, language: str, smells: list,
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
# Stage 1 -> Stage 2: the filtered CUQA report
# ─────────────────────────────────────────────────────────────────────────────

def build_smell_selection_payload(wf, selected_ids, selected_files=None):
    """Build the updated smell report and planning input without mutating the DB."""
    all_smells = parse_json_field(wf, "smells_json") or []
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
    stored_report = parse_json_field(wf, "cuqa_report_json")
    if stored_report:
        updated_report = filter_cuqa_report(stored_report, selected_ids)
    else:
        updated_report = build_report_from_smells(
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


# ─────────────────────────────────────────────────────────────────────────────
# Stage decisions
# ─────────────────────────────────────────────────────────────────────────────

def commit_smell_selection(wf, selected_ids, selected_files, selection_mode,
                           feedback, replanning):
    """Persist the developer's smell selection and plan against it.

    Steps 4 -> 7 of the DIWO workflow, and the reason the filtering happens
    here rather than in the route: the report forwarded to the RDP agent is
    the *updated* one, so a smell the developer rejected is gone before the
    planner ever sees it.
    """
    payload = build_smell_selection_payload(wf, selected_ids, selected_files)
    selected = payload["selected"]
    excluded = payload["excluded"]
    planning_input = payload["planning_input"]

    if not selected:
        raise StageError(
            "None of the provided selected_ids matched known smells in this workflow."
        )

    update_workflow(wf["id"], status="smell_selection",
                    selected_smells_json=json.dumps(selected))
    log_event(wf["id"], "smell_selection", "smells_selected",
              {"selected": selected_ids, "excluded": [s["id"] for s in excluded],
               "selection_mode": selection_mode,
               "replanned_after_fallback": replanning,
               "selected_files": sorted({
                   s.get("location", {}).get("file") for s in selected
                   if s.get("location", {}).get("file")
               })})

    for s in excluded:
        save_feedback(wf["id"], "smell_selection", "smell_excluded",
                      smell_type=s.get("type"), severity=s.get("severity"),
                      reason=feedback.get("reason", "Developer choice"),
                      accepted=False)

    # Forward the updated report — every file, but only the smells the
    # developer kept — to the RDP agent, which owns plan generation.
    plan, trace, plan_source, plan_warning = plan_from_rdp(
        payload["updated_report"], selected, wf["target"], wf_id=wf["id"]
    )

    # plan_full_json keeps the agent's plan before approval trims it down to the
    # approved steps, so a rollback from transformation can offer every step for
    # re-selection instead of only the ones approved last time.
    plan_serialized = json.dumps(plan)
    update_workflow(wf["id"], status="plan_approval",
                    plan_json=plan_serialized, plan_full_json=plan_serialized)
    log_event(wf["id"], "plan_approval", "plan_generated",
              {"plan_id": plan.get("plan_id"),
               "steps": plan["summary"]["total_steps"],
               "source": plan_source},
              actor="system")

    return {
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
    }


def apply_plan_decision(wf, decision, data):
    """Record the developer's verdict on the refactoring plan.

    'modify' and 'approve' both reduce the plan to the approved steps, so the
    report that leaves this function - and that Stage 3 forwards to SCTVA -
    can never contain a step the developer rejected.
    """
    feedback = data.get("feedback", {})
    plan     = parse_json_field(wf, "plan_json") or {}


    # ── Reject ───────────────────────────────────────────────────────────────
    if decision == "reject":
        update_workflow(wf["id"], status="rolled_back")
        log_event(wf["id"], "plan_approval", "plan_rejected",
                  {"reason": feedback.get("reason", "No reason given")})
        save_feedback(wf["id"], "plan_approval", "plan_rejected",
                      reason=feedback.get("reason"), rating=feedback.get("rating"),
                      accepted=False)
        return {"status": "rolled_back", "message": "Plan rejected. Workflow terminated."}

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
                raise StageError("'modified_steps' must be a list of step objects.")
            kept = {s.get("step_id") for s in modified_steps if isinstance(s, dict)}
            plan = build_approved_plan(
                plan,
                {s.get("step_id"): ("approve" if s.get("step_id") in kept else "reject")
                 for s in plan.get("steps") or []},
            )

        update_workflow(wf["id"], plan_json=json.dumps(plan))
        log_event(wf["id"], "plan_approval", "plan_modified", {
            "steps_after":  len(plan.get("steps") or []),
            "approved_ids": (plan.get("approval") or {}).get("approved_step_ids"),
            "rejected_ids": (plan.get("approval") or {}).get("rejected_step_ids"),
        })
        save_feedback(wf["id"], "plan_approval", "plan_modified",
                      reason=feedback.get("reason"), rating=feedback.get("rating"),
                      accepted=True)

        # One feedback row per rejected step — a step-level rejection is a
        # stronger training signal than the session-level approval.
        for step in (plan.get("approval") or {}).get("rejected_step_ids") or []:
            save_feedback(wf["id"], "plan_approval", "plan_step_rejected",
                          reason=f"Developer rejected plan step {step}.",
                          accepted=False)

        return {
            "status":  "plan_approval",
            "plan":    plan,
            "message": "Plan reduced to the approved steps. Please approve to proceed.",
        }

    # ── Approve → trigger transformation ─────────────────────────────────────
    # An approve can carry the decisions directly, so a caller that never sent
    # a separate 'modify' still gets an approved-only plan persisted.
    approve_decisions = data.get("decisions")
    if isinstance(approve_decisions, dict) and approve_decisions:
        plan = build_approved_plan(plan, approve_decisions)
        update_workflow(wf["id"], plan_json=json.dumps(plan))

    log_event(wf["id"], "plan_approval", "plan_approved",
              {"plan_id": plan.get("plan_id"),
               "steps": len(plan.get("steps") or []),
               "approved_ids": (plan.get("approval") or {}).get("approved_step_ids")})
    save_feedback(wf["id"], "plan_approval", "plan_approved",
                  reason=feedback.get("reason"), rating=feedback.get("rating"),
                  accepted=True)

    tr             = simulate_transformation(plan, wf["language"])
    metrics_before = parse_json_field(wf, "metrics_before_json") or {}
    selected       = parse_json_field(wf, "selected_smells_json") or []
    resolved       = tr["steps_passed"]
    metrics_after  = compute_metrics_after(metrics_before, resolved, len(selected))

    update_workflow(wf["id"],
                    status="transformation",
                    transformation_result_json=json.dumps(tr),
                    metrics_after_json=json.dumps(metrics_after))
    log_event(wf["id"], "transformation", "transformation_completed",
              {"status": tr["status"], "passed": tr["steps_passed"], "failed": tr["steps_failed"]},
              actor="system")

    return {
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
    }


def apply_transformation_decision(wf, decision, data):
    """Accept the transformation or roll it back.

    On accept, `data["files"]` carries the final source of every file - a
    rejected file arrives already holding its original source. That is what
    preserves the rollback behaviour end to end: the archive, the audit trail
    and the git branch all describe the code the developer settled on.

    Returns (response_payload, archive_bytes); the bytes are None unless an
    archive was built on this call.
    """
    feedback = data.get("feedback", {})

    if decision == "rollback":
        tr          = parse_json_field(wf, "transformation_result_json") or {}
        snapshot_id = tr.get("snapshot_id", "unknown")
        update_workflow(wf["id"], status="rolled_back")
        log_event(wf["id"], "transformation", "rollback_triggered",
                  {"snapshot_id": snapshot_id, "reason": feedback.get("reason")})
        save_feedback(wf["id"], "transformation", "rollback_triggered",
                      reason=feedback.get("reason"), rating=feedback.get("rating"),
                      accepted=False)
        return {
            "status":  "rolled_back",
            "message": f"Rolled back to snapshot {snapshot_id}.",
        }, None

    # Accept → move to comparison
    def _paths(key):
        value = data.get(key) or []
        return [str(p) for p in value if isinstance(p, (str, int))] if isinstance(value, list) else []

    accepted_files = _paths("accepted_files")
    rejected_files = _paths("rejected_files")
    written_files  = _paths("written_files") or accepted_files

    tr = parse_json_field(wf, "transformation_result_json") or {}
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
            archive_bytes, archive_info = store_refactored_archive(
                wf["id"],
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

    update_workflow(wf["id"],
                    status="comparison",
                    transformation_result_json=json.dumps(tr))

    log_event(wf["id"], "comparison", "transformation_accepted",
              {"rating": feedback.get("rating"),
               "accepted_files": accepted_files,
               "rejected_files": rejected_files,
               "written_files": written_files})
    save_feedback(wf["id"], "comparison", "transformation_accepted",
                  reason=feedback.get("reason"), rating=feedback.get("rating"),
                  accepted=True)

    # One feedback row per reverted file: the Feedback Manager trains on
    # rejections, and a file-level reject is a stronger signal than the
    # session-level accept above.
    for path in rejected_files:
        log_event(wf["id"], "comparison", "refactoring_reverted", {"file": path})
        save_feedback(wf["id"], "comparison", "refactoring_reverted",
                      reason=f"Developer rejected the refactoring of {path}; "
                             "file reverted to its original source.",
                      accepted=False)

    if archive_info:
        log_event(wf["id"], "comparison", "archive_built",
                  {"file_count": archive_info["file_count"], "bytes": archive_info["bytes"]},
                  actor="system")
    elif archive_error:
        log_event(wf["id"], "comparison", "archive_failed", {"reason": archive_error}, actor="system")

    metrics_before = parse_json_field(wf, "metrics_before_json")
    metrics_after  = parse_json_field(wf, "metrics_after_json")

    return {
        "status":          "comparison",
        "metrics_before":  metrics_before,
        "metrics_after":   metrics_after,
        "accepted_files":  accepted_files,
        "rejected_files":  rejected_files,
        "written_files":   written_files,
        "archive":         archive_info,
        "archive_error":   archive_error,
        "message":         "Changes accepted. View comparison report.",
    }, archive_bytes


# ─────────────────────────────────────────────────────────────────────────────
# Updated-report export
# ─────────────────────────────────────────────────────────────────────────────

def save_updated_report(wf_id: str, updated_report: dict) -> dict:
    """Write the updated code smell report to runtime/reports/ as JSON."""
    timestamp = now_iso().replace(":", "-").replace(".", "-")
    filename = f"updated_report_{wf_id}_{timestamp}.json"
    file_path = reports_dir() / filename

    with open(file_path, "w", encoding="utf-8") as handle:
        json.dump(updated_report, handle, indent=2)

    return {
        "status": "success",
        "message": "Report saved successfully",
        "file_path": str(file_path),
        "file_name": filename,
        "workflow_id": wf_id,
    }
