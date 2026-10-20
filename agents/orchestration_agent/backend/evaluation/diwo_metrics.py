"""
DIWO agent evaluation metrics
=============================
R26-SE-008 | Bandara S M Y M | IT22277886

Measures the Developer Interaction & Workflow Orchestration agent from what it
already records — the workflows table, the audit trail and the developer
feedback rows — so every number traces back to a stored event, not to a demo.

Five groups, each answering one evaluation question:

  A. Workflow effectiveness  Does a session get from smells to accepted code?
  B. Decision support        Do developers agree with DIWO's recommendations?
  C. Orchestration           Do the agent hand-offs (CUQA -> RDP -> SCTVA) work?
  D. Efficiency              How long does each human-in-the-loop stage take?
  E. Developer satisfaction  How do developers rate the outcome?

compute_metrics() is pure: it takes the rows and returns the numbers, so the
same function serves the CLI, an API endpoint and the tests. The CLI reads
through db.workflow_repository, so it works against SQLite or Supabase —
whichever DATABASE_PROVIDER selects.

    python -m evaluation.diwo_metrics            # readable report
    python -m evaluation.diwo_metrics --json     # machine-readable
"""

import json
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime

#: The stages a session passes through, each marked by the audit action that
#: proves the stage was completed.
FUNNEL = [
    ("started", "workflow_started"),
    ("smells_selected", "smells_selected"),
    ("plan_generated", "plan_generated"),
    ("plan_approved", "plan_approved"),
    ("transformation_executed", "transformation_completed"),
    ("results_accepted", "transformation_accepted"),
    ("completed", "workflow_completed"),
]

#: DIWO recommendation categories, and the decision that AGREES with each.
#: `review` asks the developer to look closely, so either decision agrees.
AGREEING_DECISION = {
    "recommended": {"approved"},
    "review": {"approved", "rejected", "manual"},
    "not_recommended": {"rejected", "manual"},
    "manual_only": {"manual", "rejected"},
}

_DIWO_TAG = re.compile(r"\[DIWO:\s*([a-z_]+)")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _ratio(num, den):
    return round(num / den, 4) if den else None


def _parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _details(row):
    raw = row.get("details_json") if "details_json" in row else row.get("details")
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}


def _minutes(start, end):
    if not start or not end or end < start:
        return None
    return round((end - start).total_seconds() / 60, 2)


def _summary(values):
    """count / median / mean / min / max of a list of numbers."""
    values = [v for v in values if v is not None]
    if not values:
        return {"n": 0, "median": None, "mean": None, "min": None, "max": None}
    return {
        "n": len(values),
        "median": round(statistics.median(values), 2),
        "mean": round(statistics.mean(values), 2),
        "min": round(min(values), 2),
        "max": round(max(values), 2),
    }


# ── Metric groups ────────────────────────────────────────────────────────────

def _workflow_effectiveness(workflows, logs_by_wf):
    reached = Counter()
    resets = Counter()
    for wf in workflows:
        actions = {row["action"] for row in logs_by_wf.get(wf["id"], [])}
        actions.add("workflow_started")          # every stored workflow was started
        for stage, action in FUNNEL:
            if action in actions:
                reached[stage] += 1
        for row in logs_by_wf.get(wf["id"], []):
            if row["action"] in ("reset_to_smell_review", "reset_to_plan_approval"):
                resets[row["action"]] += 1

    total = len(workflows)
    funnel = []
    previous = total
    for stage, _ in FUNNEL:
        count = reached[stage]
        funnel.append({
            "stage": stage,
            "workflows": count,
            "share_of_all": _ratio(count, total),
            "conversion_from_previous": _ratio(count, previous),
        })
        previous = count

    workflows_with_reset = sum(
        1 for wf in workflows
        if any(r["action"].startswith("reset_to_") for r in logs_by_wf.get(wf["id"], [])))

    return {
        "total_workflows": total,
        "status_distribution": dict(Counter(wf.get("status") for wf in workflows)),
        "funnel": funnel,
        # Sessions that produced transformed code the developer accepted.
        "end_to_end_success_rate": _ratio(reached["results_accepted"], total),
        # Of the sessions that reached a plan, how many went all the way.
        "plan_to_acceptance_rate": _ratio(reached["results_accepted"], reached["plan_generated"]),
        "rework": {
            "workflows_with_a_step_back": workflows_with_reset,
            "rework_rate": _ratio(workflows_with_reset, total),
            "by_type": dict(resets),
        },
    }


