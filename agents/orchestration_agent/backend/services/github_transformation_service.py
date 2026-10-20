"""
SCTVA on pull-request code, and the developer's final decisions
===============================================================
R26-SE-008 | Bandara S M Y M | IT22277886

After the developer approves a plan for a GitHub-linked workflow:

    approved plan (approved steps only)
         │   source of the planned files, read from GitHub AT THE ANALYSED
         │   COMMIT — never CUQA's or SCTVA's shared workspace
         v
    SCTVA POST /sctva/execute            (the existing live path,
         │                                services/transformation_service)
         v
    one record per file: original, refactored, unified diff, SCTVA's own
    syntax / structural / behavioural / invariant results, confidence
         │
         v
    Check Run + status comment: compact diffs, validation, T-ids
         │
         v
    developer accepts / rejects each T-id (GitHub command or DIWO)

WHAT COUNTS AS VALIDATED. A file's change is acceptable only if SCTVA
reported success, did not roll back, actually changed the file, and reported
syntax, structural AND behavioural validation as passed (plus invariant
checks when it ran them). Anything missing or failed fails closed: the record
is shown with the reason and can never be accepted or committed. Repository
test suites are not run by SCTVA, and the report says so rather than implying
they passed.

UNTRUSTED CODE. SCTVA's behavioural validation executes the code it checks.
This whole stage is therefore off unless GITHUB_PR_TRANSFORMATION_ENABLED is
set for an SCTVA deployment running in an isolated container. No GitHub
credential is ever sent to SCTVA.
"""

import difflib
import hashlib
import json
import logging

import config
from db import github_job_repository as jobs
from db import github_pr_workflow_repository as prs
from db import github_review_repository as reviews
from db.workflow_repository import (get_workflow, log_event, parse_json_field, save_feedback,
                                    update_workflow)
from services.access_service import AccessError

logger = logging.getLogger("diwo.github.transformation")

TRANSFORMATION_EVENT = "transformation"
REQUIRED_VALIDATIONS = ("syntax", "structural", "behavioral")
OPTIONAL_VALIDATIONS = ("invariant",)
NO_CHANGE = "no_change"

#: Refactorings whose effect spans files: accepting one file of such a run
#: without the others could leave the code broken, so they go together.
CROSS_FILE_REFACTORINGS = {"move method", "move field", "extract class", "inline class",
                           "hide delegate"}

MAX_DIFF_PREVIEW_LINES = 60
TESTS_NOTE = ("Repository test suites: not run — SCTVA validates syntax, structure and "
              "behaviour of the changed code but does not execute the project's tests.")


def sha256_text(text: str | None) -> str | None:
    return None if text is None else hashlib.sha256(text.encode("utf-8")).hexdigest()


def mapping_context(mapping: dict) -> dict:
    return {"installation_id": mapping["installation_id"],
            "repo_owner": mapping["repository_owner"], "repo_name": mapping["repository_name"],
            "repo_full_name": mapping["repository_full_name"], "pr_number": mapping["pr_number"],
            "head_sha": mapping["head_sha"]}


# ─── Queueing ───────────────────────────────────────────────────────────────

def queue_transformation(mapping: dict, workflow_id: str, identity: dict | None) -> dict:
    """Queue the SCTVA run for an approved plan — once per approved plan."""
    if not config.github_pr_transformation_enabled():
        return {"queued": False,
                "reason": "Transformation of pull-request code is disabled "
                          "(GITHUB_PR_TRANSFORMATION_ENABLED=false). The approved plan is recorded."}
    wf = get_workflow(workflow_id)
    plan = parse_json_field(wf, "plan_json") or {}
    steps = [str(s.get("step_id")) for s in plan.get("steps") or []]
    key = hashlib.sha256(json.dumps([workflow_id, mapping["head_sha"], steps,
                                     plan.get("approval")], sort_keys=True).encode()).hexdigest()
    job, created = jobs.enqueue_task(
        TRANSFORMATION_EVENT, f"transform:{key[:40]}", mapping_context(mapping),
        {"name": "run-transformation", "workflow_id": workflow_id,
         "identity": {k: v for k, v in (identity or {}).items() if k in ("email", "github_login")}})
    if created:
        log_event(workflow_id, "transformation", "github_transformation_queued",
                  {"job_id": job["id"], "steps": len(steps), "head_sha": mapping["head_sha"]},
                  actor="system")
    return {"queued": True, "job_id": job["id"], "duplicate": not created}


# ─── Building records from SCTVA's real result ──────────────────────────────

