"""
Refactoring plan shaping
========================
R26-SE-008 | Bandara S M Y M | IT22277886

Everything that turns a smell report into a plan, or one plan shape into
another. Pure functions only — deciding *when* to call them, and talking to
the RDP agent, is services/planning_service.py's job.

Contents:
  * REFACTORING_MAP        smell type -> (refactoring, risk, impact)
  * build_rdp_plan_input   filtered CUQA report -> the payload RDP is sent
  * normalize_rdp_plan     RDP's plan -> the shape the DIWO workflow expects
  * build_approved_plan    a plan -> only the steps the developer approved
  * generate_refactoring_plan  the offline fallback planner
  * generate_updated_plan_report  live re-ranking from developer preferences

Moved out of diwo/orchestrator.py with no behavioural change.
"""

from typing import Optional
from datetime import datetime, timezone

from domain.cuqa_normalizer import summarize_files

REFACTORING_MAP = {
    "Long Method":          ("Extract Method", "low", "high"),
    "God Class":            ("Extract Class", "medium", "high"),
    "Feature Envy":         ("Move Method", "medium", "high"),
    "Duplicate Code":       ("Extract Method", "low", "high"),
    "Long Parameter List":  ("Introduce Parameter Object", "low", "high"),
    "Data Clumps":          ("Extract Class", "medium", "medium"),
    "Primitive Obsession":  ("Replace Data Value with Object", "low", "medium"),
    "Shotgun Surgery":      ("Move Method", "medium", "high"),
    "Divergent Change":     ("Extract Class", "medium", "medium"),
    "Dead Code":            ("Remove Dead Code", "low", "medium"),
    "Comments":             ("Rename Method", "low", "low"),
    "Large Class":          ("Extract Class", "medium", "high"),
    "Switch Statements":    ("Replace Conditional with Polymorphism", "high", "high"),
    "Lazy Class":           ("Inline Class", "low", "low"),
    "Speculative Generality": ("Collapse Hierarchy", "medium", "medium"),
}

# CUQA emits PascalCase smell types (LongMethod) rather than the spaced names
# above, and adds C-specific ones. Same (refactoring, risk, impact) contract.
REFACTORING_MAP.update({
    "LongMethod":            ("Extract Method", "low", "high"),
    "LongFunction":          ("Extract Method", "low", "high"),
    "TooManyParameters":     ("Introduce Parameter Object", "low", "high"),
    "SwitchStatements":      ("Replace Conditional with Polymorphism", "high", "high"),
    "MessageChains":         ("Hide Delegate", "low", "medium"),
    "LargeClass":            ("Extract Class", "medium", "high"),
    "LazyClass":             ("Inline Class", "low", "low"),
    "PrimitiveObsession":    ("Replace Data Value with Object", "low", "medium"),
    "InappropriateIntimacy": ("Move Method", "medium", "medium"),
    "SpeculativeGenerality": ("Collapse Hierarchy", "medium", "medium"),
    "DuplicateCode":         ("Extract Method", "low", "high"),
    "FeatureEnvy":           ("Move Method", "medium", "high"),
    "DataClumps":            ("Extract Class", "medium", "medium"),
    "DeadCode":              ("Remove Dead Code", "low", "medium"),
    "MagicNumber":           ("Replace Magic Number with Symbolic Constant", "low", "medium"),
    "BareExcept":            ("Replace Bare Except with Specific Exception", "low", "medium"),
    "Comments":              ("Rename Method", "low", "low"),
    # C-specific (CUQA c_ast_parser)
    "DeepNesting":           ("Replace Nested Conditional with Guard Clauses", "medium", "high"),
    "UnsafeFunctionUsage":   ("Replace Unsafe Call with Safe Variant", "medium", "high"),
    "GlobalVariable":        ("Encapsulate Field", "medium", "medium"),
    "LargeHeaderFile":       ("Extract Class", "medium", "medium"),
})


