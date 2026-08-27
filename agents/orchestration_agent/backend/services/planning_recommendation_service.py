"""
Stage 2 decision-support service
================================
R26-SE-008 | Bandara S M Y M | IT22277886

Gathers the evidence domain/planning_recommendation.py needs, then attaches a
`decision_support` block to every step of an RDP plan and a
`decision_support_summary` to the plan itself.

    RDP plan  ->  + Stage 1 impact records (by smell_id)
                  + SCTVA capability set   (live probe, cached)
                  + real step-level feedback statistics
                  + the developer's strategy
              ->  enriched plan

Everything impure lives here; the scoring itself is pure and next door. The
three evidence sources are all optional and all degrade to a documented
neutral: Stage 2 must render when CUQA never ran, when SCTVA is down and when
the feedback table is empty, which is exactly the situation on a fresh install.

Why enrichment happens on the backend
-------------------------------------
The recommendation is the authoritative artefact — it is persisted with the
plan, restored on rollback, and read back by the audit trail — so it is
computed once, server-side, and the browser renders it. Recomputing the same
formula in React would give two implementations of one algorithm and no way to
tell which produced a given screenshot.
"""

from db.workflow_repository import (
    parse_json_field, plan_step_acceptance_stats,
)
from domain.planning_recommendation import (
    build_step_recommendation, strategy_from_preferences, summarize_recommendations,
)
from services.impact_service import compute_workflow_impacts, sctva_supported_actions

__all__ = [
    "enrich_plan_with_recommendations", "enrich_stored_plan",
    "recommendation_evidence", "feedback_stats_for", "impact_records_by_smell",
]


def impact_records_by_smell(wf, workspace_files=None, repo_dir=None) -> dict:
    """Stage 1's per-smell impact records, keyed by smell_id.

    RDP steps carry `smell_id`, and so do the records, which is the whole join:
    the quality gain, risk band, blast radius, validation cover and deferral
    cost the Stage 1 panel already computed are reused rather than re-derived
    from a second model that could disagree with it.

    Returns {} when the workflow has no smells or the records cannot be built —
    the recommendation then falls back to the step's own risk/impact ratings.
    """
    try:
        records = compute_workflow_impacts(wf, workspace_files, repo_dir)
    except Exception:
        # Impact records are an enhancement, not a prerequisite. A failure here
        # must cost the developer some numbers, never the plan approval screen.
        return {}

    return {
        record.get("smell_id"): record
        for record in records or []
        if record.get("smell_id")
    }


def feedback_stats_for(stats, smell_type, refactoring):
    """This (smell type, refactoring) pair's real acceptance history.

    `stats` is one plan_step_acceptance_stats() result, fetched once per plan
    rather than once per step. Falls back to the refactoring alone when the
    exact pair has no rows: "how often do you accept Extract Method" is still a
    genuine observation about this developer, just a coarser one.
    """
    if not stats:
        return None

    pairs = stats.get("pairs") or {}
    prior = stats.get("prior")

    exact = pairs.get((smell_type, refactoring))
    if exact:
        return {**exact, "prior": prior, "match": "smell_and_refactoring"}

    observations = 0
    accepted = 0
    for (_, pair_refactoring), counts in pairs.items():
        if pair_refactoring == refactoring:
            observations += counts["observations"]
            accepted += counts["accepted"]

    if not observations:
        return {"observations": 0, "accepted": 0, "prior": prior, "match": "none"}

    return {
        "observations": observations,
        "accepted": accepted,
        "prior": prior,
        "match": "refactoring_only",
    }


