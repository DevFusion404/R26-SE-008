"""
Audit trail detail
==================
R26-SE-008 | Bandara S M Y M | IT22277886

Turns the objects the workflow already holds — smells, plan steps,
transformation results — into the facts an audit entry has to carry to be worth
reading.

The problem this exists to fix
------------------------------
The audit trail recorded that a stage happened and almost nothing about what
happened in it:

    smells_selected   {"selected": ["src/A.java:41:0", "src/A.java:88:1"], ...}
    plan_generated    {"plan_id": "plan_7c2", "steps": 12, "source": "rdp_agent"}
    transformation_completed  {"status": "ok", "passed": 9, "failed": 1}

Every one of those is true and none of them answers the question the trail is
kept for: WHICH file, WHICH smell, WHICH refactoring, and what became of it.
"src/A.java:41:0" is an id, not a fact — reading it back six weeks later means
re-deriving the smell from a report that has since been overwritten. Nine steps
passed, but nobody can say which nine.

So each digest below records the *content* alongside the id: the file, the
smell type, the severity, the entity, the refactoring chosen for it, and the
recommendation DIWO attached. The trail then reads as a narrative of the run
rather than a list of stage names.

Size
----
An audit row is JSON in a SQLite column, and a repository report can carry
hundreds of smells. Every list here is therefore capped at DETAIL_LIMIT and
reports its own truncation (`omitted`), while the COUNTS are always exact and
computed over the whole input. A truncated list that silently claimed to be
complete would be worse than no list at all.

Pure functions. Nothing here reads the database, calls an agent, or logs; the
services assemble these dicts and hand them to log_event().
"""

from collections import Counter

__all__ = [
    "DETAIL_LIMIT",
    "smell_digest", "smells_by_file", "smell_type_totals", "severity_totals",
    "step_digest", "steps_by_file", "refactoring_totals", "plan_digest",
    "decision_digest", "transformation_digest", "capped",
]

#: How many individual items one audit entry will name. Beyond this the entry
#: keeps the counts and records how many it did not list.
DETAIL_LIMIT = 40


def capped(items, limit=DETAIL_LIMIT):
    """(first `limit` items, how many were dropped)."""
    items = list(items or [])
    if len(items) <= limit:
        return items, 0
    return items[:limit], len(items) - limit


def _file_of(smell):
    """A smell's file, wherever the normalizer happened to put it."""
    location = smell.get("location") or {}
    return (location.get("file")
            or smell.get("relative_path")
            or smell.get("file")
            or "(unknown file)")


def _lines_of(smell):
    location = smell.get("location") or {}
    lines = location.get("lines")
    if isinstance(lines, list) and lines:
        return lines
    line = smell.get("line") or location.get("line")
    return [line] if line else []


# ─────────────────────────────────────────────────────────────────────────────
# Smells
# ─────────────────────────────────────────────────────────────────────────────

def smell_digest(smell):
    """One smell, as the audit trail should remember it.

    The id is kept so the row still joins to the report, but it is no longer
    the only thing recorded: type, severity, file and entity are what make the
    entry readable without the report in hand.
    """
    if not isinstance(smell, dict):
        return {}

    digest = {
        "id": smell.get("id"),
        "type": smell.get("type") or "Unknown",
        "severity": (smell.get("severity") or "unknown").lower(),
        "file": _file_of(smell),
    }
    if smell.get("entity"):
        digest["entity"] = smell["entity"]
    lines = _lines_of(smell)
    if lines:
        digest["lines"] = lines
    return digest


def smell_type_totals(smells):
    """{'Long Method': 4, 'Feature Envy': 2} over every smell given."""
    return dict(Counter(
        (s.get("type") or "Unknown") for s in smells or [] if isinstance(s, dict)
    ))


def severity_totals(smells):
    """{'high': 3, 'medium': 5, 'low': 1} over every smell given."""
    return dict(Counter(
        (s.get("severity") or "unknown").lower()
        for s in smells or [] if isinstance(s, dict)
    ))


def smells_by_file(smells):
    """Per-file rollup: which file, how many smells, and of what kinds.

    This is the answer to "which files had code smells detected", which the
    trail could not previously give at all. Files are ordered by smell count so
    the worst offender is first rather than whichever the walker happened to
    reach first.
    """
    grouped = {}
    for smell in smells or []:
        if not isinstance(smell, dict):
            continue
        path = _file_of(smell)
        entry = grouped.setdefault(path, {"file": path, "count": 0, "types": Counter(),
                                          "severities": Counter()})
        entry["count"] += 1
        entry["types"][smell.get("type") or "Unknown"] += 1
        entry["severities"][(smell.get("severity") or "unknown").lower()] += 1

    rows = [
        {"file": e["file"], "count": e["count"],
         "types": dict(e["types"]), "severities": dict(e["severities"])}
        for e in grouped.values()
    ]
    rows.sort(key=lambda r: (-r["count"], r["file"]))
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Plan steps
# ─────────────────────────────────────────────────────────────────────────────

