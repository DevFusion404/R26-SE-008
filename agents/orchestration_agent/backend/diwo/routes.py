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
import os
import subprocess
import tempfile
import shutil
from pathlib import Path
from flask import Blueprint, request, jsonify

from db.database import (
    create_workflow, get_workflow, update_workflow, list_workflows,
    log_event, get_audit_logs, save_feedback, export_feedback_dataset,
    now_iso,
)
from diwo.orchestrator import (
    generate_refactoring_plan, simulate_transformation,
    compute_metrics_before, compute_metrics_after, next_stage,
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


def _require_stage(wf: dict, expected: str):
    """Return an error response if the workflow is not in the expected stage."""
    if wf["status"] != expected:
        return _err(
            f"Expected workflow stage '{expected}' but current stage is '{wf['status']}'."
            " Reload the page to sync your session."
        )
    return None


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

    wf_id          = f"wf_{uuid.uuid4().hex[:10]}"
    metrics_before = compute_metrics_before(smells)

    create_workflow(wf_id, target, language, smells)
    update_workflow(wf_id, metrics_before_json=json.dumps(metrics_before))
    log_event(wf_id, "smell_review", "workflow_started",
              {"target": target, "language": language, "smell_count": len(smells)})

    return jsonify({
        "workflow_id":     wf_id,
        "status":          "smell_review",
        "message":         "Workflow started. Developer can now review detected smells.",
        "metrics_before":  metrics_before,
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
        "plan":                  _parse_json_field(wf, "plan_json"),
        "transformation_result": _parse_json_field(wf, "transformation_result_json"),
        "metrics_before":        _parse_json_field(wf, "metrics_before_json"),
        "metrics_after":         _parse_json_field(wf, "metrics_after_json"),
    })


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 – Smell Selection
# ─────────────────────────────────────────────────────────────────────────────

@diwo_bp.route("/workflows/<wf_id>/select-smells", methods=["POST"])
def select_smells(wf_id):
    """
    Developer submits the smell IDs they want to address.
    Body: { selected_ids: [str], feedback?: { reason?: str } }

    FIX [13,14]: Stage guard + list validation before any DB write.
    """
    wf = get_workflow(wf_id)
    if not wf:
        return _err("Workflow not found.", 404)

    err = _require_stage(wf, "smell_review")
    if err:
        return err

    data = request.get_json(force=True, silent=True) or {}

    # FIX [14]: Validate selected_ids
    selected_ids = data.get("selected_ids")
    if not selected_ids or not isinstance(selected_ids, list):
        return _err("'selected_ids' must be a non-empty list of smell ID strings.")
    if not all(isinstance(sid, str) for sid in selected_ids):
        return _err("Each element of 'selected_ids' must be a string.")

    feedback = data.get("feedback", {})

    all_smells = _parse_json_field(wf, "smells_json") or []
    selected   = [s for s in all_smells if s["id"] in selected_ids]
    excluded   = [s for s in all_smells if s["id"] not in selected_ids]

    if not selected:
        return _err("None of the provided selected_ids matched known smells in this workflow.")

    update_workflow(wf_id, status="smell_selection",
                    selected_smells_json=json.dumps(selected))
    log_event(wf_id, "smell_selection", "smells_selected",
              {"selected": selected_ids, "excluded": [s["id"] for s in excluded]})

    for s in excluded:
        save_feedback(wf_id, "smell_selection", "smell_excluded",
                      smell_type=s.get("type"), severity=s.get("severity"),
                      reason=feedback.get("reason", "Developer choice"),
                      accepted=False)

    # Auto-generate plan and advance to plan_approval
    plan = generate_refactoring_plan(selected, wf["target"])
    update_workflow(wf_id, status="plan_approval", plan_json=json.dumps(plan))
    log_event(wf_id, "plan_approval", "plan_generated",
              {"plan_id": plan["plan_id"], "steps": plan["summary"]["total_steps"]},
              actor="system")

    return jsonify({
        "status":          "plan_approval",
        "selected_count":  len(selected),
        "excluded_count":  len(excluded),
        "plan":            plan,
        "message":         "Refactoring plan generated. Please review and approve.",
    })


# ─────────────────────────────────────────────────────────────────────────────
# Stage 3 – Plan Approval
# ─────────────────────────────────────────────────────────────────────────────

@diwo_bp.route("/workflows/<wf_id>/plan-decision", methods=["POST"])
def plan_decision(wf_id):
    """
    Body: { decision: 'approve'|'reject'|'modify', modified_steps?: [...], feedback?: {...} }

    FIX [13,15]: Stage guard + decision enum validation.
    """
    wf = get_workflow(wf_id)
    if not wf:
        return _err("Workflow not found.", 404)

    err = _require_stage(wf, "plan_approval")
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
        modified_steps = data.get("modified_steps")
        if modified_steps is not None:
            if not isinstance(modified_steps, list):
                return _err("'modified_steps' must be a list of step objects.")
            plan["steps"] = modified_steps
            plan["summary"]["total_steps"] = len(modified_steps)
        update_workflow(wf_id, plan_json=json.dumps(plan))
        log_event(wf_id, "plan_approval", "plan_modified",
                  {"steps_after": len(plan["steps"])})
        save_feedback(wf_id, "plan_approval", "plan_modified",
                      reason=feedback.get("reason"), rating=feedback.get("rating"),
                      accepted=True)
        return jsonify({
            "status":  "plan_approval",
            "plan":    plan,
            "message": "Plan updated. Please approve to proceed.",
        })

    # ── Approve → trigger transformation ─────────────────────────────────────
    log_event(wf_id, "plan_approval", "plan_approved",
              {"plan_id": plan.get("plan_id")})
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
        "transformation_result": tr,
        "refactored_code":      tr.get("refactored_code", ""),
        "diff_rows":            tr.get("diff_rows", []),
        "files":                tr.get("files", []),
        "metrics_after":        metrics_after,
        "message":              "Transformation applied. Please review results.",
    })


# ─────────────────────────────────────────────────────────────────────────────
# Stage 4 – Transformation Decision (Accept / Rollback)
# ─────────────────────────────────────────────────────────────────────────────

@diwo_bp.route("/workflows/<wf_id>/transformation-decision", methods=["POST"])
def transformation_decision(wf_id):
    """
    Body: { decision: 'accept'|'rollback', feedback?: {...} }

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
    update_workflow(wf_id, status="comparison")
    log_event(wf_id, "comparison", "transformation_accepted",
              {"rating": feedback.get("rating")})
    save_feedback(wf_id, "comparison", "transformation_accepted",
                  reason=feedback.get("reason"), rating=feedback.get("rating"),
                  accepted=True)

    metrics_before = _parse_json_field(wf, "metrics_before_json")
    metrics_after  = _parse_json_field(wf, "metrics_after_json")

    return jsonify({
        "status":          "comparison",
        "metrics_before":  metrics_before,
        "metrics_after":   metrics_after,
        "message":         "Changes accepted. View comparison report.",
    })


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
