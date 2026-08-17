"""
Workflow Orchestration Engine
==============================
Implements the 5-stage state machine for the DIWO Agent.

Stages:
  1. smell_review        – Agent 1 outputs shown to developer
  2. smell_selection     – Developer selects which smells to address
  3. plan_approval       – Agent 2 refactoring plan shown; developer approve/reject/edit
  4. transformation      – Agent 3 transforms; result shown for approval
  5. comparison          – Before/after metrics + audit log view

Transitions are gated: each stage requires an explicit developer action.
"""

import uuid
import json
import random
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone


# ─────────────────────────────────────────────────────────────────────────────
# Stage definitions
# ─────────────────────────────────────────────────────────────────────────────

STAGES = [
    "smell_review",
    "smell_selection",
    "plan_approval",
    "transformation",
    "comparison",
    "completed",
    "rolled_back",
]

STAGE_LABELS = {
    "smell_review":    "Code Smell Review",
    "smell_selection": "Developer Smell Selection",
    "plan_approval":   "Plan Review & Approval",
    "transformation":  "Refactor & Validation",
    "comparison":      "Comparison & Visualization",
    "completed":       "Completed",
    "rolled_back":     "Rolled Back",
}


def next_stage(current: str) -> str:
    idx = STAGES.index(current)
    return STAGES[min(idx + 1, len(STAGES) - 1)]


# ─────────────────────────────────────────────────────────────────────────────
# Agent 1 ingestion: CUQA quality report → DIWO smell model
#
# The CUQA agent (FastAPI, port 8080) answers POST /api/quality-report with
# either a repository report or a single-file report. Everything downstream in
# DIWO — workflow creation, metrics, plan generation, the updated report the
# frontend renders — works on the flat "smell" shape produced here.
# ─────────────────────────────────────────────────────────────────────────────

SEVERITIES = ("high", "medium", "low")

# CUQA smell types that describe a class rather than a single method.
CLASS_LEVEL_SMELLS = {
    "LargeClass", "LazyClass", "PrimitiveObsession",
    "InappropriateIntimacy", "SpeculativeGenerality", "GodClass",
}

# CUQA smell types whose `entity` is a method/function name.
METHOD_LEVEL_SMELLS = {
    "LongMethod", "LongFunction", "TooManyParameters", "SwitchStatements",
    "MessageChains", "FeatureEnvy", "DuplicateCode", "DataClumps",
    "BareExcept", "DeadCode",
}

# Extra per-smell fields CUQA emits that the planner and the UI can use.
SMELL_METRIC_FIELDS = (
    "cyclomatic_complexity", "parameter_count", "method_count",
    "primitive_field_count", "chain_length", "nesting_depth",
    "external_field_accesses", "self_field_accesses",
)


def _normalize_severity(value) -> str:
    severity = str(value or "low").lower()
    return severity if severity in SEVERITIES else "low"


def _summarize_files(files: list) -> dict:
    """Rebuild the CUQA summary block from a list of file reports."""
    severity_totals = {"high": 0, "medium": 0, "low": 0}
    for file_report in files:
        for smell in file_report.get("code_smells") or []:
            severity_totals[_normalize_severity(smell.get("severity"))] += 1

    scored = [f for f in files if isinstance(f.get("quality_score"), (int, float))]
    average = sum(f["quality_score"] for f in scored) / len(scored) if scored else 0

    return {
        "files_analyzed": len(files),
        "total_lines_of_code": sum(
            (f.get("metrics") or {}).get("lines_of_code", 0) for f in files
        ),
        "total_code_smells": sum(severity_totals.values()),
        "smell_severity": severity_totals,
        "average_quality_score": round(average, 1),
    }