def _validation_verdict(file_result: dict, overall: dict | None) -> tuple[bool, str | None, dict]:
    validation = file_result.get("validation") or overall or {}
    checks = {name: validation.get(name) for name in REQUIRED_VALIDATIONS + OPTIONAL_VALIDATIONS
              if isinstance(validation.get(name), dict)}
    missing = [n for n in REQUIRED_VALIDATIONS if n not in checks or "passed" not in checks[n]]
    failed = [n for n, c in checks.items() if c.get("passed") is False]
    if failed:
        return False, "validation failed: " + ", ".join(failed), checks
    if missing:
        return False, "required validation unavailable: " + ", ".join(missing), checks
    return True, None, checks


def build_records(result: dict, originals: dict, plan: dict, mapping: dict, workflow_id: str,
                  run_id: str) -> list:
    steps = [s for s in plan.get("steps") or [] if isinstance(s, dict)]
    rows = []
    for file_result in sorted(result.get("files") or [], key=lambda f: f.get("path") or ""):
        path = file_result.get("path")
        if not path:
            continue
        source = originals.get(path) or {}
        original = source.get("source_code")
        if original is None:
            original = file_result.get("before") or ""
        after = file_result.get("after") or ""
        changed = bool(after) and after != original

        passed, reason, checks = _validation_verdict(file_result, result.get("validation"))
        if file_result.get("rollback_occurred"):
            status, reason = NO_CHANGE, "SCTVA rolled the change back"
            passed = False
        elif not file_result.get("success"):
            status, reason = reviews.FAILED, reason or "SCTVA reported the transformation failed"
            passed = False
        elif not changed:
            status, reason = NO_CHANGE, "SCTVA produced no change for this file"
            passed = False
        elif passed:
            status = reviews.PROPOSED
        else:
            status = reviews.FAILED

        diff = None
        if changed:
            diff = "".join(difflib.unified_diff(
                original.splitlines(keepends=True), after.splitlines(keepends=True),
                fromfile=f"a/{path}", tofile=f"b/{path}"))
        refactorings = [{"step_id": s.get("step_id"), "refactoring": s.get("refactoring")}
                        for s in steps if (s.get("target") or {}).get("file") == path]
        rows.append({
            "workflow_id": workflow_id, "mapping_id": mapping["id"], "run_id": run_id,
            "head_sha": mapping["head_sha"], "file_path": path,
            "original_blob_sha": source.get("blob_sha"),
            "original_sha256": sha256_text(original),
            "refactored_sha256": sha256_text(after) if changed else None,
            "original_content": original,
            "refactored_content": after if changed else None,
            "diff": diff,
            "refactorings_json": refactorings,
            "validation_json": {"checks": checks,
                                "success": bool(file_result.get("success")),
                                "rollback_occurred": bool(file_result.get("rollback_occurred")),
                                "transformation_applied": bool(file_result.get("transformation_applied")),
                                "total_replacements": file_result.get("total_replacements") or 0},
            "confidence": file_result.get("confidence_score")
                          if isinstance(file_result.get("confidence_score"), (int, float)) else None,
            "validation_passed": passed,
            "status": status,
            "status_reason": reason,
        })

    for index, row in enumerate(rows, start=1):
        row["display_id"] = f"T{index:03d}"
    cross_file = any(str(s.get("refactoring") or "").lower() in CROSS_FILE_REFACTORINGS
                     for s in steps)
    proposed = [r for r in rows if r["status"] == reviews.PROPOSED]
    for row in rows:
        row["coupled"] = cross_file and len(proposed) > 1 and row["status"] == reviews.PROPOSED
    return rows


# ─── Running ────────────────────────────────────────────────────────────────

class TransformationJobError(RuntimeError):
    def __init__(self, message: str, retryable: bool):
        super().__init__(message)
        self.message = message
        self.retryable = retryable


