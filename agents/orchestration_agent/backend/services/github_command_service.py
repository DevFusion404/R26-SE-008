"""
/refactoriq PR commands
=======================
R26-SE-008 | Bandara S M Y M | IT22277886

The GitHub-native side of the review. Every command acts on the SAME records
DIWO uses — the shared draft selection and plan decisions on the PR-commit
mapping, the workflow's plan, the transformation records — through the same
business rules (services/github_review_service.py, workflow_service), so
GitHub and DIWO can never disagree.

WHO. The actor is the signed webhook's `sender`, never a name in the comment
text. A command that changes anything requires that GitHub user to have
write, maintain or admin permission on the repository, checked live through
the App, and the analysed commit to still be the open PR's head.

CONFIRMATION. Submitting a plan for transformation and submitting accepted
changes for commit both need an explicit second command
("... confirm") from the same user, within GITHUB_CONFIRMATION_TTL_SECONDS,
for the same decisions. A Check Run button press is itself that deliberate
second action.

REPLIES go to one bot-managed status comment, edited in place.
"""

import hashlib
import json
import logging
import re
import time

import config
from db import github_pr_workflow_repository as prs
from db import github_review_repository as reviews
from db.workflow_repository import get_workflow, parse_json_field
from services.access_service import AccessError
from services.github_command_parser import COMMANDS, PREFIX

logger = logging.getLogger("diwo.github.commands")

SELECTION_OPEN = ("smell_review", "plan_approval")


class CommandError(Exception):
    """The command cannot be carried out; the message is shown to the user."""


class RetryableCommandError(Exception):
    """A transient failure (GitHub unavailable); the job is retried."""


HELP = f"""**RefactorIQ commands** — one per comment, at the start of a line:

| Command | What it does |
|---|---|
| `{PREFIX} list` | Findings grouped by file, with their IDs |
| `{PREFIX} select S001 S004` | Add findings to your selection |
| `{PREFIX} select-group <file> <SmellType>` | Add every finding of one type in one file |
| `{PREFIX} deselect S004` · `clear-selection` | Remove from the selection |
| `{PREFIX} show-selection` | What is selected now (shared with DIWO) |
| `{PREFIX} generate-plan` | Ask the RDP agent for a plan for the selection |
| `{PREFIX} show-plan` | The plan, grouped by file |
| `{PREFIX} approve-plan P001 P002` · `reject-plan P003` | Decide on plan steps |
| `{PREFIX} submit-plan` | Submit the approved steps (then `submit-plan confirm`) |
| `{PREFIX} show-transformations` | Proposed changes and their validation |
| `{PREFIX} accept-transformation T001` · `reject-transformation T002` | Final decisions |
| `{PREFIX} accept-all-validated` | Accept every validated change |
| `{PREFIX} submit-refactoring` | Commit accepted changes (then `submit-refactoring confirm`) |
| `{PREFIX} cancel` | Reject every change not yet applied |

Changing anything requires write access to this repository. Nothing is
committed until accepted changes are explicitly submitted."""


# ─── Entry point ────────────────────────────────────────────────────────────

def process_command_job(job: dict, app) -> str:
    """Run one queued command. Returns a short outcome for the logs.

    Raises RetryableCommandError for transient failures; every other problem
    is answered in the status comment.
    """
    from flask import g

    command = json.loads(job.get("command_json") or "{}")
    login = command.get("actor_login")
    g.diwo_user = {"github_login": login, "email": None}
    mapping = prs.latest_mapping_for_pr(job["repo_full_name"], job["pr_number"])
    if mapping is None:
        return "no_mapping"            # nothing analysed for this PR: stay silent

    try:
        section, body = _run(command, mapping, app)
        outcome = "ok"
    except CommandError as exc:
        section, body, outcome = "Command not carried out", str(exc), "refused"
    except AccessError as exc:
        if exc.status == 503:
            raise RetryableCommandError(exc.message) from None
        section, body, outcome = "Command not carried out", exc.message, "refused"

    from services.github_comment_service import upsert_status_comment
    upsert_status_comment(app, mapping, "\n".join([
        f"### RefactorIQ — {section}", "",
        f"> `{_inline(command.get('raw'))}` — @{login}", "", body]))
    return outcome


def _inline(text) -> str:
    return str(text or "").replace("`", "'")[:200]


