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

from clients.rdp_client import (
    RDPError, generate_plan as rdp_generate_plan, rdp_base_url,
)
from db.workflow_repository import log_event
from domain.plan_normalizer import (
    build_rdp_plan_input, generate_refactoring_plan, normalize_rdp_plan,
    generate_updated_plan_report, build_approved_plan,
)

__all__ = [
    "plan_from_rdp", "build_rdp_plan_input", "normalize_rdp_plan",
    "generate_updated_plan_report", "build_approved_plan",
]


def plan_from_rdp(updated_report: dict, selected: list, target: str,
                  wf_id: Optional[str] = None):
    """Generate the refactoring plan for the developer's smell selection.

    The updated report — every analysed file, but only the smells the developer
    kept — is forwarded to the RDP agent's POST /generate, which is the agent
    that owns planning. The local generator in orchestrator.py stays only as
    the offline fallback, and the response always says which one produced the
    plan so a fallback is never mistaken for real RDP output.

    Returns (plan, trace, source, warning).
    """
    rdp_input = build_rdp_plan_input(updated_report)

    if not rdp_input["files"]:
        warning = (
            "The selection contains no code smells, so the RDP agent was not called."
        )
        return generate_refactoring_plan(selected, target), {}, "diwo_local_fallback", warning

    try:
        result = rdp_generate_plan(rdp_input)
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

    if wf_id:
        log_event(wf_id, "plan_approval", "rdp_plan_generated", {
            "rdp_url":       rdp_base_url(),
            "plan_id":       plan.get("plan_id"),
            "target":        plan.get("target"),
            "steps":         plan["summary"]["total_steps"],
            "files_sent":    len(rdp_input["files"]),
            "smells_sent":   rdp_input["summary"]["total_code_smells"],
            "smells_skipped": len(result["trace"].get("plan_generation", {}).get("skipped_smells", [])),
        }, actor="system")

    return plan, result["trace"], "rdp_agent", None
