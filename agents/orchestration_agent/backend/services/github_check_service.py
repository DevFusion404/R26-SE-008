"""
GitHub Check Runs
=================
R26-SE-008 | Bandara S M Y M | IT22277886

The "RefactorIQ / DIWO" Check Run on a pull request's head commit — DIWO's one
channel back to GitHub. It needs only the App's "Checks: Read and write"
permission. From Milestone 2 it carries the real CUQA findings: a summary,
a findings table, and line annotations (50 per request, GitHub's limit).

    create_diwo_check   POST  /repos/{owner}/{repo}/check-runs
    update_diwo_check   PATCH /repos/{owner}/{repo}/check-runs/{id}

A Check Run is attached to a commit SHA, not to a PR, which is exactly what is
wanted: when the developer pushes again, the new head has no DIWO check until
DIWO has actually looked at it.
"""

from datetime import datetime, timezone

import config
from services.github_app_service import GitHubAppService

#: The Checks API caps output.summary at 65535 characters.
MAX_SUMMARY = 65535

VALID_STATUSES = ("queued", "in_progress", "completed")
VALID_CONCLUSIONS = ("action_required", "cancelled", "failure", "neutral",
                     "success", "skipped", "stale", "timed_out")


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _output(title: str | None, summary: str | None, text: str | None = None) -> dict | None:
    if not title and not summary:
        return None
    output = {"title": (title or config.GITHUB_CHECK_NAME)[:255],
              "summary": (summary or "")[:MAX_SUMMARY]}
    if text:
        output["text"] = text[:MAX_SUMMARY]
    return output


def create_diwo_check(app: GitHubAppService, installation_id: int, owner: str, repo: str,
                      head_sha: str, *, status: str = "queued", title: str | None = None,
                      summary: str | None = None, external_id: str | None = None,
                      details_url: str | None = None) -> dict:
    """Create the DIWO Check Run on `head_sha`. Returns GitHub's check-run object."""
    if status not in VALID_STATUSES:
        raise ValueError(f"Unknown check status {status!r}")
    body = {"name": config.GITHUB_CHECK_NAME, "head_sha": head_sha, "status": status}
    if status == "in_progress":
        body["started_at"] = _iso_now()
    if external_id:
        body["external_id"] = external_id
    if details_url:
        body["details_url"] = details_url
    output = _output(title, summary)
    if output:
        body["output"] = output
    return app.request("POST", f"repos/{owner}/{repo}/check-runs", installation_id,
                       json=body, expected=(201,), what="Check Run creation")


def update_diwo_check(app: GitHubAppService, installation_id: int, owner: str, repo: str,
                      check_run_id: int, *, status: str | None = None,
                      conclusion: str | None = None, title: str | None = None,
                      summary: str | None = None, text: str | None = None,
                      annotations: list | None = None,
                      details_url: str | None = None,
                      actions: list | None = None) -> dict:
    """Move the DIWO Check Run along queued → in_progress → completed.

    A conclusion implies status "completed", as the Checks API requires.
    At most 50 annotations per call (GitHub's limit); they are appended to the
    ones already on the Check Run.
    """
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"Unknown check status {status!r}")
    if conclusion is not None and conclusion not in VALID_CONCLUSIONS:
        raise ValueError(f"Unknown check conclusion {conclusion!r}")
    if annotations and len(annotations) > ANNOTATIONS_PER_REQUEST:
        raise ValueError(f"At most {ANNOTATIONS_PER_REQUEST} annotations per request")

    body = {}
    if conclusion:
        body.update(status="completed", conclusion=conclusion, completed_at=_iso_now())
    elif status:
        body["status"] = status
        if status == "in_progress":
            body["started_at"] = _iso_now()
    if details_url:
        body["details_url"] = details_url
    if actions is not None:
        # Check Run buttons: at most 3; label <= 20, description <= 40 chars.
        body["actions"] = [{"label": a["label"][:20], "identifier": a["identifier"][:20],
                            "description": a["description"][:40]} for a in actions[:3]]
    output = _output(title, summary, text)
    if output:
        if annotations:
            output["annotations"] = annotations
        body["output"] = output
    return app.request("PATCH", f"repos/{owner}/{repo}/check-runs/{int(check_run_id)}",
                       installation_id, json=body, expected=(200,),
                       what="Check Run update")