def _run(command: dict, mapping: dict, app) -> tuple[str, str]:
    from services.github_review_service import authorize_github_login, ensure_current_commit

    name, login = command.get("name"), command.get("actor_login")
    if name not in COMMANDS:
        return "Unknown command", f"`{_inline(name)}` is not a RefactorIQ command.\n\n{HELP}"
    if name == "help":
        return "Help", HELP

    if not mapping.get("workflow_id"):
        status = mapping.get("analysis_status")
        reason = {"no_smells": "CUQA found no code smells in the analysed commit.",
                  "skipped_fork": "fork pull requests are not analysed.",
                  "failed": "the analysis of the latest commit failed.",
                  "pending": "the latest commit is still being analysed."}.get(status, status)
        raise CommandError(f"There is nothing to review: {reason}")
    wf = get_workflow(mapping["workflow_id"])

    if COMMANDS[name]:
        authorize_github_login(mapping, login)
        ensure_current_commit(mapping)
        if command.get("source") == "check_run" and command.get("head_sha") not in (None, mapping["head_sha"]):
            raise CommandError("That button belongs to an older commit's check.")

    context = {"mapping": mapping, "wf": wf, "args": command.get("args") or [], "login": login,
               "identity": {"github_login": login, "email": None}, "app": app,
               "source": command.get("source"),
               "smells": parse_json_field(wf, "smells_json") or []}
    return HANDLERS[name](context)


# ─── Selection ──────────────────────────────────────────────────────────────

def _draft_selection(ctx) -> list:
    draft = prs.get_draft(ctx["mapping"], "draft_selection_json", None)
    if draft is None:
        draft = [s["id"] for s in parse_json_field(ctx["wf"], "selected_smells_json") or []]
    known = {s["id"] for s in ctx["smells"]}
    return [i for i in draft if i in known]


def _selection_reply(ctx, selected: list) -> str:
    from services.github_report_service import display_ids
    from services.github_check_service import _md

    ids = display_ids(ctx["smells"])
    by_id = {s["id"]: s for s in ctx["smells"]}
    lines = [f"Selected findings: **{len(selected)}**",
             f"Excluded findings: **{len(ctx['smells']) - len(selected)}**", ""]
    if selected:
        lines.append("Selected:")
        for sid in sorted(selected, key=lambda i: ids[i])[:60]:
            smell = by_id[sid]
            lines.append(f"- {ids[sid]} — {_md(smell.get('type'))} — "
                         f"{_md((smell.get('location') or {}).get('file'))}")
        if len(selected) > 60:
            lines.append(f"- …and {len(selected) - 60} more")
        lines += ["", f"Next action: `{PREFIX} generate-plan`"]
    else:
        lines.append(f"Nothing selected yet: `{PREFIX} select S001 S002`")
    return "\n".join(lines)


def _require_selection_open(ctx):
    if ctx["wf"]["status"] not in SELECTION_OPEN:
        raise CommandError(f"The selection can no longer be changed (workflow status: "
                           f"{ctx['wf']['status']}).")


def _resolve(ctx, tokens) -> list:
    from services.github_report_service import resolve_display_ids
    if not tokens:
        raise CommandError("Name the findings by ID, e.g. `S001 S004`.")
    ids, unknown = resolve_display_ids(ctx["smells"], tokens)
    if unknown:
        raise CommandError("Not findings of this pull request's analysed commit: "
                           + ", ".join(f"`{_inline(t)}`" for t in unknown[:20])
                           + ". Nothing was changed.")
    return ids


def _save_selection(ctx, selected):
    prs.set_draft(ctx["mapping"]["id"], "draft_selection_json", selected)
    return "Developer Selection", _selection_reply(ctx, selected)


def cmd_list(ctx):
    from services.github_report_service import compress_ids, group_findings
    from services.github_check_service import _md
    lines = []
    for entry in group_findings(ctx["smells"])[:40]:
        lines.append(f"**{_md(entry['file'])}**")
        for group in entry["groups"]:
            lines.append(f"- {_md(group['type'])} ({group['count']}, {group['severity']}) — "
                         f"{compress_ids(group['ids'], limit=12)}")
        lines.append("")
    lines.append(f"Select with `{PREFIX} select S001 S002` or "
                 f"`{PREFIX} select-group <file> <SmellType>`.")
    return "Findings", "\n".join(lines)


def cmd_show_selection(ctx):
    return "Developer Selection", _selection_reply(ctx, _draft_selection(ctx))