def _complete_step_metadata(step, impact_record):
    """Fill in the smell facts an RDP step arrives without.

    RDP's plan steps carry `smell_id` but not always `smell_type` or
    `severity`: the browser used to fold those in from the decision trace, but
    the backend scores the plan before the browser ever sees it, and the
    step-level feedback rows are written from the same step objects. Without
    this the feedback table records "a step was rejected" with a null smell
    type — exactly the metadata loss §20 is about.

    Only ABSENT fields are filled, and only from the impact record for the same
    smell_id, so nothing RDP actually said is overwritten.
    """
    if not impact_record:
        return step

    missing = {}
    if not step.get("smell_type") and impact_record.get("smell_type"):
        missing["smell_type"] = impact_record["smell_type"]
    if not step.get("severity") and impact_record.get("severity"):
        missing["severity"] = impact_record["severity"]

    # The plan step's own file wins; the record's is the fallback, and it is
    # what lets Stage 2 group a fallback plan's steps by file at all.
    target = step.get("target") if isinstance(step.get("target"), dict) else {}
    if not target.get("file") and impact_record.get("file"):
        missing["target"] = {**target, "file": impact_record["file"]}

    return {**step, **missing} if missing else step


def recommendation_evidence(wf, workspace_files=None, repo_dir=None) -> dict:
    """Every external input the scorer needs, gathered once per plan.

    Kept separate from the enrichment so a caller that already holds the
    evidence — a loop over several plan revisions, say — does not re-probe
    SCTVA and re-read the feedback table for each one.
    """
    try:
        actions = sctva_supported_actions()
    except Exception:
        # No probe means capability_map decides from its static tables, which
        # is the documented offline behaviour rather than an error.
        actions = None

    try:
        feedback = plan_step_acceptance_stats()
    except Exception:
        feedback = None

    return {
        "impact_records": impact_records_by_smell(wf, workspace_files, repo_dir),
        "supported_actions": actions,
        "feedback": feedback,
    }


def enrich_plan_with_recommendations(wf, plan, preferences=None, plan_source=None,
                                     evidence=None, workspace_files=None,
                                     repo_dir=None) -> dict:
    """Attach `decision_support` to every step and a summary to the plan.

    The original plan is never mutated and no RDP field is replaced: the
    recommendation is added ALONGSIDE `score`, `risk`, `expected_impact`,
    `prediction`, `alternatives`, `parameters` and `explanation`, because RDP's
    output and DIWO's decision support are two different claims about the same
    step and collapsing them would lose one of them.

    Returns the plan unchanged when it carries no step list, so a fallback
    response or an error payload passes through untouched.
    """
    if not isinstance(plan, dict):
        return plan

    steps = plan.get("steps")
    if not isinstance(steps, list):
        return plan

    preferences = preferences or {}
    strategy = strategy_from_preferences(preferences)
    preferred = preferences.get("preferred_refactorings")
    source = plan_source or plan.get("source") or plan.get("plan_source")

    if evidence is None:
        evidence = recommendation_evidence(wf, workspace_files, repo_dir)

    impact_records = evidence.get("impact_records") or {}
    feedback = evidence.get("feedback")
    actions = evidence.get("supported_actions")

    enriched_steps = []
    for step in steps:
        if not isinstance(step, dict):
            enriched_steps.append(step)
            continue

        record = impact_records.get(step.get("smell_id"))
        step = _complete_step_metadata(step, record)

        support = build_step_recommendation(
            step,
            impact_record=record,
            developer_strategy=strategy,
            feedback_stats=feedback_stats_for(
                feedback, step.get("smell_type"), step.get("refactoring")),
            supported_actions=actions,
            preferred_refactorings=preferred,
            plan_source=source,
        )
        enriched_steps.append({**step, "decision_support": support})

    return {
        **plan,
        "steps": enriched_steps,
        "decision_support_summary": summarize_recommendations(
            enriched_steps, developer_strategy=strategy, plan_source=source),
    }


def enrich_stored_plan(wf, plan_field="plan_json", preferences=None,
                       plan_source=None) -> dict:
    """Enrich a plan read straight off the workflow row.

    Used by the routes that restore Stage 2 (rollback from transformation) and
    by the preference re-ranker, both of which hold a workflow dict rather than
    a plan.
    """
    plan = parse_json_field(wf, plan_field) or {}
    if preferences is None:
        preferences = plan.get("user_preferences") or {}
    return enrich_plan_with_recommendations(
        wf, plan, preferences=preferences, plan_source=plan_source)
