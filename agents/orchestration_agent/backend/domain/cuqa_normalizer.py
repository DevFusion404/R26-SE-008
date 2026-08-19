"""
CUQA report normalization
=========================
R26-SE-008 | Bandara S M Y M | IT22277886

Agent 1 ingestion: a CUQA quality report becomes the DIWO smell model, and a
developer's selection narrows that report back down for the RDP agent.

The CUQA agent (FastAPI, port 8080) answers POST /api/quality-report with
either a repository report or a single-file report. Everything downstream in
DIWO — workflow creation, metrics, plan generation, the updated report the
frontend renders — works on the flat "smell" shape produced here.

Moved verbatim out of diwo/orchestrator.py; these functions are pure, so they
carry no Flask, no HTTP and no database.
"""

from pathlib import Path
from datetime import datetime, timezone

from domain.workflow_states import normalize_severity

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


def summarize_files(files: list) -> dict:
    """Rebuild the CUQA summary block from a list of file reports."""
    severity_totals = {"high": 0, "medium": 0, "low": 0}
    for file_report in files:
        for smell in file_report.get("code_smells") or []:
            severity_totals[normalize_severity(smell.get("severity"))] += 1

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
            entry["severity"] = normalize_severity(smell.get("severity"))
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
        summary = summarize_files(files)

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
            smell_summary[normalize_severity(smell.get("severity"))] += 1

        entry = dict(file_report)
        entry["code_smells"] = kept
        entry["smell_summary"] = smell_summary
        files.append(entry)

    summary = summarize_files(files)
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
                "severity": normalize_severity(smell.get("severity")),
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
# Fallback report builder
# ─────────────────────────────────────────────────────────────────────────────

def build_report_from_smells(smells: list, repo_name: str, selected_ids=None):
    """Build a cquaAgent.json-style report, keeping all files and filtering smells.

    If selected_ids is provided, smells not in selected_ids are excluded from each
    file's code_smells list, but the file itself remains in the report.
    """
    selected_ids = set(selected_ids or [])
    file_map = {}
    file_order = []
    severity_totals = {"high": 0, "medium": 0, "low": 0}

    for smell in smells:
        loc = smell.get("location", {}) or {}
        file_path = loc.get("file") or smell.get("relative_path") or "unknown"
        metrics = smell.get("metrics", {}) or {}

        if file_path not in file_map:
            quality_score = metrics.get("quality_score", smell.get("quality_score", 0))
            file_map[file_path] = {
                "file": Path(file_path).name,
                "language": smell.get("language") or (file_path.split(".")[-1] or "unknown").lower(),
                "metrics": {
                    "filename": Path(file_path).name,
                    "lines_of_code": metrics.get("lines_of_code", 0),
                    "blank_lines": metrics.get("blank_lines", 0),
                    "comment_lines": metrics.get("comment_lines", 0),
                    "functions": metrics.get("functions", 0),
                    "classes": metrics.get("classes", 0),
                },
                "code_smells": [],
                "smell_summary": {"high": 0, "medium": 0, "low": 0},
                "quality_score": quality_score,
                "relative_path": file_path,
            }
            file_order.append(file_path)

        smell_id = smell.get("id")
        include_smell = not selected_ids or smell_id in selected_ids
        if not include_smell:
            continue

        severity = (smell.get("severity") or "low").lower()
        if severity not in ("high", "medium", "low"):
            severity = "low"

        line = smell.get("line")
        if line is None:
            line = (loc.get("lines") or [0, 0])[0]

        file_map[file_path]["code_smells"].append({
            "type": smell.get("type"),
            "message": smell.get("message", ""),
            "line": line,
            "severity": severity,
        })
        file_map[file_path]["smell_summary"][severity] += 1
        severity_totals[severity] += 1

    files = [file_map[path] for path in file_order]
    total_loc = sum(f["metrics"]["lines_of_code"] for f in files)
    total_smells = sum(severity_totals.values())
    avg_quality = (sum(f["quality_score"] for f in files) / max(len(files), 1)) if files else 0

    return {
        "summary": {
            "files_analyzed": len(files),
            "total_lines_of_code": total_loc,
            "total_code_smells": total_smells,
            "smell_severity": severity_totals,
            "average_quality_score": round(avg_quality, 2),
        },
        "files": files,
        "repo_name": repo_name,
    }