def cmd_select(ctx):
    _require_selection_open(ctx)
    current = _draft_selection(ctx)
    return _save_selection(ctx, current + [i for i in _resolve(ctx, ctx["args"]) if i not in current])


def cmd_select_group(ctx):
    _require_selection_open(ctx)
    if len(ctx["args"]) != 2:
        raise CommandError(f"Usage: `{PREFIX} select-group <file> <SmellType>` — both are required, "
                           "so a group command never selects more than one file's findings of one type.")
    path, smell_type = ctx["args"]
    matched = [s["id"] for s in ctx["smells"]
               if (s.get("location") or {}).get("file") == path and s.get("type") == smell_type]
    if not matched:
        raise CommandError(f"No {smell_type} findings in `{_inline(path)}`.")
    current = _draft_selection(ctx)
    return _save_selection(ctx, current + [i for i in matched if i not in current])


def cmd_deselect(ctx):
    _require_selection_open(ctx)
    remove = set(_resolve(ctx, ctx["args"]))
    return _save_selection(ctx, [i for i in _draft_selection(ctx) if i not in remove])


def cmd_clear_selection(ctx):
    _require_selection_open(ctx)
    return _save_selection(ctx, [])


# ─── Planning ───────────────────────────────────────────────────────────────

def _plan_reply(ctx, plan, title_note=""):
    from services.github_check_service import plan_steps_markdown
    mapping = prs.get_mapping_by_id(ctx["mapping"]["id"])
    steps = [s for s in (plan or {}).get("steps") or [] if isinstance(s, dict)]
    source = (plan or {}).get("source") or (plan or {}).get("plan_source")
    origin = ("RDP agent" if source == "rdp_agent" else
              "DIWO offline fallback planner (the RDP agent was unavailable)" if source else "—")
    body = [title_note, f"Planning source: **{origin}**", f"Steps: **{len(steps)}**",
            "Developer review: **required**", "",
            plan_steps_markdown(steps, (plan or {}).get("approval") or {},
                                "plan_approved" if ctx["wf"]["status"] == "plan_approved"
                                else "plan_ready",
                                prs.get_draft(mapping, "draft_plan_decisions_json", {})),
            "", f"Decide: `{PREFIX} approve-plan P001` · `{PREFIX} reject-plan P002` · "
                f"`{PREFIX} submit-plan`"]
    return "\n".join(line for line in body if line is not None)


def cmd_generate_plan(ctx):
    from services.github_review_service import plan_for_selection
    _require_selection_open(ctx)
    selected = _draft_selection(ctx)
    if not selected:
        raise CommandError(f"Nothing is selected. Use `{PREFIX} select S001 …` first.")
    payload = plan_for_selection(ctx["wf"], ctx["mapping"], ctx["identity"], selected,
                                 feedback={"reason": f"Selected on GitHub by @{ctx['login']}"})
    note = f"> {payload['plan_warning']}" if payload.get("plan_warning") else ""
    ctx["wf"] = get_workflow(ctx["wf"]["id"])
    return "RDP Planning", _plan_reply(ctx, payload.get("plan"), note)


def _current_plan(ctx):
    if ctx["wf"]["status"] not in ("plan_approval", "plan_approved", "transformation", "completed"):
        raise CommandError(f"There is no plan yet. Use `{PREFIX} generate-plan`.")
    return parse_json_field(ctx["wf"], "plan_json") or {}


def cmd_show_plan(ctx):
    return "RDP Planning", _plan_reply(ctx, _current_plan(ctx))


