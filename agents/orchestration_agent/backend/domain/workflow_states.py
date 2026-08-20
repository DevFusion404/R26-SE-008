"""
DIWO workflow state machine
===========================
R26-SE-008 | Bandara S M Y M | IT22277886

The 5-stage workflow the developer walks through, plus the two terminal
states. Extracted verbatim from the former diwo/orchestrator.py so the stage
vocabulary has one home and the services can guard on it without importing the
planning code.

Stages:
  1. smell_review        – Agent 1 (CUQA) output shown to the developer
  2. smell_selection     – Developer selects which smells to address
  3. plan_approval       – Agent 2 (RDP) plan shown; approve / reject / edit
  4. transformation      – Agent 3 (SCTVA) transforms; result shown for approval
  5. comparison          – Before/after metrics + audit log view

Transitions are gated: each stage requires an explicit developer action.
"""

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

#: Severity vocabulary shared by the CUQA normalizer and the metrics helpers.
SEVERITIES = ("high", "medium", "low")

#: Verdicts accepted at each human-decision point.
PLAN_DECISIONS = ("approve", "reject", "modify")
TRANSFORMATION_DECISIONS = ("accept", "rollback")


def next_stage(current: str) -> str:
    idx = STAGES.index(current)
    return STAGES[min(idx + 1, len(STAGES) - 1)]


def normalize_severity(value) -> str:
    severity = str(value or "low").lower()
    return severity if severity in SEVERITIES else "low"