def step_digest(step, decision=None):
    """One plan step: the smell, the refactoring chosen for it, and the verdict.

    `smell_type -> refactoring` is the single most useful pair in the whole
    trail — it is the answer to "what did the system decide to do about this
    problem" — so it is recorded even when RDP left the other fields empty.
    """
    if not isinstance(step, dict):
        return {}

    target = step.get("target") or {}
    support = step.get("decision_support") if isinstance(
        step.get("decision_support"), dict) else {}

    digest = {
        "step_id": step.get("step_id"),
        "smell_id": step.get("smell_id"),
        "smell_type": step.get("smell_type"),
        "refactoring": step.get("refactoring"),
        "file": target.get("file") or "(module level)",
        "risk": step.get("risk"),
        "impact": step.get("impact") or step.get("expected_impact"),
    }

    entity = ".".join(p for p in (target.get("class"), target.get("method")) if p)
    if entity:
        digest["entity"] = entity
    if isinstance(target.get("lines"), list) and target["lines"]:
        digest["lines"] = target["lines"]
    if isinstance(step.get("score"), (int, float)):
        digest["rdp_score"] = round(float(step["score"]), 3)

    # What DIWO advised, so the trail can be read against what was done.
    if support.get("category"):
        digest["recommendation"] = support["category"]
        if isinstance(support.get("score"), (int, float)):
            digest["decision_support_score"] = support["score"]
    if decision:
        digest["decision"] = decision
    return digest


def refactoring_totals(steps):
    """{'Extract Method': 5, 'Move Method': 2} over every step given."""
    return dict(Counter(
        (s.get("refactoring") or "Unknown")
        for s in steps or [] if isinstance(s, dict)
    ))


def steps_by_file(steps):
    """Per-file rollup of planned work: which refactorings land where."""
    grouped = {}
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        path = (step.get("target") or {}).get("file") or "(module level)"
        entry = grouped.setdefault(path, {"file": path, "count": 0,
                                          "refactorings": Counter()})
        entry["count"] += 1
        entry["refactorings"][step.get("refactoring") or "Unknown"] += 1

    rows = [
        {"file": e["file"], "count": e["count"], "refactorings": dict(e["refactorings"])}
        for e in grouped.values()
    ]
    rows.sort(key=lambda r: (-r["count"], r["file"]))
    return rows


def plan_digest(steps, decisions=None):
    """The whole plan, itemised — the block `plan_generated` records.

    `decisions` is the optional {step_id: verdict} map, so the same helper
    serves both "here is what was planned" and "here is what was decided".
    """
    steps = [s for s in (steps or []) if isinstance(s, dict)]
    decisions = decisions if isinstance(decisions, dict) else {}

    def verdict(step):
        step_id = step.get("step_id")
        return decisions.get(str(step_id)) or decisions.get(step_id)

    listed, omitted = capped(steps)
    # Deliberately NOT called "steps": the plan_generated entry already carries
    # an integer  count, and shadowing it with a list would silently
    # change the type of a field other readers already consume.
    detail = {
        "total_steps": len(steps),
        "by_file": steps_by_file(steps),
        "refactorings": refactoring_totals(steps),
        "step_detail": [step_digest(s, verdict(s)) for s in listed],
    }
    if omitted:
        detail["step_detail_omitted"] = omitted
    return detail


def decision_digest(steps, decisions):
    """What the developer decided, itemised by verdict.

    Approved / rejected / manual are listed separately rather than as one list
    with a field on each row, because the questions asked of this record later
    are "what was actually transformed" and "what did they turn down" — and
    those should not require filtering to answer.
    """
    steps = [s for s in (steps or []) if isinstance(s, dict)]
    decisions = decisions if isinstance(decisions, dict) else {}

    buckets = {"approved": [], "rejected": [], "manual": [], "pending": []}
    for step in steps:
        step_id = step.get("step_id")
        verdict = decisions.get(str(step_id)) or decisions.get(step_id)
        key = {"approve": "approved", "reject": "rejected",
               "manual": "manual"}.get(verdict, "pending")
        buckets[key].append(step)

    detail = {}
    for key, group in buckets.items():
        listed, omitted = capped(group)
        detail[key] = {
            "count": len(group),
            "steps": [step_digest(s) for s in listed],
        }
        if omitted:
            detail[key]["omitted"] = omitted
    return detail


# ─────────────────────────────────────────────────────────────────────────────
# Transformation
# ─────────────────────────────────────────────────────────────────────────────

def transformation_digest(result):
    """Per-step outcome of a transformation run.

    "9 passed, 1 failed" is not enough to act on: the one that failed is the
    only interesting row, and the trail previously did not say which it was.
    """
    if not isinstance(result, dict):
        return {}

    steps = [s for s in (result.get("steps") or []) if isinstance(s, dict)]
    listed, omitted = capped(steps)

    outcomes = []
    for step in listed:
        row = {
            "step_id": step.get("step_id"),
            "refactoring": step.get("refactoring") or step.get("action") or step.get("type"),
            "file": step.get("file") or (step.get("target") or {}).get("file"),
            "status": step.get("status"),
        }
        if step.get("message"):
            row["message"] = step["message"]
        if step.get("validation"):
            row["validation"] = step["validation"]
        outcomes.append({k: v for k, v in row.items() if v is not None})

    # Same key as plan_digest uses, for one reason: a reader that knows how to
    # render itemised steps should not need a different field name per stage.
    detail = {"step_detail": outcomes}
    if omitted:
        detail["step_detail_omitted"] = omitted

    files = result.get("files")
    if isinstance(files, list) and files:
        names, files_omitted = capped(
            [f.get("path") or f.get("file") for f in files if isinstance(f, dict)])
        detail["files_changed"] = [n for n in names if n]
        if files_omitted:
            detail["files_omitted"] = files_omitted

    return detail