def _decide_steps(ctx, verdict):
    from services.github_check_service import is_manual_only, step_display_id
    from services.github_review_service import publish_review_status
    if ctx["wf"]["status"] != "plan_approval":
        raise CommandError(f"The plan is not open for decisions (status: {ctx['wf']['status']}).")
    plan = _current_plan(ctx)
    steps = {step_display_id(s): s for s in plan.get("steps") or [] if isinstance(s, dict)}
    if not ctx["args"]:
        raise CommandError("Name the plan steps, e.g. `P001 P002`.")
    chosen, unknown = [], []
    for token in ctx["args"]:
        match = re.fullmatch(r"[Pp]0*(\d+)", token)
        key = f"P{int(match.group(1)):03d}" if match else None
        (chosen if key in steps else unknown).append(key or token)
    if unknown:
        raise CommandError("Not steps of the current plan: " + ", ".join(f"`{_inline(t)}`" for t in unknown)
                           + ". Nothing was changed.")
    if verdict == "approve":
        manual = [k for k in chosen if is_manual_only(steps[k])]
        if manual:
            raise CommandError(", ".join(manual) + " can only be done by hand (manual-only); "
                               "they are never forwarded for automatic transformation. Reject them, "
                               "or decide them in DIWO. Nothing was changed.")
    draft = prs.get_draft(ctx["mapping"], "draft_plan_decisions_json", {})
    for key in chosen:
        draft[str(steps[key].get("step_id"))] = verdict
    prs.set_draft(ctx["mapping"]["id"], "draft_plan_decisions_json", draft)
    prs.update_mapping(ctx["mapping"]["id"], pending_confirmation=None)
    publish_review_status(ctx["wf"]["id"], "plan_ready", ctx["identity"], plan=plan,
                          details={"draft_decisions": draft})
    return "RDP Planning", _plan_reply(ctx, plan, f"{verdict.title()}d: {', '.join(chosen)}")


def cmd_approve_plan(ctx):
    return _decide_steps(ctx, "approve")


def cmd_reject_plan(ctx):
    return _decide_steps(ctx, "reject")


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]


def _confirmed(ctx, action, digest) -> bool:
    """True when this is the matching '... confirm' (or a button); else ask for it."""
    if ctx["source"] == "check_run":
        return True
    pending = prs.get_draft(ctx["mapping"], "pending_confirmation", None) or {}
    if "confirm" in [a.lower() for a in ctx["args"]]:
        if (pending.get("action") == action and pending.get("by") == ctx["login"]
                and pending.get("digest") == digest
                and time.time() - pending.get("at", 0) <= config.github_confirmation_ttl_seconds()):
            prs.update_mapping(ctx["mapping"]["id"], pending_confirmation=None)
            return True
        raise CommandError(f"There is no matching request to confirm. Run `{PREFIX} {action}` "
                           "first, then confirm it yourself within "
                           f"{config.github_confirmation_ttl_seconds() // 60} minutes.")
    prs.set_draft(ctx["mapping"]["id"], "pending_confirmation",
                  {"action": action, "by": ctx["login"], "digest": digest, "at": time.time()})
    return False


def cmd_submit_plan(ctx):
    from services.github_review_service import decide_plan
    if ctx["wf"]["status"] != "plan_approval":
        raise CommandError(f"The plan is not open for decisions (status: {ctx['wf']['status']}).")
    plan = _current_plan(ctx)
    draft = prs.get_draft(ctx["mapping"], "draft_plan_decisions_json", {})
    approved = [s for s in plan.get("steps") or [] if draft.get(str(s.get("step_id"))) == "approve"]
    if not approved:
        raise CommandError(f"No step is approved. Use `{PREFIX} approve-plan P001 …` first.")
    pending = [s for s in plan.get("steps") or [] if str(s.get("step_id")) not in draft]
    if not _confirmed(ctx, "submit-plan", _digest(draft)):
        return "Confirm Plan Submission", "\n".join([
            f"**{len(approved)}** step(s) approved, "
            f"**{sum(1 for v in draft.values() if v == 'reject')}** rejected, "
            f"**{len(pending)}** undecided (undecided steps are NOT forwarded).", "",
            f"To submit, comment `{PREFIX} submit-plan confirm` within "
            f"{config.github_confirmation_ttl_seconds() // 60} minutes."])
    result = decide_plan(ctx["wf"], ctx["mapping"], ctx["identity"], "approve",
                         {"decisions": draft,
                          "feedback": {"reason": f"Approved on GitHub by @{ctx['login']}"}})
    transformation = result.get("transformation") or {}
    next_step = ("SCTVA transformation has been queued; its validated changes will appear here."
                 if transformation.get("queued") else transformation.get("reason") or "")
    return "Plan Approved", f"Plan approved: {len(approved)} step(s) forwarded.\n\n{next_step}"


# ─── Final decisions ────────────────────────────────────────────────────────

def _records(ctx):
    records = reviews.list_transformations(ctx["wf"]["id"])
    if not records:
        raise CommandError("There are no proposed changes for this pull request yet.")
    return records