def run_transformation_job(job: dict, app) -> dict:
    """Run SCTVA for a queued, approved plan. Idempotent per job (run_id = job id).

    Returns {"records": [...], "summary": {...}}. Raises TransformationJobError.
    """
    from services.github_review_service import ensure_current_commit, fetch_commit_sources
    from services.transformation_service import TransformationError, run_transformation
    from domain.sctva_mapper import collect_plan_source_paths

    command = json.loads(job.get("command_json") or "{}")
    workflow_id = command.get("workflow_id")
    mapping = prs.get_mapping_for_workflow(workflow_id)
    wf = get_workflow(workflow_id) if mapping else None
    if not mapping or not wf:
        raise TransformationJobError("The workflow for this transformation no longer exists.", False)

    run_id = job["id"]
    existing = reviews.list_transformations(workflow_id, run_id)
    if existing:                     # a retry after the records were stored
        return {"records": existing, "summary": parse_json_field(wf, "transformation_result_json") or {}}
    if wf["status"] != "plan_approved":
        raise TransformationJobError(
            f"The workflow is '{wf['status']}', not plan_approved; nothing was transformed.", False)
    try:
        ensure_current_commit(mapping)
    except AccessError as exc:
        raise TransformationJobError(exc.message, exc.status == 503) from None

    plan = parse_json_field(wf, "plan_json") or {}
    report = parse_json_field(wf, "cuqa_report_json") or {}
    originals = {}

    def commit_sources(paths):
        files, missing = fetch_commit_sources(mapping, paths, report)
        for entry in files:
            originals[entry["file_path"]] = entry
        return {"files": [{"file_name": f["file_name"], "source_code": f["source_code"],
                           "language": f["language"], "source_mode": "github_commit"} for f in files],
                "missing": missing, "imported": len(files), "total": len(paths)}

    if not collect_plan_source_paths(plan):
        raise TransformationJobError("The approved plan names no source file.", False)
    try:
        outcome = run_transformation(plan, wf["language"], request_id=f"diwo_{run_id}",
                                     wf_id=workflow_id, source_loader=commit_sources)
    except TransformationError as exc:
        raise TransformationJobError(f"SCTVA transformation failed: {exc.message}",
                                     exc.status in (502, 503, 504)) from None
    except Exception as exc:  # noqa: BLE001 - GitHub read errors etc.
        message = getattr(exc, "message", None) or type(exc).__name__
        raise TransformationJobError(f"Transformation could not run: {message}",
                                     bool(getattr(exc, "retryable", False))) from None

    result = outcome["result"]
    records = build_records(result, originals, plan, mapping, workflow_id, run_id)
    reviews.insert_transformations(records)
    mapping_info = outcome.get("mapping") or {}
    summary = {
        "source": "sctva", "run_id": run_id, "head_sha": mapping["head_sha"],
        "sctva_url": outcome.get("sctva_url"), "request_id": result.get("requestId"),
        "success": result.get("success"), "rollback_occurred": result.get("rollbackOccurred"),
        "confidence_score": result.get("confidenceScore"),
        "executable_actions": mapping_info.get("executableCount"),
        "unsupported_actions": mapping_info.get("noopCount"),
        "missing_sources": (outcome.get("sources") or {}).get("missing") or [],
        "tests": "not_run",
        "transformations": [{"id": r["display_id"], "file": r["file_path"], "status": r["status"],
                             "validated": r["validation_passed"]} for r in records],
    }
    update_workflow(workflow_id, status="transformation",
                    transformation_result_json=json.dumps(summary))
    log_event(workflow_id, "transformation", "github_sctva_transformation_executed", {
        **{k: summary[k] for k in ("run_id", "head_sha", "request_id", "success",
                                   "rollback_occurred", "unsupported_actions")},
        "proposed": sum(1 for r in records if r["status"] == reviews.PROPOSED),
        "failed": sum(1 for r in records if r["status"] == reviews.FAILED),
        "no_change": sum(1 for r in records if r["status"] == NO_CHANGE),
    }, actor="system")
    return {"records": reviews.list_transformations(workflow_id, run_id), "summary": summary}


# ─── Final decisions (shared by GitHub commands and DIWO) ───────────────────

class DecisionError(Exception):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.message = message
        self.status = status