# ─────────────────────────────────────────────────────────────────────────────
# RDP Agent hand-off
# ─────────────────────────────────────────────────────────────────────────────

def build_rdp_plan_input(updated_report: Optional[dict]) -> dict:
    """Narrow the updated smell report down to what the RDP agent should plan.

    The report keeps every analysed file so the developer can see what was
    excluded, but only the selected smells survive the filter. Files left with
    no smells are dropped here for two reasons:

      * RDP would skip them anyway, and
      * RDP takes ``files[0]["file"]`` as the plan's target, so an unselected
        file sitting first would name the whole plan after itself.

    `file` is set to the repository-relative path rather than the bare
    filename: RDP copies it into every step's ``parameters.source_file``, and
    the Transformation stage resolves that path against the CUQA workspace.
    """
    # Bound once rather than `(updated_report or {})` at each use: the repeated
    # form guards every read but still leaves the last one subscripting the
    # possibly-None original, which is the kind of near-miss that only stays
    # correct by accident.
    report = updated_report or {}

    files = []
    for entry in report.get("files") or []:
        smells = entry.get("code_smells") or []
        if not smells:
            continue

        relative_path = entry.get("relative_path") or entry.get("file") or "unknown"
        files.append({**entry, "file": relative_path, "relative_path": relative_path})

    # Recompute every total from the files actually being sent. Carrying the
    # report's own figures through would leave the payload self-contradicting —
    # a summary describing files that are not in it.
    summary = dict(report.get("summary") or {})
    summary.update(summarize_files(files))

    payload = {"files": files, "summary": summary}
    if report.get("repo_name"):
        payload["repo_name"] = report["repo_name"]
    return payload


def _relative_path_index(plan_input: Optional[dict]) -> dict:
    """Map basename -> repo-relative path, for names that are unambiguous.

    RDP reduces every path to its basename while translating the report
    (``file_path.split("/")[-1]`` in rdp_agent/app.py), so the plan comes back
    naming "Helper.java" where the report said "src/util/Helper.java". The
    Transformation stage resolves those paths against the CUQA workspace, so
    the folders are put back here.

    Names that appear on more than one path are left alone: guessing between
    two different Helper.java would point a refactoring at the wrong file.
    """
    counts = {}
    for entry in (plan_input or {}).get("files") or []:
        path = entry.get("relative_path") or entry.get("file") or ""
        if not path:
            continue
        base = path.replace("\\", "/").rsplit("/", 1)[-1]
        counts.setdefault(base, set()).add(path)

    return {base: next(iter(paths)) for base, paths in counts.items() if len(paths) == 1}


def _restore_relative_paths(plan: dict, index: dict) -> dict:
    """Put repo-relative paths back onto a plan's steps."""
    if not index:
        return plan

    def upgrade(value):
        if not isinstance(value, str) or "/" in value or "\\" in value:
            return value
        return index.get(value, value)

    for step in plan.get("steps") or []:
        target = step.get("target")
        if isinstance(target, dict) and target.get("file"):
            target["file"] = upgrade(target["file"])

        params = step.get("parameters")
        if isinstance(params, dict) and params.get("source_file"):
            params["source_file"] = upgrade(params["source_file"])

        location = step.get("location")
        if isinstance(location, dict) and location.get("file"):
            location["file"] = upgrade(location["file"])

    if isinstance(plan.get("target"), str):
        plan["target"] = upgrade(plan["target"])

    return plan