def _latest(rows, action):
    matching = [r for r in rows if r["action"] == action]
    return matching[-1] if matching else None


def _decision_support(workflows, logs_by_wf, feedback):
    # Stage 1: how much of what CUQA found the developer chose to act on.
    selected = excluded = 0
    for wf in workflows:
        row = _latest(logs_by_wf.get(wf["id"], []), "smells_selected")
        if row:
            d = _details(row)
            selected += int(d.get("selected_count") or 0)
            excluded += int(d.get("excluded_count") or 0)

    # Stage 2: one confusion table of DIWO's category against the developer's
    # decision, from the per-step feedback rows. Not from the plan_approved
    # audit event: a plan is "modified" first, which drops the rejected steps,
    # so the approval event alone would show every review as 100% approved.
    # Each row carries the category DIWO showed as "[DIWO: <category>...]";
    # rows are deduplicated per (workflow, step) where the step key is known.
    table = defaultdict(Counter)
    seen = set()
    for f in feedback:
        action = f.get("action")
        if action not in ("plan_step_accepted", "plan_step_rejected"):
            continue
        if f.get("step_key"):
            key = (f.get("workflow_id"), f["step_key"])
            if key in seen:
                continue
            seen.add(key)
        match = _DIWO_TAG.search(f.get("reason") or "")
        category = match.group(1) if match else "unassessed"
        table[category]["approved" if action == "plan_step_accepted" else "rejected"] += 1

    decided = sum(sum(c.values()) for c in table.values())
    assessed = {k: v for k, v in table.items() if k in AGREEING_DECISION}
    agree = sum(sum(n for dec, n in counts.items() if dec in AGREEING_DECISION[cat])
                for cat, counts in assessed.items())
    assessed_total = sum(sum(c.values()) for c in assessed.values())
    approved = sum(c["approved"] for c in table.values())

    # "Recommended" behaves like a positive prediction: precision is how often
    # a step DIWO recommended was approved.
    rec = table.get("recommended", Counter())
    not_rec = table.get("not_recommended", Counter())
    overrides = rec["rejected"] + rec["manual"] + not_rec["approved"] + \
        table.get("manual_only", Counter())["approved"]

    # Cross-check from the per-step feedback rows: the tag recorded with each
    # accepted / rejected step.
    tagged = Counter()
    for f in feedback:
        if f.get("action") in ("plan_step_accepted", "plan_step_rejected"):
            match = _DIWO_TAG.search(f.get("reason") or "")
            tagged[(match.group(1) if match else "untagged", f["action"])] += 1

    return {
        "smell_selection": {
            "selected": selected,
            "excluded": excluded,
            "selection_rate": _ratio(selected, selected + excluded),
        },
        "plan_steps_decided": decided,
        "step_approval_rate": _ratio(approved, decided),
        "confusion": {cat: dict(c) for cat, c in table.items()},
        # Share of assessed steps where the developer did what DIWO advised.
        # `review` counts as agreeing whatever was decided, so this is lenient.
        "recommendation_agreement_rate": _ratio(agree, assessed_total),
        # The strict version: only the categories that advise one way.
        "decisive_agreement_rate": _ratio(
            sum(sum(n for dec, n in counts.items() if dec in AGREEING_DECISION[cat])
                for cat, counts in assessed.items() if cat != "review"),
            sum(sum(counts.values()) for cat, counts in assessed.items() if cat != "review")),
        "recommended_precision": _ratio(rec["approved"], sum(rec.values())),
        "not_recommended_rejection_rate": _ratio(not_rec["rejected"] + not_rec["manual"],
                                                 sum(not_rec.values())),
        "override_rate": _ratio(overrides, assessed_total),
        "feedback_rows_by_tag": {f"{k[0]}:{k[1]}": v for k, v in tagged.items()},
    }