# ─────────────────────────────────────────────────────────────────────────────
# Milestones 2-3: publishing CUQA findings
# ─────────────────────────────────────────────────────────────────────────────

#: GitHub accepts at most 50 annotations per create/update request.
ANNOTATIONS_PER_REQUEST = 50
#: Annotations DIWO publishes per Check Run; the rest are listed in the text.
MAX_ANNOTATIONS = 200
#: Smells listed in the Check Run text before it says "and N more".
MAX_LISTED_SMELLS = 300

_LEVEL = {"high": "warning", "medium": "warning", "low": "notice"}

REVIEW_REQUIRED = "Analysis completed — developer review required"
NOT_CHANGED_NOTE = (
    "_No code has been changed. Detected smells are not approved, refactored, "
    "applied or validated at this stage — review them in DIWO._"
)


def workflow_details_url(workflow_id: str | None) -> str | None:
    """Link to the workflow in the React frontend, or None when not configured."""
    base = config.diwo_frontend_url()
    if not base or not workflow_id:
        return None
    return f"{base}/?diwo_workflow={workflow_id}"


def _md(value) -> str:
    """Neutralise text from the repository/CUQA for a Markdown table cell."""
    text = str(value if value is not None else "")
    for char in ("\\", "|", "`", "<", ">", "[", "]", "*", "_"):
        text = text.replace(char, "\\" + char)
    return " ".join(text.split())[:300]


def _safe_repo_path(path) -> str | None:
    """A repository-relative path GitHub can annotate, or None."""
    if not isinstance(path, str) or not path:
        return None
    path = path.replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    parts = path.split("/")
    if path.startswith("/") or ".." in parts or "" in parts or ":" in parts[0]:
        return None
    return path


def _line_range(smell: dict) -> tuple[int, int] | None:
    lines = (smell.get("location") or {}).get("lines") or []
    start = lines[0] if lines else None
    end = lines[1] if len(lines) > 1 else None
    if not isinstance(start, int) or isinstance(start, bool) or start < 1:
        start = smell.get("line")
    if not isinstance(start, int) or isinstance(start, bool) or start < 1:
        return None
    if not isinstance(end, int) or isinstance(end, bool) or end < start:
        end = start
    return start, end


def build_annotations(smells: list, report: dict | None = None) -> tuple[list, int]:
    """Line annotations for findings whose location is valid.

    With the verified locations the pipeline recorded for the analysed commit
    (report["snapshot"]["locations"]), a finding is annotated only where its
    line was confirmed against the file; without them (workflows analysed
    before verification existed) the CUQA line is used as reported.
    High and medium findings are annotated first, so the cap never crowds
    them out with low-severity repeats. Returns (annotations, unmappable).
    Unmapped findings are not lost: they stay in the report text and DIWO.
    """
    from services.github_report_service import SEVERITY_RANK, display_ids

    verified = ((report or {}).get("snapshot") or {}).get("locations")
    ids = display_ids(smells)
    ordered = sorted(smells, key=lambda s: SEVERITY_RANK.get(s.get("severity"), 3))
    annotations, unmappable = [], 0
    for smell in ordered:
        path = _safe_repo_path((smell.get("location") or {}).get("file")
                               or smell.get("relative_path"))
        if isinstance(verified, dict):
            found = verified.get(smell["id"]) or {}
            lines = (found["start"], found["end"]) if "start" in found else None
        else:
            lines = _line_range(smell)
        if not path or not lines:
            unmappable += 1
            continue
        if len(annotations) >= MAX_ANNOTATIONS:
            continue
        where = (smell.get("location") or {})
        target = where.get("method") or where.get("class") or smell.get("entity")
        message = smell.get("message") or f"{smell.get('type')} detected by CUQA."
        if target:
            message = f"{message}\n\nIn: {target}"
        annotations.append({
            "path": path,
            "start_line": lines[0],
            "end_line": lines[1],
            "annotation_level": _LEVEL.get(smell.get("severity"), "notice"),
            "title": f"{ids[smell['id']]} {smell.get('type') or 'Code smell'} "
                     f"({smell.get('severity') or 'unknown'})"[:255],
            "message": message[:4000],
        })
    # GitHub lists annotations in the order sent; show them file by file.
    annotations.sort(key=lambda a: (a["path"], a["start_line"]))
    return annotations, unmappable


