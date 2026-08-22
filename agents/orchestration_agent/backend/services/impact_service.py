"""
Impact analysis service
=======================
R26-SE-008 | Bandara S M Y M | IT22277886

Gathers the external evidence domain/impact_model.py needs, then caches the
per-smell records.

Records depend only on a workflow's smells, never on the current selection, so
they are computed ONCE and reused for every checkbox click — the panel has to
stay instant, and re-probing SCTVA or re-reading git on every keystroke would
make it anything but.

Everything impure lives here:
  * SCTVA capability probe (GET /sctva/health), cached with a TTL
  * git churn per file, for the deferral-interest axis
  * blast radius and test presence, from the workspace file list
  * persistence through db/workflow_repository.py

Every one of those degrades to a documented default when unavailable. A design
that only worked with all three agents up would be useless in exactly the
situation where the developer most needs to know what is fixable.
"""

import re
import subprocess
from datetime import datetime, timedelta, timezone

from clients.sctva_client import fetch_supported_actions
from db.workflow_repository import (
    get_impact_records, parse_json_field, save_impact_records,
)
from domain.impact_model import (
    CHURN_WINDOW_DAYS, MODEL_VERSION, aggregate, build_impact_record,
)
from domain.selection_optimizer import optimise_preset
from domain.smell_graph import build_edges, selection_notes

#: How long a SCTVA capability probe stays fresh. Its action set only changes
#: when the agent is redeployed, so this is generous on purpose.
CAPABILITY_TTL_SECONDS = 300

#: git log is spawned per file; cap the work so a large report cannot stall the
#: request. Files past the cap simply report zero churn.
MAX_CHURN_FILES = 200

GIT_TIMEOUT_SECONDS = 10

#: Last successful SCTVA capability probe. Annotated so the cache is not
#: inferred as dict[str, None] from its empty initial state.
_capability_cache: dict = {"actions": None, "fetched_at": None}

__all__ = [
    "compute_workflow_impacts", "analyse_selection", "optimise_selection",
    "sctva_supported_actions", "invalidate_capability_cache",
]


# ─────────────────────────────────────────────────────────────────────────────
# Evidence gathering
# ─────────────────────────────────────────────────────────────────────────────

def invalidate_capability_cache():
    """Forget the cached SCTVA action set — used by tests."""
    _capability_cache.update({"actions": None, "fetched_at": None})


def sctva_supported_actions(ttl_seconds: int = CAPABILITY_TTL_SECONDS):
    """SCTVA's live action set, cached.

    Returns None when the agent has never answered, which makes capability_map
    fall back to its static tables — the panel degrades, it does not break.
    """
    now = datetime.now(timezone.utc)
    fetched_at = _capability_cache["fetched_at"]
    if fetched_at and (now - fetched_at).total_seconds() < ttl_seconds:
        return _capability_cache["actions"]

    actions = fetch_supported_actions()
    if actions is not None:
        _capability_cache.update({"actions": actions, "fetched_at": now})
        return actions

    # Probe failed: keep serving the last known set rather than downgrading a
    # correct answer to None because the agent restarted.
    return _capability_cache["actions"]


def file_churn(repo_dir, relative_paths, days: int = CHURN_WINDOW_DAYS) -> dict:
    """Commits touching each path in the window. {} when this is not a git repo.

    Change frequency is what makes the deferral axis meaningful: debt in code
    nobody edits is dormant. Without a repository the axis still renders, it
    just reports zero pressure — and `churn_known` tells the UI not to claim
    otherwise.
    """
    if not repo_dir:
        return {}

    paths = [p for p in dict.fromkeys(relative_paths) if p][:MAX_CHURN_FILES]
    if not paths:
        return {}

    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    counts = {}
    for path in paths:
        try:
            result = subprocess.run(
                ["git", "-C", str(repo_dir), "log", f"--since={since}",
                 "--oneline", "--", path],
                capture_output=True, text=True, timeout=GIT_TIMEOUT_SECONDS,
            )
            counts[path] = sum(1 for line in result.stdout.splitlines() if line.strip())
        except (OSError, subprocess.SubprocessError):
            counts[path] = 0

    return counts