def _orchestration(workflows, logs_by_wf):
    plans_rdp = plans_fallback = 0
    sctva_runs = sctva_success = rollbacks = 0
    actions = executable = noops = 0
    files_sent = files_missing = 0
    steps_passed = steps_failed = 0
    accepted_files = rejected_files = 0

    for wf in workflows:
        for row in logs_by_wf.get(wf["id"], []):
            d = _details(row)
            if row["action"] == "plan_generated":
                if d.get("source") == "rdp_agent":
                    plans_rdp += 1
                else:
                    plans_fallback += 1
            elif row["action"] == "sctva_transformation_executed":
                sctva_runs += 1
                sctva_success += bool(d.get("success"))
                rollbacks += bool(d.get("rollback"))
                actions += int(d.get("actions") or 0)
                executable += int(d.get("executable") or 0)
                noops += int(d.get("noops") or 0)
                files_sent += int(d.get("files_sent") or 0)
                files_missing += int(d.get("files_missing") or 0)
            elif row["action"] == "transformation_completed":
                steps_passed += int(d.get("passed") or 0)
                steps_failed += int(d.get("failed") or 0)
            elif row["action"] == "transformation_accepted":
                accepted_files += len(d.get("accepted_files") or [])
                rejected_files += len(d.get("rejected_files") or [])

    return {
        "plans": {
            "from_rdp_agent": plans_rdp,
            "fallback": plans_fallback,
            "rdp_availability": _ratio(plans_rdp, plans_rdp + plans_fallback),
        },
        "sctva": {
            "runs": sctva_runs,
            "reported_success_rate": _ratio(sctva_success, sctva_runs),
            "rollback_rate": _ratio(rollbacks, sctva_runs),
            # How much of the approved plan SCTVA could actually execute —
            # the capability gap DIWO's Manual-only category exists to expose.
            "executable_action_rate": _ratio(executable, actions),
            "noop_rate": _ratio(noops, actions),
            "source_file_resolution_rate": _ratio(files_sent - files_missing, files_sent),
        },
        "transformation_steps": {
            "passed": steps_passed,
            "failed": steps_failed,
            "step_pass_rate": _ratio(steps_passed, steps_passed + steps_failed),
        },
        "results_review": {
            "accepted_files": accepted_files,
            "rejected_files": rejected_files,
            "file_acceptance_rate": _ratio(accepted_files, accepted_files + rejected_files),
        },
    }


def _efficiency(workflows, logs_by_wf):
    """Minutes between stage-completing events, per workflow."""
    spans = defaultdict(list)
    for wf in workflows:
        rows = logs_by_wf.get(wf["id"], [])
        first = {}
        for row in rows:
            first.setdefault(row["action"], _parse_time(row.get("timestamp")))
        start = _parse_time(wf.get("created_at"))
        selected = first.get("smells_selected")
        approved = first.get("plan_approved")
        transformed = first.get("transformation_completed")
        accepted = first.get("transformation_accepted")
        spans["smell_review_minutes"].append(_minutes(start, selected))
        spans["plan_review_minutes"].append(_minutes(selected, approved))
        spans["transformation_minutes"].append(_minutes(approved, transformed))
        spans["results_review_minutes"].append(_minutes(transformed, accepted))
        spans["end_to_end_minutes"].append(_minutes(start, accepted))
    return {name: _summary(values) for name, values in spans.items()}


def _satisfaction(feedback):
    ratings = [int(f["rating"]) for f in feedback
               if f.get("rating") not in (None, "") and f.get("action") in
               ("plan_approved", "transformation_accepted", "workflow_completed")]
    by_value = Counter(ratings)
    return {
        "ratings": len(ratings),
        "mean_rating": round(statistics.mean(ratings), 2) if ratings else None,
        "distribution": {str(k): by_value[k] for k in sorted(by_value)},
        "share_4_or_5": _ratio(sum(1 for r in ratings if r >= 4), len(ratings)),
    }


# ── Entry points ─────────────────────────────────────────────────────────────

def compute_metrics(workflows, logs_by_wf, feedback):
    """All five metric groups from stored rows. Pure — no I/O."""
    workflows = list(workflows or [])
    logs_by_wf = {k: sorted(v, key=lambda r: str(r.get("timestamp") or ""))
                  for k, v in (logs_by_wf or {}).items()}
    feedback = list(feedback or [])
    return {
        "dataset": {
            "workflows": len(workflows),
            "audit_events": sum(len(v) for v in logs_by_wf.values()),
            "feedback_rows": len(feedback),
            "first_workflow": min((wf.get("created_at") or "" for wf in workflows), default=None),
            "last_workflow": max((wf.get("created_at") or "" for wf in workflows), default=None),
        },
        "A_workflow_effectiveness": _workflow_effectiveness(workflows, logs_by_wf),
        "B_decision_support": _decision_support(workflows, logs_by_wf, feedback),
        "C_orchestration": _orchestration(workflows, logs_by_wf),
        "D_efficiency": _efficiency(workflows, logs_by_wf),
        "E_satisfaction": _satisfaction(feedback),
    }