def normalize_rdp_plan(plan: dict, plan_input: Optional[dict] = None) -> dict:
    """Reshape an RDP plan into the structure the DIWO workflow expects.

    RDP serializes ``summary`` as a human-readable string; every other stage
    here reads ``plan["summary"]["total_steps"]``. The text is preserved under
    ``summary_text`` so nothing is lost.

    When ``plan_input`` is supplied, the repo-relative paths RDP flattened to
    basenames are restored so later stages can find the files again.
    """
    plan = _restore_relative_paths(dict(plan), _relative_path_index(plan_input))

    steps = plan.get("steps") or []
    raw_summary = plan.get("summary")

    summary = dict(raw_summary) if isinstance(raw_summary, dict) else {}
    summary["total_steps"] = len(steps)

    def _rating(step, *keys):
        for key in keys:
            value = step.get(key)
            if isinstance(value, str) and value:
                return value.lower()
        return None

    summary.setdefault(
        "high_impact",
        sum(1 for s in steps if _rating(s, "expected_impact", "impact") == "high"),
    )
    summary.setdefault(
        "risks",
        {level: sum(1 for s in steps if _rating(s, "risk") == level)
         for level in ("low", "medium", "high")},
    )

    normalized = {**plan, "steps": steps, "summary": summary, "source": "rdp_agent"}
    if isinstance(raw_summary, str) and raw_summary:
        normalized["summary_text"] = raw_summary
    return normalized


#: Ratings RDP can emit. Anything else is coerced to the fallback.
_RATINGS = ("low", "medium", "high")


def _rating(value, fallback="medium"):
    text = str(value or "").strip().lower()
    return text if text in _RATINGS else fallback


def _round(value, digits=3):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(float(value), digits)


def fold_trace_into_plan(plan: dict, trace: Optional[dict] = None) -> dict:
    """Fold RDP's decision trace back onto the plan's steps.

    RDP's steps carry only the transformation-facing fields —
    ``{step_id, smell_id, refactoring, target, parameters, explanation}`` —
    while the impact / risk ratings, the MCDA score, the impact prediction and
    the rejected-but-viable candidates live in the trace, keyed by smell_id.

    The browser has always done this fold (utils/planTrace.js) so Stage 2 could
    render those fields. It now has to happen here as well, and BEFORE the
    decision-support pass: the recommendation is scored from `risk`, `score`,
    `expected_impact` and `prediction`, so a fold that happened only in the
    browser would leave the backend scoring a step as medium-risk and unscored
    while the card beside the badge displayed "Risk: high · RDP 0.84". The two
    read the same trace and produce the same values, which is what keeps the
    badge and the row telling the same story.

    Nothing RDP sent is discarded, and a value already on the step wins over
    the trace's fallback — this only fills the fields RDP left off the step.
    """
    steps = plan.get("steps")
    if not isinstance(steps, list) or not isinstance(trace, dict):
        return plan

    def _by_smell(entries, key="predictions"):
        return {
            entry.get("smell_id"): entry.get(key) or []
            for entry in (entries or []) if isinstance(entry, dict)
        }

    selections = {
        entry.get("smell_id"): entry
        for entry in (trace.get("candidate_generation") or []) if isinstance(entry, dict)
    }
    impacts = _by_smell(trace.get("impact_prediction"))
    mcda = _by_smell(trace.get("mcda_selection"))
    inputs = {
        smell.get("id"): smell
        for smell in ((trace.get("input_summary") or {}).get("smells") or [])
        if isinstance(smell, dict)
    }

    folded = []
    for step in steps:
        if not isinstance(step, dict):
            folded.append(step)
            continue

        smell_id = step.get("smell_id")
        selection = selections.get(smell_id) or {}
        candidates = [c for c in (selection.get("candidates") or []) if isinstance(c, dict)]
        chosen = next((c for c in candidates if c.get("name") == step.get("refactoring")), {})
        source = inputs.get(smell_id) or {}

        mcda_entry = next(
            (m for m in mcda.get(smell_id, [])
             if isinstance(m, dict) and m.get("refactoring") == step.get("refactoring")),
            None,
        )
        score = selection.get("selected_score")
        if score is None and mcda_entry:
            score = mcda_entry.get("final_score")

        prediction = next(
            (p for p in impacts.get(smell_id, [])
             if isinstance(p, dict) and p.get("refactoring") == step.get("refactoring")),
            None,
        )

        folded.append({
            **step,
            "impact": step.get("impact") or _rating(chosen.get("impact")),
            "expected_impact": step.get("expected_impact") or _rating(chosen.get("impact")),
            "risk": step.get("risk") or _rating(chosen.get("risk")),
            "complexity": step.get("complexity") or _rating(chosen.get("complexity")),
            "score": step.get("score") if step.get("score") is not None else _round(score),
            "scoring_method": step.get("scoring_method") or selection.get("scoring_method"),
            "smell_type": step.get("smell_type") or selection.get("smell_type") or source.get("type"),
            "severity": step.get("severity") or selection.get("severity") or source.get("severity"),
            "location": step.get("location") or source.get("location"),
            "smell_metrics": step.get("smell_metrics") or source.get("metrics"),
            "prediction": step.get("prediction") or prediction,
            "alternatives": step.get("alternatives") or [
                {
                    "name": c.get("name"),
                    "score": _round(c.get("score")),
                    "impact": _rating(c.get("impact")),
                    "risk": _rating(c.get("risk")),
                }
                for c in candidates
                if c.get("name") != step.get("refactoring") and c.get("preconditions_met")
            ],
        })

    generation = trace.get("plan_generation") or {}
    dependency = trace.get("dependency_analysis") or {}
    return {
        **plan,
        "steps": folded,
        "skipped_smells": plan.get("skipped_smells") or generation.get("skipped_smells") or [],
        "smells_skipped": plan.get("smells_skipped", generation.get("smells_skipped", 0)),
        "reordered": bool(plan.get("reordered") or dependency.get("reordered")),
    }