def blast_radius(workspace_files: dict, smell: dict) -> int:
    """How many files name this smell's entity. Textual, deliberately.

    A real reference graph is out of scope for the prototype; a whole-word match
    on the entity name across the workspace is a sound lower bound and costs one
    pass over text already in memory. Always ≥ 1 — the smell's own file counts.
    """
    entity = smell.get("entity") or (smell.get("location") or {}).get("class")
    if not entity or len(str(entity)) < 3:
        return 1

    own = (smell.get("location") or {}).get("file")
    try:
        pattern = re.compile(rf"\b{re.escape(str(entity))}\b")
    except re.error:
        return 1

    return 1 + sum(
        1 for path, text in (workspace_files or {}).items()
        if path != own and text and pattern.search(text)
    )


def has_test_coverage(workspace_paths, relative_path: str) -> bool:
    """Is there a plausible test file for this source file?

    Name-based, so it over-reports a file called `latest.py` and under-reports
    a suite named by feature rather than by file. It feeds a risk multiplier,
    not a gate, and the risk drivers name it explicitly when absent.
    """
    name = (relative_path or "").rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0].lower()
    if len(stem) < 3:
        return False

    return any(
        ("test" in path.lower() or "spec" in path.lower()) and stem in path.lower()
        for path in (workspace_paths or [])
        if path != relative_path
    )


# ─────────────────────────────────────────────────────────────────────────────
# Records
# ─────────────────────────────────────────────────────────────────────────────

def compute_workflow_impacts(wf, workspace_files=None, repo_dir=None,
                             refresh: bool = False) -> list:
    """Build and persist every per-smell record for a workflow (idempotent).

    `workspace_files` maps repo-relative path -> source text, when the caller
    has it; without it blast radius falls back to 1 and test detection to False,
    both of which are stated in the record's risk drivers.
    """
    if not refresh:
        cached = get_impact_records(wf["id"], MODEL_VERSION)
        if cached:
            return cached

    smells = parse_json_field(wf, "smells_json") or []
    if not smells:
        return []

    actions = sctva_supported_actions()
    workspace_files = workspace_files or {}
    paths = list(workspace_files.keys())

    files = [(s.get("location") or {}).get("file") for s in smells]
    churn = file_churn(repo_dir, files)

    records = []
    for smell in smells:
        path = (smell.get("location") or {}).get("file") or ""
        records.append(build_impact_record(
            smell,
            supported_actions=actions,
            blast_radius=blast_radius(workspace_files, smell),
            has_tests=has_test_coverage(paths, path),
            churn=churn.get(path, 0),
            churn_known=bool(churn),
        ))

    save_impact_records(wf["id"], records)
    return records


def _quality_before(wf) -> float:
    """The baseline a projected gain is added to.

    The stored CUQA report is the best source, but a workflow seeded from a
    client-supplied smell list never had one. Falling back to the mean of the
    per-file quality scores carried on the smells themselves keeps the backend
    summary agreeing with the panel, which derives its baseline the same way
    from the report it is rendering.
    """
    report = parse_json_field(wf, "cuqa_report_json") or {}
    value = (report.get("summary") or {}).get("average_quality_score")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)

    by_file = {}
    for smell in parse_json_field(wf, "smells_json") or []:
        score = smell.get("quality_score")
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            by_file.setdefault((smell.get("location") or {}).get("file"), float(score))

    return sum(by_file.values()) / len(by_file) if by_file else 0.0


def analyse_selection(wf, selected_ids, workspace_files=None, repo_dir=None) -> dict:
    """The endpoint payload: per-smell records + aggregate + interaction notes.

    Pure arithmetic over cached records — no agent calls, no git, no file reads
    — so it is safe to call on every selection change.
    """
    smells = parse_json_field(wf, "smells_json") or []
    records = compute_workflow_impacts(wf, workspace_files, repo_dir)
    edges = build_edges(smells)
    selected = set(selected_ids or [])

    return {
        "workflow_id": wf["id"],
        "model_version": MODEL_VERSION,
        "selected_ids": sorted(selected),
        "summary": aggregate(records, selected, _quality_before(wf)),
        "interaction_notes": selection_notes(edges, selected),
    }


def optimise_selection(wf, preset="best_value", budget_minutes=60) -> dict:
    """Propose a selection under a review-time budget."""
    records = compute_workflow_impacts(wf)
    result = optimise_preset(records, preset=preset, budget_minutes=budget_minutes)

    return {
        "workflow_id": wf["id"],
        "model_version": MODEL_VERSION,
        **result,
        "summary": aggregate(records, set(result["selected_ids"]), _quality_before(wf)),
    }