def _header(job: dict) -> str:
    return (f"**Repository:** {_md(job['repo_full_name'])} · "
            f"**Pull request:** #{job['pr_number']} · "
            f"**Commit:** `{job['head_sha']}`")


#: File groups shown in the Check Run summary; the rest go to the text.
MAX_SUMMARY_FILES = 25

COMMAND_HELP = (
    "**Review from this pull request** — comment one command per comment: "
    "`/refactoriq list` · `/refactoriq select S001 S004` · `/refactoriq deselect S004` · "
    "`/refactoriq show-selection` · `/refactoriq generate-plan` · `/refactoriq help`"
)


def _finding_order(smells: list, files: list) -> list:
    """Every finding, file by file and group by group (the report's order)."""
    rank = {}
    for f_index, entry in enumerate(files):
        for g_index, group in enumerate(entry["groups"]):
            rank[(entry["file"], group["type"])] = (f_index, g_index)
    def key(item):
        index, smell = item
        path = (smell.get("location") or {}).get("file") or smell.get("relative_path") or "unknown"
        return rank.get((path, smell.get("type") or "Unknown"), (9999, 9999)) + (index,)
    return [smell for _, smell in sorted(enumerate(smells), key=key)]


def build_findings_output(job: dict, result: dict, details_url: str | None) -> dict:
    """Compact, grouped report of a completed analysis — real CUQA data only.

    Summary: totals, then per file one row per smell TYPE ("MagicNumber" with
    its occurrence count) carrying the highest severity, where it occurs and
    the stable ids to select it by. Text: every individual finding, capped;
    the complete interactive list is in DIWO.
    """
    from services.github_report_service import (compress_ids, describe_lines, display_ids,
                                                group_findings)

    smells = result.get("smells") or []
    report = result.get("report") or {}
    summary_block = report.get("summary") or {}
    severities = {"high": 0, "medium": 0, "low": 0}
    for smell in smells:
        severities[smell.get("severity")] = severities.get(smell.get("severity"), 0) + 1
    files = group_findings(smells)

    count = len(smells)
    title = (f"{count} code smell{'s' if count != 1 else ''} in {len(files)} "
             f"file{'s' if len(files) != 1 else ''} — developer review required"
             if count else "No code smells detected")

    analysed = result.get("files_analyzed")
    found = result.get("files_found")
    status = REVIEW_REQUIRED if count else "Analysis completed — no code smells reported by CUQA"
    rows = [
        "| | |", "|---|---|",
        f"| Status | {status} |",
        f"| Developer review | {'Required — select the findings to plan for' if count else 'Not needed'} |",
        f"| Detected code smells | {count} |",
        f"| Affected files | {len(files)} |",
    ]
    if analysed is not None:
        coverage = f"{analysed}"
        if isinstance(found, int) and found > analysed:
            coverage += (f" of {found} supported source files (CUQA analyses at most "
                         f"{analysed} files per snapshot)")
        rows.append(f"| Files analysed | {coverage} |")
    if summary_block.get("total_lines_of_code"):
        rows.append(f"| Lines of code analysed | {summary_block['total_lines_of_code']} |")
    if count:
        rows.append(f"| Severity | high {severities.get('high', 0)} · "
                    f"medium {severities.get('medium', 0)} · low {severities.get('low', 0)} |")
    score = summary_block.get("average_quality_score")
    if isinstance(score, (int, float)) and analysed:
        rows.append(f"| Average quality score (CUQA) | {score} |")

    parts = [_header(job), "", *rows]
    if count:
        parts += ["", "### Findings by file", ""]
        for entry in files[:MAX_SUMMARY_FILES]:
            c = entry["counts"]
            parts += [f"**{_md(entry['file'])}** — {sum(c.values())} finding(s): "
                      f"high {c.get('high', 0)} · medium {c.get('medium', 0)} · low {c.get('low', 0)}",
                      "", "| Smell | Count | Highest severity | Where | Lines | IDs |",
                      "|---|---|---|---|---|---|"]
            for group in entry["groups"]:
                where = ", ".join(_md(e) for e in group["entities"][:3])
                if len(group["entities"]) > 3:
                    where += f" (+{len(group['entities']) - 3})"
                parts.append(f"| {_md(group['type'])} | {group['count']} | {group['severity']} | "
                             f"{where or '—'} | {describe_lines(group['lines'])} | "
                             f"{compress_ids(group['ids'])} |")
            parts.append("")
        if len(files) > MAX_SUMMARY_FILES:
            parts.append(f"…and {len(files) - MAX_SUMMARY_FILES} more files — see the details below.")
        if details_url:
            parts += ["", f"**Developer review:** [Open Code Smell Approval in DIWO]({details_url}) "
                          "(DIWO sign-in required), or use the commands below."]
        else:
            parts += ["", "**Developer review:** open this workflow in DIWO "
                          f"(`{result.get('workflow_id')}`), or use the commands below. "
                          "Set DIWO_FRONTEND_URL to link it here."]
        parts += ["", COMMAND_HELP]
    parts += ["", NOT_CHANGED_NOTE]

    summary = "\n".join(parts)
    if len(summary) > MAX_SUMMARY:
        summary = summary[:MAX_SUMMARY - 80] + "\n\n…summary truncated; see DIWO for every finding."

    text = None
    if smells:
        ids = display_ids(smells)
        lines = ["### Every finding", "", "| ID | Smell | File | Line | Severity | Detail |",
                 "|---|---|---|---|---|---|"]
        for smell in _finding_order(smells, files)[:MAX_LISTED_SMELLS]:
            where = smell.get("location") or {}
            span = _line_range(smell)
            lines.append("| {} | {} | {} | {} | {} | {} |".format(
                ids[smell["id"]], _md(smell.get("type")),
                _md(where.get("file") or smell.get("relative_path")),
                f"{span[0]}" if span else "—", _md(smell.get("severity")),
                _md(smell.get("message"))))
        if len(smells) > MAX_LISTED_SMELLS:
            lines.append(f"\n…and {len(smells) - MAX_LISTED_SMELLS} more; see the DIWO workflow.")
        text = "\n".join(lines)

    return {"title": title, "summary": summary, "text": text}


