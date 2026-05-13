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