def load_and_compute():
    """Read every workflow, its audit trail and the feedback through the
    configured repository (SQLite or Supabase) and compute the metrics."""
    from db import workflow_repository as repo

    workflows = repo.list_workflows() or []
    logs_by_wf = {wf["id"]: repo.get_audit_logs(wf["id"]) or [] for wf in workflows}
    metrics = compute_metrics(workflows, logs_by_wf, repo.export_feedback_dataset() or [])
    metrics["dataset"]["provider"] = repo.active_backend()
    return metrics


def _pct(value):
    return "n/a" if value is None else f"{value * 100:.1f}%"


def format_report(m):
    a, b, c, d, e = (m[k] for k in ("A_workflow_effectiveness", "B_decision_support",
                                    "C_orchestration", "D_efficiency", "E_satisfaction"))
    ds = m["dataset"]
    lines = [
        "DIWO agent evaluation",
        f"Dataset: {ds['workflows']} workflows, {ds['audit_events']} audit events, "
        f"{ds['feedback_rows']} feedback rows ({ds.get('provider', 'rows')}; "
        f"{(ds['first_workflow'] or '')[:10]} to {(ds['last_workflow'] or '')[:10]})",
        "",
        "A. Workflow effectiveness",
    ]
    for row in a["funnel"]:
        lines.append(f"   {row['stage']:<24} {row['workflows']:>4}   "
                     f"{_pct(row['share_of_all']):>7} of all   "
                     f"{_pct(row['conversion_from_previous']):>7} from previous")
    lines += [
        f"   End-to-end success rate         {_pct(a['end_to_end_success_rate'])}",
        f"   Plan -> accepted code           {_pct(a['plan_to_acceptance_rate'])}",
        f"   Rework rate (stepped back)      {_pct(a['rework']['rework_rate'])}",
        "",
        "B. Decision support",
        f"   Smell selection rate            {_pct(b['smell_selection']['selection_rate'])}"
        f"  ({b['smell_selection']['selected']} selected / {b['smell_selection']['excluded']} excluded)",
        f"   Plan steps decided              {b['plan_steps_decided']}",
        f"   Step approval rate              {_pct(b['step_approval_rate'])}",
        f"   Recommendation agreement        {_pct(b['recommendation_agreement_rate'])}",
        f"   Decisive agreement (no review)  {_pct(b['decisive_agreement_rate'])}",
        f"   'Recommended' precision         {_pct(b['recommended_precision'])}",
        f"   'Not recommended' rejected      {_pct(b['not_recommended_rejection_rate'])}",
        f"   Override rate                   {_pct(b['override_rate'])}",
        f"   Category x decision             {b['confusion']}",
        "",
        "C. Orchestration",
        f"   RDP plan availability           {_pct(c['plans']['rdp_availability'])}"
        f"  ({c['plans']['from_rdp_agent']} RDP / {c['plans']['fallback']} fallback)",
        f"   SCTVA runs                      {c['sctva']['runs']}",
        f"   SCTVA reported success          {_pct(c['sctva']['reported_success_rate'])}",
        f"   SCTVA rollback rate             {_pct(c['sctva']['rollback_rate'])}",
        f"   Executable action rate          {_pct(c['sctva']['executable_action_rate'])}",
        f"   Source file resolution          {_pct(c['sctva']['source_file_resolution_rate'])}",
        f"   Transformation step pass rate   {_pct(c['transformation_steps']['step_pass_rate'])}",
        f"   Result file acceptance rate     {_pct(c['results_review']['file_acceptance_rate'])}",
        "",
        "D. Efficiency (minutes: median / mean, n)",
    ]
    for name, s in d.items():
        lines.append(f"   {name:<30} {s['median'] if s['n'] else 'n/a':>8} / "
                     f"{s['mean'] if s['n'] else 'n/a':>8}   n={s['n']}")
    lines += [
        "",
        "E. Developer satisfaction",
        f"   Ratings                         {e['ratings']}  mean {e['mean_rating']}  "
        f"4-5: {_pct(e['share_4_or_5'])}  {e['distribution']}",
    ]
    return "\n".join(lines)


def main(argv=None):
    import argparse
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    parser = argparse.ArgumentParser(description="Compute DIWO agent evaluation metrics.")
    parser.add_argument("--json", action="store_true", help="print JSON instead of a report")
    args = parser.parse_args(argv)

    from app import create_app
    app = create_app(start_github_worker=False)
    with app.app_context():
        metrics = load_and_compute()
    print(json.dumps(metrics, indent=2, default=str) if args.json else format_report(metrics))


if __name__ == "__main__":
    main()