def failure_output(job: dict, message: str) -> dict:
    return {"title": "Analysis failed",
            "summary": "\n".join([_header(job), "", "**Status:** Analysis failed", "",
                                  f"Reason: {_md(message)}", "",
                                  "No findings are reported for this commit. Push a new commit, "
                                  "or re-run the check, to try again."])}


def superseded_output(job: dict, newer_sha: str | None) -> dict:
    newer = f" (`{newer_sha[:7]}`)" if newer_sha else ""
    return {"title": "Superseded by a newer commit",
            "summary": "\n".join([_header(job), "",
                                  f"**Status:** Analysis superseded by a newer commit{newer}.",
                                  "", "Results for this commit are outdated; the newest commit "
                                  "of the pull request is analysed instead."])}


def fork_output(job: dict) -> dict:
    return {"title": "Fork pull requests are not analysed yet",
            "summary": "\n".join([_header(job), "",
                                  f"The head branch comes from `{_md(job.get('head_repo_full_name'))}`. "
                                  "RefactorIQ currently analyses same-repository pull requests only."])}


def publish_result(app: GitHubAppService, job: dict, check_run_id: int, *, conclusion: str,
                   output: dict, annotations: list | None = None,
                   details_url: str | None = None, actions: list | None = None) -> None:
    """Complete the Check Run, then append any annotations past the first 50."""
    annotations = annotations or []
    owner, repo, installation = job["repo_owner"], job["repo_name"], job["installation_id"]
    if conclusion == "action_required" and not details_url:
        # GitHub expects a details_url for action_required. Without a DIWO
        # link the action is taken in the PR conversation, so link there.
        details_url = f"https://github.com/{owner}/{repo}/pull/{job['pr_number']}"
    batches = [annotations[i:i + ANNOTATIONS_PER_REQUEST]
               for i in range(0, len(annotations), ANNOTATIONS_PER_REQUEST)] or [[]]
    update_diwo_check(app, installation, owner, repo, check_run_id, conclusion=conclusion,
                      title=output["title"], summary=output["summary"], text=output.get("text"),
                      annotations=batches[0], details_url=details_url,
                      actions=actions if actions is not None else [])
    for batch in batches[1:]:
        update_diwo_check(app, installation, owner, repo, check_run_id,
                          title=output["title"], summary=output["summary"],
                          text=output.get("text"), annotations=batch)