def build_approved_plan(plan: dict, decisions: dict) -> dict:
    """Reduce an RDP plan to the steps the developer approved.

    This is the plan report that goes to the Safe Transformation Agent, so it
    must contain the approved steps and nothing else: a rejected step left in
    the JSON would be mapped to an SCTVA action and executed.

    The steps keep their original ``step_id`` so every action SCTVA reports
    still traces back to the RDP plan the developer reviewed. The summary is
    recomputed rather than carried over — leaving the original
    ``total_steps`` on a reduced step list produces a report that contradicts
    itself, which is exactly the kind of stale figure that hides a filtering
    bug. What was rejected is recorded under ``approval`` instead of being
    silently dropped.
    """
    steps = plan.get("steps") or []
    decisions = decisions or {}

    def verdict(step):
        step_id = step.get("step_id")
        return decisions.get(str(step_id)) or decisions.get(step_id)

    approved = [s for s in steps if verdict(s) == "approve"]
    rejected = [s for s in steps if verdict(s) == "reject"]
    pending = [s for s in steps if verdict(s) not in ("approve", "reject")]

    def ratings(step, *keys):
        for key in keys:
            value = step.get(key)
            if isinstance(value, str) and value:
                return value.lower()
        return None

    raw_summary = plan.get("summary")
    summary = dict(raw_summary) if isinstance(raw_summary, dict) else {}
    summary["total_steps"] = len(approved)
    summary["high_impact"] = sum(
        1 for s in approved if ratings(s, "expected_impact", "impact") == "high"
    )
    summary["risks"] = {
        level: sum(1 for s in approved if ratings(s, "risk") == level)
        for level in ("low", "medium", "high")
    }

    # Reducing an already-reduced plan must not erase what was rejected the
    # first time: the second pass only sees the survivors, so its own rejected
    # list would come back empty and the audit would lose the verdict.
    raw_prior = plan.get("approval")
    prior: dict = raw_prior if isinstance(raw_prior, dict) else {}
    prior_rejected = [i for i in (prior.get("rejected_step_ids") or [])]
    rejected_ids = prior_rejected + [
        s.get("step_id") for s in rejected if s.get("step_id") not in prior_rejected
    ]

    updated = {
        **plan,
        "steps": approved,
        "summary": summary,
        "approval": {
            "source_plan_id": prior.get("source_plan_id") or plan.get("plan_id"),
            "approved_step_ids": [s.get("step_id") for s in approved],
            "rejected_step_ids": rejected_ids,
            "pending_step_ids": [s.get("step_id") for s in pending],
            "approved_count": len(approved),
            "rejected_count": len(rejected_ids),
            "original_total_steps": prior.get("original_total_steps", len(steps)),
            "decided_at": datetime.now(timezone.utc).isoformat(),
        },
    }

    if isinstance(raw_summary, str) and raw_summary:
        updated["summary_text"] = raw_summary

    # Same reasoning as the summary above: a decision-support summary counting
    # twelve steps on a plan that now holds four is the kind of stale figure
    # that hides a filtering bug. The per-step `decision_support` blocks travel
    # with their steps untouched — they describe the step, not the selection.
    #
    # Imported here rather than at module scope because capability_map reads
    # REFACTORING_MAP from this module, so a top-level import would close the
    # cycle plan_normalizer -> planning_recommendation -> capability_map.
    if isinstance(plan.get("decision_support_summary"), dict):
        from domain.planning_recommendation import summarize_recommendations

        prior_summary = plan["decision_support_summary"]
        updated["decision_support_summary"] = summarize_recommendations(
            approved,
            developer_strategy=prior_summary.get("developer_strategy", "balanced"),
            plan_source=prior_summary.get("plan_source"),
        )

    return updated