def decide(workflow_id: str, decisions: dict, actor: str | None) -> dict:
    """Apply {"T001": "accept"|"reject"} to the latest run's records.

    Rules: only a validated, proposed change can be accepted; coupled changes
    (cross-file refactorings) are accepted together or not at all; an applied
    change cannot be re-decided. All-or-nothing: an invalid entry changes
    nothing. Returns {"accepted": [...], "rejected": [...]}.
    """
    records = {r["display_id"].upper(): r for r in reviews.list_transformations(workflow_id)}
    if not records:
        raise DecisionError("There are no proposed changes for this workflow.")
    normalized = {}
    for key, verdict in decisions.items():
        tid = str(key).upper()
        if tid not in records:
            raise DecisionError(f"{key} is not a transformation of this workflow.", 400)
        if verdict not in ("accept", "reject"):
            raise DecisionError(f"Decision for {key} must be 'accept' or 'reject'.", 400)
        record = records[tid]
        if record["status"] == reviews.APPLIED:
            raise DecisionError(f"{tid} has already been applied to the pull request.")
        if verdict == "accept" and (not record["validation_passed"]
                                    or record["status"] not in (reviews.PROPOSED, reviews.REJECTED,
                                                                reviews.ACCEPTED)):
            raise DecisionError(f"{tid} cannot be accepted: "
                                f"{record.get('status_reason') or 'it was not validated'}.")
        normalized[tid] = verdict

    coupled = {tid for tid, r in records.items() if r["coupled"]}
    accepting = {tid for tid, v in normalized.items() if v == "accept"}
    final_accepted = {tid for tid, r in records.items() if r["status"] == reviews.ACCEPTED} \
        - {tid for tid, v in normalized.items() if v == "reject"} | accepting
    if coupled and (final_accepted & coupled) and not coupled <= final_accepted:
        raise DecisionError("These changes come from a cross-file refactoring and must be "
                            f"accepted together: {', '.join(sorted(coupled))}.")

    now = jobs.stamp()
    for tid, verdict in normalized.items():
        record = records[tid]
        status = reviews.ACCEPTED if verdict == "accept" else reviews.REJECTED
        reviews.update_transformation(record["id"], status=status, decided_by=actor, decided_at=now)
        save_feedback(workflow_id, "transformation",
                      "github_transformation_accepted" if verdict == "accept"
                      else "github_transformation_rejected",
                      refactoring_type=", ".join(str(r.get("refactoring")) for r in
                                                 record.get("refactorings_json") or []) or None,
                      reason=f"{tid} {record['file_path']}", accepted=verdict == "accept")
    # actor="developer" resolves to the verified DIWO user or GitHub sender.
    log_event(workflow_id, "transformation", "github_transformation_decisions",
              {"decisions": normalized, "by": actor})
    return {"accepted": sorted(t for t, v in normalized.items() if v == "accept"),
            "rejected": sorted(t for t, v in normalized.items() if v == "reject")}


# ─── Reporting ──────────────────────────────────────────────────────────────

def _check(record: dict, name: str) -> str:
    check = ((record.get("validation_json") or {}).get("checks") or {}).get(name)
    if not check:
        return "not run"
    return "passed" if check.get("passed") else "FAILED"


def transformations_markdown(records: list, summary: dict, previews: bool = True) -> tuple[str, str]:
    """(summary table, diff previews) for the Check Run / status comment."""
    from services.github_check_service import _md

    rows = ["| ID | File | Refactorings | Syntax | Structural | Behavioural | Invariant | "
            "Confidence | Status |", "|---|---|---|---|---|---|---|---|---|"]
    for r in records:
        refs = ", ".join(str(x.get("refactoring")) for x in r.get("refactorings_json") or []) or "—"
        confidence = f"{r['confidence']:.2f}" if isinstance(r.get("confidence"), (int, float)) else "—"
        status = r["status"] + (" (must be accepted together)" if r.get("coupled") else "")
        if r.get("status_reason") and r["status"] in (reviews.FAILED, NO_CHANGE):
            status += f" — {r['status_reason']}"
        rows.append(f"| {r['display_id']} | {_md(r['file_path'])} | {_md(refs)} | "
                    f"{_check(r, 'syntax')} | {_check(r, 'structural')} | "
                    f"{_check(r, 'behavioral')} | {_check(r, 'invariant')} | {confidence} | "
                    f"{_md(status)} |")
    table = "\n".join(rows)

    blocks = []
    if previews:
        for r in records:
            if not r.get("diff"):
                continue
            lines = r["diff"].splitlines()
            shown = "\n".join(lines[:MAX_DIFF_PREVIEW_LINES]).replace("```", "``​`")
            more = (f"\n_…{len(lines) - MAX_DIFF_PREVIEW_LINES} more diff lines in DIWO._"
                    if len(lines) > MAX_DIFF_PREVIEW_LINES else "")
            blocks.append(f"**{r['display_id']} — {_md(r['file_path'])}**\n\n```diff\n{shown}\n```{more}")
    return table, "\n\n".join(blocks)


TRANSFORMATION_COMMANDS = (
    "**Decide from this pull request:** `/refactoriq show-transformations` · "
    "`/refactoriq accept-transformation T001` · `/refactoriq reject-transformation T002` · "
    "`/refactoriq accept-all-validated` · `/refactoriq submit-refactoring` (asks for confirmation) · "
    "`/refactoriq cancel`"
)