# ─────────────────────────────────────────────────────────────────────────────
# Milestone 4: developer review and RDP planning status
# ─────────────────────────────────────────────────────────────────────────────

#: Plan steps listed in the Check Run text before "and N more".
MAX_LISTED_STEPS = 100

_REVIEW_TITLES = {
    "smell_review": "Code smells awaiting developer review",
    "plan_ready": "Refactoring plan ready — awaiting developer approval",
    "plan_approved": "Refactoring plan approved by developer — not yet applied",
    "plan_rejected": "Refactoring plan rejected by developer",
}

_REVIEW_STATUS_TEXT = {
    "smell_review": "Developer review required — select the smells to plan for",
    "plan_ready": "Refactoring plan generated — developer approval required",
    "plan_approved": "Plan approved — transformation has not been run; no code has changed",
    "plan_rejected": "Plan rejected — no refactoring will be applied",
}


def _band(value) -> str:
    return str(value).lower() if isinstance(value, str) and value else "—"


def _gain(step: dict) -> str:
    impact = ((step.get("decision_support") or {}).get("impact") or {})
    low, high = impact.get("quality_gain_low"), impact.get("quality_gain_high")
    if isinstance(low, (int, float)) and isinstance(high, (int, float)):
        return f"+{low:g}–{high:g} pts" if low != high else f"+{low:g} pts"
    return _band(step.get("expected_impact") or step.get("impact"))


PLAN_COMMAND_HELP = (
    "**Decide from this pull request:** `/refactoriq show-plan` · "
    "`/refactoriq approve-plan P001 P002` · `/refactoriq reject-plan P003` · "
    "`/refactoriq submit-plan` (asks for confirmation)"
)


def step_display_id(step: dict) -> str:
    """P001 for step 1 — the id plan commands use."""
    try:
        return f"P{int(step.get('step_id')):03d}"
    except (TypeError, ValueError):
        return f"P{step.get('step_id')}"


def step_decision(step: dict, approval: dict, review_status: str, draft: dict) -> str:
    sid = str(step.get("step_id"))
    if sid in {str(i) for i in approval.get("approved_step_ids") or []}:
        return "approved"
    if sid in {str(i) for i in approval.get("rejected_step_ids") or []}:
        return "rejected"
    if review_status == "plan_approved":
        return "approved"
    verdict = draft.get(sid)
    return {"approve": "approved (draft)", "reject": "rejected (draft)",
            "manual": "manual work (draft)"}.get(verdict, "pending")


def is_manual_only(step: dict) -> bool:
    return ((step.get("decision_support") or {}).get("category")) == "manual_only"


def plan_steps_markdown(steps: list, approval: dict, review_status: str, draft: dict) -> str:
    """The plan grouped by affected file, one block per file."""
    by_file = {}
    for step in steps:
        path = (step.get("target") or {}).get("file") or "(module level)"
        by_file.setdefault(path, []).append(step)
    lines = []
    shown = 0
    for path, file_steps in by_file.items():
        lines += [f"### File: {_md(path)}", "",
                  "| Step | Refactoring | Smell | Target | DIWO recommendation | Risk | "
                  "Expected improvement | Decision |",
                  "|---|---|---|---|---|---|---|---|"]
        for step in file_steps:
            if shown >= MAX_LISTED_STEPS:
                break
            shown += 1
            target = step.get("target") or {}
            support = step.get("decision_support") or {}
            risk = (support.get("impact") or {}).get("risk_band") or step.get("risk")
            label = support.get("label") or "—"
            if is_manual_only(step):
                label += " (manual only — not automated)"
            capability = (support.get("capability") or {}).get("status")
            if capability and capability not in ("supported", "executable"):
                label += f" · SCTVA: {capability}"
            smell = step.get("smell_type") or step.get("smell") or step.get("smell_id")
            lines.append("| {} | {} | {} | {} | {} | {} | {} | {} |".format(
                step_display_id(step), _md(step.get("refactoring")), _md(smell),
                _md(target.get("method") or target.get("class")), _md(label), _band(risk),
                _md(_gain(step)), step_decision(step, approval, review_status, draft)))
        lines.append("")
    if len(steps) > shown:
        lines.append(f"…and {len(steps) - shown} more steps; see DIWO.")
    return "\n".join(lines)