def generate_refactoring_plan(selected_smells: list, target: str) -> dict:
    steps = []
    for i, smell in enumerate(selected_smells, start=1):
        smell_type = smell.get("type", "Unknown")
        refactoring, risk, impact = REFACTORING_MAP.get(
            smell_type, ("Rename Method", "low", "low")
        )
        loc = smell.get("location", {})
        method = loc.get("method") or "N/A"
        cls = loc.get("class") or target.replace(".java", "").replace(".py", "")
        lines = loc.get("lines", [0, 0])
        metrics = smell.get("metrics", {})

        step = {
            "step_id": i,
            "smell_id": smell.get("id", f"smell_{i:03d}"),
            "smell_type": smell_type,
            # Severity and the file come straight off the smell. They were
            # dropped before, which left the fallback plan's steps unable to
            # say which file they touched — so Stage 2 grouped every one of
            # them under "(module level)" — and left the step-level feedback
            # rows with a null severity.
            "severity": smell.get("severity"),
            "refactoring": refactoring,
            "risk": risk,
            "expected_impact": impact,
            "target": {"class": cls, "method": method,
                       "file": loc.get("file") or smell.get("relative_path"),
                       "lines": lines},
            "parameters": _build_parameters(refactoring, cls, method, lines, metrics),
            "explanation": (
                f"Apply {refactoring} on {cls}.{method} to resolve '{smell_type}' smell. "
                f"Expected {impact} impact with {risk} risk. "
                f"Affected lines: {lines[0]}–{lines[1]}."
            ),
        }
        steps.append(step)

    return {
        "plan_id": f"plan_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
        "target": target,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "steps": steps,
        "summary": {
            "total_steps": len(steps),
            "high_impact": sum(1 for s in steps if s["expected_impact"] == "high"),
            "risks": {r: sum(1 for s in steps if s["risk"] == r) for r in ("low", "medium", "high")},
        }
    }


