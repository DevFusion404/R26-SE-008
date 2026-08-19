"""
Transformation stage
====================
R26-SE-008 | Bandara S M Y M | IT22277886

Stands in for Agent 3 while the browser owns the live SCTVA call.

Where the transformation actually happens today
-----------------------------------------------
The approved plan is posted to the SCTVA agent from the frontend
(frontend/src/pages/diwo/services/sctvaApi.js -> POST /sctva/execute), and the
code it returns is what the Transformation and Results stages render. That
integration is untouched.

What this module produces is the *validation summary* the workflow persists
when a plan is approved: per-step pass/fail, a snapshot id for rollback, and
the mock before/after used when no live SCTVA output is available. It is
simulated, and clearly labelled as such, exactly as it was in
diwo/orchestrator.py.

clients/sctva_client.py is the seam for moving the execute call behind the
orchestrator later, so the frontend would not need a second integration path.
"""

import uuid
import random
from datetime import datetime, timezone

from db.workflow_repository import parse_json_field
from domain.metrics import compute_metrics_after

__all__ = [
    "simulate_transformation", "compute_metrics_after",
    "record_file_decisions", "metrics_after_for",
]


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
# Result decision
# ─────────────────────────────────────────────────────────────────────────────

def record_file_decisions(transformation_result: dict, accepted_files: list,
                          rejected_files: list, written_files: list) -> dict:
    """Stamp the developer's per-file verdict onto the stored result.

    A rejected file is written back as its original source, so recording the
    split is what makes the audit trail say which files were actually
    refactored and which were reverted.
    """
    result = dict(transformation_result or {})
    result["file_decisions"] = {
        "accepted": accepted_files,
        "rejected_reverted": rejected_files,
        "written": written_files,
    }
    return result


def metrics_after_for(workflow: dict, transformation_result: dict) -> dict:
    """Recompute the after-metrics for a workflow's stored before-metrics."""
    metrics_before = parse_json_field(workflow, "metrics_before_json") or {}
    selected = parse_json_field(workflow, "selected_smells_json") or []
    return compute_metrics_after(
        metrics_before, transformation_result["steps_passed"], len(selected)
    )