def build_review_output(job: dict, review_status: str, plan: dict | None, actor: str | None,
                        details: dict, workflow_id: str, details_url: str | None):
    """(output, conclusion) for the Check Run while the developer reviews.

    Reports only what the plan actually carries — RDP's refactorings, files,
    impact and risk, DIWO's recommendation — and says plainly where the plan
    came from when RDP was unavailable. Never claims a change was applied.
    """
    plan = plan or {}
    steps = [s for s in plan.get("steps") or [] if isinstance(s, dict)]
    approval = plan.get("approval") or {}
    awaiting = review_status in ("smell_review", "plan_ready")
    reviewable = bool(details_url) or config.github_commands_enabled()
    conclusion = "action_required" if awaiting and reviewable else "neutral"

    title = _REVIEW_TITLES.get(review_status, "Developer review in progress")
    if review_status == "plan_ready" and steps:
        title += f" ({len(steps)} step{'s' if len(steps) != 1 else ''})"

    rows = ["| | |", "|---|---|",
            f"| Status | {_REVIEW_STATUS_TEXT.get(review_status, _md(review_status))} |"]
    if actor:
        rows.append(f"| Last decision by | {_md(actor)} |")
    if details.get("selected_count") is not None:
        rows.append(f"| Smells selected by developer | {details['selected_count']} "
                    f"(excluded {details.get('excluded_count') or 0}) |")
    source = details.get("plan_source") or plan.get("source") or plan.get("plan_source")
    if steps or source:
        origin = ("RDP agent" if source == "rdp_agent"
                  else "DIWO offline fallback planner — the RDP agent was unavailable"
                  if source else "—")
        rows.append(f"| Plan source | {origin} |")
    if steps:
        rows.append(f"| Plan steps | {len(steps)} |")
        files = {(s.get("target") or {}).get("file") for s in steps} - {None}
        rows.append(f"| Files affected by the plan | {len(files)} |")
        risks = {}
        for s in steps:
            band = ((s.get("decision_support") or {}).get("impact") or {}).get("risk_band") \
                or s.get("risk")
            risks[_band(band)] = risks.get(_band(band), 0) + 1
        rows.append("| Risk | " + " · ".join(f"{k} {v}" for k, v in sorted(risks.items())) + " |")
    if approval:
        rows.append(f"| Approved / rejected steps | {len(approval.get('approved_step_ids') or [])}"
                    f" / {len(approval.get('rejected_step_ids') or [])} |")

    parts = [_header(job), "", *rows]
    if details.get("plan_warning"):
        parts += ["", f"> {_md(details['plan_warning'])}"]
    if details.get("reason"):
        parts += ["", f"**Developer note:** {_md(details['reason'])}"]
    if details_url:
        label = "Review in DIWO" if awaiting else "Open the workflow in DIWO"
        parts += ["", f"**{label}:** [{label}]({details_url}) (DIWO sign-in required)."]
    else:
        parts += ["", f"DIWO workflow: `{workflow_id}`."]
    if awaiting and review_status == "plan_ready":
        parts += ["", PLAN_COMMAND_HELP]
    parts += ["", NOT_CHANGED_NOTE]

    text = None
    if steps:
        text = plan_steps_markdown(steps, approval, review_status,
                                   details.get("draft_decisions") or {})

    return {"title": title, "summary": "\n".join(parts), "text": text}, conclusion