def _build_parameters(refactoring, cls, method, lines, metrics):
    if refactoring == "Extract Method":
        return {"source_lines": lines, "new_method_name": f"extracted_{method}"}
    if refactoring == "Extract Class":
        return {"source_class": cls, "new_class_name": f"{cls}Helper"}
    if refactoring == "Move Method":
        return {"source_class": cls, "method": method, "destination_class": "<inferred>"}
    if refactoring == "Introduce Parameter Object":
        return {"method": method, "parameter_object_name": f"{method}Params"}
    if refactoring == "Remove Dead Code":
        return {"target_class": cls, "target_method": method, "source_lines": lines}
    if refactoring == "Replace Magic Number with Symbolic Constant":
        return {"source_lines": lines, "constant_name": "EXTRACTED_CONSTANT"}
    if refactoring == "Replace Nested Conditional with Guard Clauses":
        return {"target_method": method, "source_lines": lines,
                "nesting_depth": metrics.get("nesting_depth")}
    if refactoring == "Replace Unsafe Call with Safe Variant":
        return {"target_function": method or cls, "source_lines": lines}
    if refactoring == "Encapsulate Field":
        return {"target_class": cls, "field": method or cls}
    return {"target_class": cls, "target_method": method}


def generate_updated_plan_report(plan: dict, decisions: Optional[dict] = None, preferences: Optional[dict] = None) -> dict:
    """
    Re-rank and filter a plan using developer step decisions + preferences.
    This is used for live plan updates while the developer is reviewing steps.
    """
    base_steps = list(plan.get("steps", []))
    decisions = decisions or {}
    preferences = preferences or {}

    preferred_refactorings = set(preferences.get("preferred_refactorings", []))
    risk_tolerance = str(preferences.get("risk_tolerance", "balanced")).lower()
    impact_focus = str(preferences.get("impact_focus", "high")).lower()

    impact_weight = {"low": 1, "medium": 2, "high": 3}
    risk_weight_balanced = {"low": 3, "medium": 2, "high": 1}
    risk_weight_aggressive = {"low": 1, "medium": 2, "high": 3}
    risk_weight_conservative = {"low": 4, "medium": 2, "high": 0}

    if risk_tolerance == "aggressive":
        risk_weight = risk_weight_aggressive
    elif risk_tolerance == "conservative":
        risk_weight = risk_weight_conservative
    else:
        risk_weight = risk_weight_balanced

    def step_score(step: dict) -> int:
        decision = decisions.get(str(step.get("step_id"))) or decisions.get(step.get("step_id"))
        expected_impact = str(step.get("impact") or step.get("expected_impact") or "medium").lower()
        risk = str(step.get("risk") or "medium").lower()

        score = 0
        score += impact_weight.get(expected_impact, 2) * 4
        score += risk_weight.get(risk, 2) * 2

        if impact_focus == expected_impact:
            score += 3
        if preferred_refactorings and step.get("refactoring") in preferred_refactorings:
            score += 4

        if decision == "approve":
            score += 10
        elif decision == "reject":
            score -= 50

        return score

    accepted_steps = [
        s for s in base_steps
        if (decisions.get(str(s.get("step_id"))) or decisions.get(s.get("step_id"))) != "reject"
    ]

    ranked = sorted(accepted_steps, key=step_score, reverse=True)

    remapped_steps = []
    for idx, step in enumerate(ranked, start=1):
        new_step = dict(step)
        new_step["step_id"] = idx
        remapped_steps.append(new_step)

    plan_id = plan.get("plan_id", f"plan_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}")
    updated_plan_id = f"{plan_id}_updated"

    summary_meta = {
        "total_steps": len(remapped_steps),
        "approved_count": sum(1 for d in decisions.values() if d == "approve"),
        "rejected_count": sum(1 for d in decisions.values() if d == "reject"),
        "risk_tolerance": risk_tolerance,
        "impact_focus": impact_focus,
        "preferred_refactorings": sorted(preferred_refactorings),
    }

    summary_text = (
        f"{summary_meta['total_steps']}-step updated plan generated from developer preferences. "
        f"Approved: {summary_meta['approved_count']}, Rejected: {summary_meta['rejected_count']}, "
        f"Risk tolerance: {summary_meta['risk_tolerance']}, Impact focus: {summary_meta['impact_focus']}."
    )

    return {
        "plan_id": updated_plan_id,
        "target": plan.get("target"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "steps": remapped_steps,
        "summary": summary_text,
        "summary_meta": summary_meta,
        "user_preferences": preferences,
    }