def build_transformation_output(job: dict, records: list, summary: dict,
                                details_url: str | None, writeback: dict | None = None):
    """(output, conclusion, actions) for the Check Run."""
    from services.github_check_service import _header

    proposed = [r for r in records if r["status"] == reviews.PROPOSED]
    accepted = [r for r in records if r["status"] == reviews.ACCEPTED]
    applied = [r for r in records if r["status"] == reviews.APPLIED]
    failed = [r for r in records if r["status"] in (reviews.FAILED, NO_CHANGE)]

    if applied:
        title = f"{len(applied)} accepted change(s) applied in {(writeback or {}).get('commit_sha', '')[:7]}"
    elif accepted and not proposed:
        title = f"{len(accepted)} change(s) accepted — submit to apply"
    elif proposed or accepted:
        title = (f"{len(proposed) + len(accepted)} validated change(s) proposed — "
                 "developer acceptance required")
    else:
        title = "Transformation produced no validated changes"

    rows = ["| | |", "|---|---|",
            f"| Transformation run | `{summary.get('run_id')}` (SCTVA request "
            f"`{summary.get('request_id') or '—'}`) |",
            f"| Original commit | `{summary.get('head_sha') or job['head_sha']}` |",
            f"| Proposed / accepted / applied | {len(proposed)} / {len(accepted)} / {len(applied)} |",
            f"| Failed or unchanged files | {len(failed)} |",
            f"| SCTVA overall | {'success' if summary.get('success') else 'not fully successful'}"
            f"{' (rolled back)' if summary.get('rollback_occurred') else ''} |"]
    if summary.get("unsupported_actions"):
        rows.append(f"| Plan steps SCTVA could not execute | {summary['unsupported_actions']} "
                    "(not applied) |")
    if summary.get("missing_sources"):
        rows.append(f"| Files not readable at the commit | {len(summary['missing_sources'])} |")
    if isinstance(summary.get("confidence_score"), (int, float)):
        rows.append(f"| SCTVA confidence | {summary['confidence_score']:.2f} |")
    if writeback and writeback.get("status") in (reviews.WB_REFUSED, reviews.WB_FAILED):
        rows.append(f"| Write-back | {writeback['status']}: {writeback.get('error') or ''} |")

    table, previews = transformations_markdown(records, summary)
    parts = [_header(job), "", *rows, "", table, "", TESTS_NOTE]
    if details_url:
        parts += ["", f"**Full side-by-side comparison:** [Open in DIWO]({details_url}) "
                      "(DIWO sign-in required)."]
    if not applied:
        parts += ["", TRANSFORMATION_COMMANDS,
                  "", "_Nothing is committed to the pull request until accepted changes are "
                      "explicitly submitted by someone with write access._"]
    output = {"title": title, "summary": "\n".join(parts)[:65000],
              "text": (previews or "No diff: no file was changed.")[:65000]}

    awaiting = bool(proposed or accepted) and not applied
    actions = []
    if proposed:
        actions.append({"label": "Accept validated", "identifier": "accept_validated",
                        "description": "Accept every validated change"})
    if proposed or accepted:
        actions.append({"label": "Reject all", "identifier": "reject_all",
                        "description": "Reject all proposed changes"})
    if accepted and config.github_pr_writeback_enabled():
        actions.append({"label": "Apply accepted", "identifier": "apply_accepted",
                        "description": "Commit accepted changes to this PR"})
    return output, ("action_required" if awaiting else "neutral"), actions


def publish_transformations(app, mapping: dict, workflow_id: str, writeback: dict | None = None,
                            note: str | None = None, comment: bool = True) -> None:
    """Show the latest run on the commit's Check Run and (unless a command is
    about to reply there anyway) in the status comment."""
    from services.github_check_service import publish_result, workflow_details_url
    from services.github_comment_service import upsert_status_comment

    wf = get_workflow(workflow_id)
    records = reviews.list_transformations(workflow_id)
    summary = parse_json_field(wf, "transformation_result_json") or {}
    job = {**mapping_context(mapping)}
    details_url = workflow_details_url(workflow_id)
    output, conclusion, actions = build_transformation_output(job, records, summary, details_url,
                                                              writeback)
    if mapping.get("check_run_id"):
        try:
            publish_result(app, job, mapping["check_run_id"], conclusion=conclusion, output=output,
                           details_url=details_url, actions=actions)
        except Exception as exc:  # noqa: BLE001 - best effort, state is already saved
            logger.warning("Transformation Check Run not updated: %s",
                           getattr(exc, "message", type(exc).__name__))
    if not comment:
        return
    table, _ = transformations_markdown(records, summary, previews=False)
    body = [f"### RefactorIQ — {output['title']}", ""]
    if note:
        body += [note, ""]
    body += [table, "", TESTS_NOTE, "", "Diff previews are on the **RefactorIQ / DIWO** check."]
    if not any(r["status"] == reviews.APPLIED for r in records):
        body += ["", TRANSFORMATION_COMMANDS]
    upsert_status_comment(app, mapping, "\n".join(body))
