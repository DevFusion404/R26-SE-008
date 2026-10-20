"""
Planning hand-off to the RDP agent
==================================
R26-SE-008 | Bandara S M Y M | IT22277886

Owns the Stage 1 -> Stage 2 boundary:

    filtered CUQA report  ->  RDP request  ->  RDP  ->  normalized plan

The report handed over is the *updated* one — every analysed file, but only
the code smells the developer kept — so a deselected smell can never reach
the planner. build_rdp_plan_input() then drops the files left with no smells,
because RDP takes files[0]["file"] as the plan's target.

The offline fallback planner in domain/plan_normalizer.py is used only when
RDP cannot be reached or refuses the report, and the response always says
which one produced the plan so a fallback is never mistaken for RDP output.
"""

from typing import Optional

import config
from clients.rdp_client import (
    RDPError, generate_plan as rdp_generate_plan, rdp_base_url,
)
from db.workflow_repository import log_event
from domain.plan_normalizer import (
    build_rdp_plan_input, fold_trace_into_plan, generate_refactoring_plan,
    normalize_rdp_plan, generate_updated_plan_report, build_approved_plan,
)

__all__ = [
    "plan_from_rdp", "build_rdp_plan_input", "normalize_rdp_plan",
    "generate_updated_plan_report", "build_approved_plan", "fold_trace_into_plan",
]


def plan_from_rdp(updated_report: dict, selected: list, target: str,
                  wf_id: Optional[str] = None, source_files: Optional[list] = None):
    """Generate the refactoring plan for the developer's smell selection.

    The updated report — every analysed file, but only the smells the developer
    kept — is forwarded to the RDP agent's POST /generate, which is the agent
    that owns planning. The local generator in orchestrator.py stays only as
    the offline fallback, and the response always says which one produced the
    plan so a fallback is never mistaken for real RDP output.

    `source_files` ({file_name, file_path, language, source_code}) is passed
    through to RDP, whose Move Method resolver reads it. Only GitHub-linked
    workflows send it, read from the analysed PR commit; ordinary workflows
    send none, exactly as before.

    Returns (plan, trace, source, warning).
    """
    rdp_input = build_rdp_plan_input(updated_report)
    if source_files:
        rdp_input["source_files"] = source_files

    if not rdp_input["files"]:
        warning = (
            "The selection contains no code smells, so the RDP agent was not called."
        )
        return generate_refactoring_plan(selected, target), {}, "diwo_local_fallback", warning

    try:
        # A large selection takes RDP minutes to score; the 120 s client
        # default cut those runs off and fell back to the local plan.
        result = rdp_generate_plan(rdp_input, timeout=config.rdp_generate_timeout())
    except RDPError as exc:
        if wf_id:
            log_event(wf_id, "plan_approval", "rdp_plan_failed",
                      {"rdp_url": rdp_base_url(), "status": exc.status, "reason": exc.message},
                      actor="system")
        return (
            generate_refactoring_plan(selected, target),
            {},
            "diwo_local_fallback",
            exc.message,
        )

    plan = normalize_rdp_plan(result["plan"], rdp_input)
    # RDP keeps the ratings, the MCDA score, the impact prediction and the
    # rejected candidates in the trace rather than on the steps. Fold them on
    # here, so the decision-support pass downstream scores each step from the
    # same numbers Stage 2 renders next to its badge.
    plan = fold_trace_into_plan(plan, result.get("trace"))

    if wf_id:
        log_event(wf_id, "plan_approval", "rdp_plan_generated", {
            "rdp_url":       rdp_base_url(),
            "plan_id":       plan.get("plan_id"),
            "target":        plan.get("target"),
            "steps":         plan["summary"]["total_steps"],
            "files_sent":    len(rdp_input["files"]),
            "smells_sent":   rdp_input["summary"]["total_code_smells"],
            "source_files_sent": len(source_files or []),
            "smells_skipped": len(result["trace"].get("plan_generation", {}).get("skipped_smells", [])),
        }, actor="system")

    return plan, result["trace"], "rdp_agent", None