def cmd_show_transformations(ctx):
    from services.github_transformation_service import TESTS_NOTE, transformations_markdown
    table, _ = transformations_markdown(_records(ctx), parse_json_field(
        ctx["wf"], "transformation_result_json") or {}, previews=False)
    return "Proposed Changes", "\n".join([table, "", TESTS_NOTE,
                                           "", "Diff previews are on the **RefactorIQ / DIWO** check."])


def _transformation_decision(ctx, decisions):
    from services.github_transformation_service import DecisionError, decide, publish_transformations
    try:
        outcome = decide(ctx["wf"]["id"], decisions, f"github:{ctx['login']}")
    except DecisionError as exc:
        raise CommandError(exc.message + " Nothing was changed.") from None
    prs.update_mapping(ctx["mapping"]["id"], pending_confirmation=None)
    publish_transformations(ctx["app"], ctx["mapping"], ctx["wf"]["id"], comment=False)
    return "Proposed Changes", (f"Accepted: {', '.join(outcome['accepted']) or '—'} · "
                                f"Rejected: {', '.join(outcome['rejected']) or '—'}\n\n"
                                f"When ready: `{PREFIX} submit-refactoring`")


def _tids(ctx):
    if not ctx["args"]:
        raise CommandError("Name the changes, e.g. `T001 T002`.")
    return ctx["args"]


def cmd_accept_transformation(ctx):
    _records(ctx)
    return _transformation_decision(ctx, {t: "accept" for t in _tids(ctx)})


def cmd_reject_transformation(ctx):
    _records(ctx)
    return _transformation_decision(ctx, {t: "reject" for t in _tids(ctx)})


def cmd_accept_all_validated(ctx):
    proposed = [r["display_id"] for r in _records(ctx) if r["status"] == reviews.PROPOSED]
    if not proposed:
        raise CommandError("There is no validated change waiting for a decision.")
    return _transformation_decision(ctx, {t: "accept" for t in proposed})


def cmd_cancel(ctx):
    open_ = [r["display_id"] for r in _records(ctx)
             if r["status"] in (reviews.PROPOSED, reviews.ACCEPTED)]
    if not open_:
        raise CommandError("There is no pending change to reject.")
    return _transformation_decision(ctx, {t: "reject" for t in open_})


def cmd_submit_refactoring(ctx):
    from services.github_transformation_service import publish_transformations
    from services.github_writeback_service import WritebackRefused, request_writeback
    accepted = [r for r in _records(ctx) if r["status"] == reviews.ACCEPTED]
    if not accepted:
        raise CommandError("No change has been accepted. Use "
                           f"`{PREFIX} accept-transformation T001` first.")
    digest = _digest(sorted((r["display_id"], r["refactored_sha256"]) for r in accepted))
    if not _confirmed(ctx, "submit-refactoring", digest):
        return "Confirm Refactoring Submission", "\n".join(
            [f"These **{len(accepted)}** accepted change(s) will be committed to this pull request's "
             "branch (fast-forward only — never force-pushed):", ""]
            + [f"- {r['display_id']} — {r['file_path']}" for r in accepted]
            + ["", f"To apply, comment `{PREFIX} submit-refactoring confirm` within "
                   f"{config.github_confirmation_ttl_seconds() // 60} minutes."])
    try:
        result = request_writeback(ctx["mapping"], ctx["wf"]["id"], ctx["identity"])
    except WritebackRefused as exc:
        raise CommandError(exc.message) from None
    publish_transformations(ctx["app"], ctx["mapping"], ctx["wf"]["id"], comment=False)
    if result["status"] == "committed":
        return "Changes Applied", f"Already applied in commit `{result['commit_sha'][:7]}`."
    return "Applying Changes", "The accepted changes are being committed; this comment will be updated."


HANDLERS = {
    "list": cmd_list, "show-selection": cmd_show_selection, "select": cmd_select,
    "select-group": cmd_select_group, "deselect": cmd_deselect,
    "clear-selection": cmd_clear_selection, "generate-plan": cmd_generate_plan,
    "show-plan": cmd_show_plan, "approve-plan": cmd_approve_plan, "reject-plan": cmd_reject_plan,
    "submit-plan": cmd_submit_plan, "show-transformations": cmd_show_transformations,
    "accept-transformation": cmd_accept_transformation,
    "reject-transformation": cmd_reject_transformation,
    "accept-all-validated": cmd_accept_all_validated,
    "submit-refactoring": cmd_submit_refactoring, "cancel": cmd_cancel,
}