def normalize_cuqa_report(payload: dict) -> dict:
    """Coerce any CUQA quality-report payload into one repository-shaped report.

    Accepts the raw envelope ({"type": ..., "report": ...}) as returned by
    POST /api/quality-report, a bare repository report, or a single-file report.
    Guarantees every file entry carries relative_path, language, metrics,
    code_smells, smell_summary and quality_score so the frontend can render it
    without defensive checks.
    """
    if not isinstance(payload, dict):
        raise ValueError("CUQA payload must be a JSON object.")

    report = payload.get("report") if isinstance(payload.get("report"), dict) else payload

    if isinstance(report.get("files"), list):
        raw_files = report["files"]
        repo_name = report.get("repo_name") or payload.get("repo_name")
    else:
        # Single-file report — wrap it so callers only handle one shape.
        raw_files = [report]
        repo_name = report.get("relative_path") or report.get("file")

    files = []
    for raw in raw_files:
        if not isinstance(raw, dict):
            continue

        rel_path = (raw.get("relative_path") or raw.get("file") or "unknown").replace("\\", "/")
        metrics = dict(raw.get("metrics") or {})
        metrics.setdefault("filename", Path(rel_path).name)

        smells = []
        for smell in raw.get("code_smells") or []:
            if not isinstance(smell, dict):
                continue
            entry = dict(smell)
            entry["severity"] = _normalize_severity(smell.get("severity"))
            entry["type"] = smell.get("type") or "Unknown"
            smells.append(entry)

        smell_summary = {"high": 0, "medium": 0, "low": 0}
        for smell in smells:
            smell_summary[smell["severity"]] += 1

        files.append({
            "file": raw.get("file") or Path(rel_path).name,
            "relative_path": rel_path,
            "language": (raw.get("language") or Path(rel_path).suffix.lstrip(".") or "unknown").lower(),
            "metrics": metrics,
            "code_smells": smells,
            "smell_summary": smell_summary,
            "quality_score": raw.get("quality_score", 100),
            **({"error": raw["error"]} if raw.get("error") else {}),
        })

    summary = report.get("summary")
    if not isinstance(summary, dict) or "total_code_smells" not in summary:
        summary = _summarize_files(files)

    return {
        "summary": summary,
        "files": files,
        "repo_name": repo_name,
        "source": "cuqa",
        "report_type": payload.get("type", "repository"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def filter_cuqa_report(report: dict, selected_ids) -> dict:
    """Narrow a CUQA quality report down to the developer's selected smells.

    The result is the *same JSON shape* POST /api/cuqa/quality-report serves:
    every file keeps its language, its full metrics block and its
    quality_score, and every surviving smell keeps all of its own fields
    (entity, start_line, end_line, ...). Only the code_smells lists shrink.

    That matters because this report is what /select-smells forwards to the
    RDP agent. Rebuilding it from DIWO's flattened smell list — as
    _build_report_from_smells does — drops the richer metrics and every
    smell's `entity`, and reports quality_score 0, so RDP plans against
    weaker input than CUQA actually produced.

    Smell ids are recomputed exactly as cuqa_report_to_smells() assigns them
    (`<relative_path>:<line>:<index>`), so a selection made against the
    flattened list resolves here without a lookup table.
    """
    selected = set(selected_ids or [])
    files = []
    kept_ids = []
    total_smells = 0

    for file_report in report.get("files") or []:
        rel_path = file_report.get("relative_path") or file_report.get("file") or "unknown"

        kept = []
        for idx, smell in enumerate(file_report.get("code_smells") or []):
            total_smells += 1
            smell_id = f"{rel_path}:{smell.get('line') or 0}:{idx}"
            if selected and smell_id not in selected:
                continue
            kept.append(dict(smell))
            kept_ids.append(smell_id)

        smell_summary = {"high": 0, "medium": 0, "low": 0}
        for smell in kept:
            smell_summary[_normalize_severity(smell.get("severity"))] += 1

        entry = dict(file_report)
        entry["code_smells"] = kept
        entry["smell_summary"] = smell_summary
        files.append(entry)

    summary = _summarize_files(files)
    summary["selected_smell_ids"] = kept_ids
    summary["selected_count"] = len(kept_ids)
    summary["excluded_count"] = max(0, total_smells - len(kept_ids))
    summary["selected_file_count"] = sum(1 for f in files if f.get("code_smells"))

    return {
        **report,
        "files": files,
        "summary": summary,
        "filtered": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def cuqa_report_to_smells(report: dict) -> list:
    """Flatten a normalized CUQA report into the DIWO smell list.

    The id format is `<relative_path>:<line>:<index>` — the same key the DIWO
    frontend and _resolve_selected_ids() use, so file- or smell-level selection
    resolves back to these ids without a lookup table.
    """
    smells = []

    for file_report in report.get("files") or []:
        rel_path = file_report.get("relative_path") or file_report.get("file") or "unknown"
        file_metrics = file_report.get("metrics") or {}
        class_fallback = Path(rel_path).stem

        for idx, smell in enumerate(file_report.get("code_smells") or []):
            smell_type = smell.get("type") or "Unknown"
            entity = smell.get("entity")
            line = smell.get("line") or 0
            start_line = smell.get("start_line") or line
            end_line = smell.get("end_line") or start_line

            if smell_type in CLASS_LEVEL_SMELLS:
                target_class, target_method = entity or class_fallback, None
            elif smell_type in METHOD_LEVEL_SMELLS:
                target_class, target_method = class_fallback, entity
            else:
                target_class, target_method = class_fallback, None

            metrics = {
                "lines_of_code": file_metrics.get("lines_of_code", 0),
                "blank_lines": file_metrics.get("blank_lines", 0),
                "comment_lines": file_metrics.get("comment_lines", 0),
                "functions": file_metrics.get("functions", 0),
                "classes": file_metrics.get("classes", 0),
                "quality_score": file_report.get("quality_score", 100),
            }
            for field in SMELL_METRIC_FIELDS:
                if smell.get(field) is not None:
                    metrics[field] = smell[field]

            smells.append({
                "id": f"{rel_path}:{line}:{idx}",
                "type": smell_type,
                "severity": _normalize_severity(smell.get("severity")),
                "message": smell.get("message", ""),
                "line": line,
                "entity": entity,
                "language": file_report.get("language", "unknown"),
                "relative_path": rel_path,
                "quality_score": file_report.get("quality_score", 100),
                "location": {
                    "file": rel_path,
                    "class": target_class,
                    "method": target_method,
                    "lines": [start_line, end_line],
                },
                "metrics": metrics,
                "source": "cuqa",
            })

    return smells


def detect_primary_language(report: dict) -> str:
    """Most common language among the analysed files (defaults to 'java')."""
    counts = {}
    for file_report in report.get("files") or []:
        language = (file_report.get("language") or "").lower()
        if language and language != "unknown":
            counts[language] = counts.get(language, 0) + 1

    if not counts:
        return "java"
    return max(counts.items(), key=lambda item: item[1])[0]


def derive_target_name(report: dict) -> str:
    """Workflow target label: the repo name, or the single file analysed."""
    repo_name = report.get("repo_name")
    if repo_name:
        return str(repo_name)

    files = report.get("files") or []
    if len(files) == 1:
        return files[0].get("relative_path") or files[0].get("file") or "cuqa_workspace"
    return "cuqa_workspace"


# ─────────────────────────────────────────────────────────────────────────────
# Simulated Agent 2: Refactoring Plan Generator
# In a real system this calls the Refactoring Planning Agent over REST.
# ─────────────────────────────────────────────────────────────────────────────

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

def build_rdp_plan_input(updated_report: dict) -> dict:
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
    files = []
    for entry in (updated_report or {}).get("files") or []:
        smells = entry.get("code_smells") or []
        if not smells:
            continue

        relative_path = entry.get("relative_path") or entry.get("file") or "unknown"
        files.append({**entry, "file": relative_path, "relative_path": relative_path})

    # Recompute every total from the files actually being sent. Carrying the
    # report's own figures through would leave the payload self-contradicting —
    # a summary describing files that are not in it.
    summary = dict((updated_report or {}).get("summary") or {})
    summary.update(_summarize_files(files))

    payload = {"files": files, "summary": summary}
    if (updated_report or {}).get("repo_name"):
        payload["repo_name"] = updated_report["repo_name"]
    return payload


def _relative_path_index(plan_input: dict) -> dict:
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


def normalize_rdp_plan(plan: dict, plan_input: dict = None) -> dict:
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
            "refactoring": refactoring,
            "risk": risk,
            "expected_impact": impact,
            "target": {"class": cls, "method": method},
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


# ─────────────────────────────────────────────────────────────────────────────
# Simulated Agent 3: Transformation + Validation
# In a real system this calls the Safe Code Transformation Agent over REST.
# ─────────────────────────────────────────────────────────────────────────────

def simulate_transformation(plan: dict, language: str, before_code: str = "") -> dict:
    steps = plan.get("steps", [])
    results = []
    all_passed = True

    for step in steps:
        passed = random.random() > 0.08   # 92% success rate simulation
        results.append({
            "step_id": step["step_id"],
            "smell_id": step["smell_id"],
            "refactoring": step["refactoring"],
            "status": "passed" if passed else "failed",
            "validation": {
                "syntax_ok": passed,
                "behavior_preserved": passed,
                "tests_passed": passed,
            },
            "message": "Transformation applied and validated." if passed
                       else "Transformation failed: syntax error detected.",
        })
        if not passed:
            all_passed = False

    snapshot_id = f"snapshot_{uuid.uuid4().hex[:8]}"
    
    # Generate mock refactored code and diffs
    after_code = before_code.replace("processor.calculateTotal", "processor.extracted_calculateTotal") if before_code else _generate_mock_refactored_code()
    diff_rows = _generate_mock_diff_rows(before_code or _generate_mock_before_code(), after_code)
    files = [{
        "path": "ECommerceSystem.java",
        "before": before_code or _generate_mock_before_code(),
        "after": after_code,
        "diff_rows": diff_rows,
    }]

    return {
        "status": "success" if all_passed else "partial_failure",
        "language": language,
        "snapshot_id": snapshot_id,
        "transformed_at": datetime.now(timezone.utc).isoformat(),
        "step_results": results,
        "rollback_available": True,
        "overall_passed": all_passed,
        "steps_passed": sum(1 for r in results if r["status"] == "passed"),
        "steps_failed": sum(1 for r in results if r["status"] == "failed"),
        "refactored_code": after_code,
        "diff_rows": diff_rows,
        "files": files,
    }


def _generate_mock_before_code():
    return """public class ECommerceSystem {
    public static void main(String[] args) {
        Customer customer = new Customer(1, "Pasan", "pasan@example.com");
        Order order = new Order(1001, customer);
        order.items.add(new OrderItem("Laptop", 2, 1200.00));
        order.items.add(new OrderItem("Mouse", 1, 30.00));

        OrderProcessor processor = new OrderProcessor();
        double total = processor.calculateTotal(order, "CARD", true, "PROMO10", "EXPRESS");
        System.out.println("Order Total: " + total);
    }
}"""


def _generate_mock_refactored_code():
    return """public class ECommerceSystem {
    public static void main(String[] args) {
        Customer customer = new Customer(1, "Pasan", "pasan@example.com", "premium", "Colombo");
        Order order = new Order(1001, customer);
        order.items.add(new OrderItem("Laptop", 2, 1200.00));
        order.items.add(new OrderItem("Mouse", 1, 30.00));

        OrderProcessorHelper processor = new OrderProcessorHelper();
        OrderParams params = new OrderParams("CARD", true, "PROMO10", "EXPRESS");
        double total = processor.extracted_calculateTotal(order, params);
        System.out.println("Order Total: " + total);
    }
}"""


def _generate_mock_diff_rows(before: str, after: str):
    """Generate a simple line-by-line diff for frontend display."""
    before_lines = before.split('\n')
    after_lines = after.split('\n')
    diff_rows = []
    key_counter = 0
    
    max_lines = max(len(before_lines), len(after_lines))
    for i in range(max_lines):
        if i < len(before_lines) and i < len(after_lines):
            if before_lines[i] == after_lines[i]:
                diff_rows.append({
                    "key": f"same-{key_counter}",
                    "lineNo": i + 1,
                    "kind": "same",
                    "marker": "  ",
                    "text": before_lines[i],
                })
            else:
                diff_rows.append({
                    "key": f"before-{key_counter}",
                    "lineNo": i + 1,
                    "kind": "before",
                    "marker": "- ",
                    "text": before_lines[i],
                })
                key_counter += 1
                diff_rows.append({
                    "key": f"after-{key_counter}",
                    "lineNo": i + 1,
                    "kind": "after",
                    "marker": "+ ",
                    "text": after_lines[i],
                })
        elif i < len(before_lines):
            diff_rows.append({
                "key": f"before-{key_counter}",
                "lineNo": i + 1,
                "kind": "before",
                "marker": "- ",
                "text": before_lines[i],
            })
        else:
            diff_rows.append({
                "key": f"after-{key_counter}",
                "lineNo": i + 1,
                "kind": "after",
                "marker": "+ ",
                "text": after_lines[i],
            })
        key_counter += 1
    
    return diff_rows


# ─────────────────────────────────────────────────────────────────────────────
# Code Quality Metrics Simulation
# ─────────────────────────────────────────────────────────────────────────────

def compute_metrics_before(smells: list) -> dict:
    critical = sum(1 for s in smells if s.get("severity") == "critical")
    high = sum(1 for s in smells if s.get("severity") == "high")
    medium = sum(1 for s in smells if s.get("severity") == "medium")
    low = sum(1 for s in smells if s.get("severity") == "low")

    # Heuristic complexity score
    complexity = min(100, 30 + critical * 15 + high * 8 + medium * 4 + low * 1)
    duplication = min(100, 10 + len(smells) * 3)
    maintainability = max(0, 100 - complexity - duplication // 2)

    return {
        "cyclomatic_complexity": complexity,
        "code_duplication_pct": duplication,
        "maintainability_index": maintainability,
        "total_smells": len(smells),
        "smell_breakdown": {"critical": critical, "high": high, "medium": medium, "low": low},
    }


def compute_metrics_after(metrics_before: dict, resolved_count: int, total_smells: int) -> dict:
    ratio = resolved_count / max(total_smells, 1)
    complexity_reduction = int(metrics_before["cyclomatic_complexity"] * ratio * 0.7)
    dup_reduction = int(metrics_before["code_duplication_pct"] * ratio * 0.6)

    new_complexity = max(5, metrics_before["cyclomatic_complexity"] - complexity_reduction)
    new_dup = max(0, metrics_before["code_duplication_pct"] - dup_reduction)
    new_maint = min(100, metrics_before["maintainability_index"] + complexity_reduction + dup_reduction // 2)

    before_breakdown = metrics_before.get("smell_breakdown", {})
    after_breakdown = {k: max(0, v - int(v * ratio)) for k, v in before_breakdown.items()}

    return {
        "cyclomatic_complexity": new_complexity,
        "code_duplication_pct": new_dup,
        "maintainability_index": new_maint,
        "total_smells": max(0, total_smells - resolved_count),
        "smell_breakdown": after_breakdown,
        "improvements": {
            "complexity_reduced_by": complexity_reduction,
            "duplication_reduced_by": dup_reduction,
            "maintainability_gained": new_maint - metrics_before["maintainability_index"],
        }
    }
