"""
Transformation stage
====================
R26-SE-008 | Bandara S M Y M | IT22277886

Owns the Stage 3 hand-off to the Safe Code Transformation & Validation agent:

    approved plan  ->  SCTVA actions  ->  SCTVA  ->  normalized result

run_transformation() is the live path. The browser used to assemble and post
this request itself; it now calls POST /api/workflows/<id>/transform and the
orchestrator does the work, so the architecture is uniformly

    DIWO frontend -> Orchestration Agent -> CUQA / RDP / SCTVA

simulate_transformation() is the older, simulated validation summary the
workflow still persists when a plan is approved (per-step pass/fail plus a
snapshot id for rollback). It is clearly labelled as simulated and is
unchanged from diwo/orchestrator.py.
"""

import uuid
import random
from datetime import datetime, timezone
from typing import Optional

from clients.sctva_client import (
    SCTVAError, execute_transformation as sctva_execute,
    fetch_workspace_sources, sctva_base_url,
)
from db.workflow_repository import log_event, parse_json_field
from domain.metrics import compute_metrics_after
from domain.audit_detail import DETAIL_LIMIT, capped
from domain.sctva_mapper import (
    DEFAULT_EXECUTION_OPTIONS, collect_plan_source_paths, normalize_execute_result,
    normalize_language, normalize_plan_for_sctva,
)

__all__ = [
    "run_transformation", "TransformationError",
    "simulate_transformation", "compute_metrics_after",
    "record_file_decisions", "metrics_after_for",
]


class TransformationError(RuntimeError):
    """The transformation could not be attempted, or SCTVA refused it.

    `status` is the HTTP status the route answers with, so the stage can tell
    "SCTVA is not running" (503) from "the plan is not executable" (422).
    """

    def __init__(self, message: str, status: int = 422, detail=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.detail = detail


def run_transformation(plan: dict, language: Optional[str] = None,
                       request_id: Optional[str] = None,
                       execution_options: Optional[dict] = None,
                       wf_id: Optional[str] = None) -> dict:
    """Run the approved plan through SCTVA and return the normalized result.

    Only the approved plan is ever passed in - the rejected steps were dropped
    at plan approval - so nothing the developer refused can be executed here.

    Two things have to be assembled before /sctva/execute can run, and neither
    is carried by the plan on its own:

      1. SOURCE TEXT. The CUQA report describes files but never ships their
         contents. /sctva/cuqa-sources reads them back out of the CUQA temp
         workspace, already shaped for the `source_files` field.
      2. ACTIONS. domain/sctva_mapper.py translates each approved step into
         the action vocabulary SCTVA executes.

    Returns { result, request, sources, mapping, sctva_url, executed_at }.
    `request` is the exact payload that was posted, kept so the stage can show
    what the agent was asked to do next to what it reported back.
    """
    try:
        mapping = normalize_plan_for_sctva(plan, correlation_id=(plan or {}).get("plan_id"))
    except ValueError as exc:
        raise TransformationError(str(exc), status=422) from exc

    paths = collect_plan_source_paths(plan)
    if not paths:
        raise TransformationError(
            "The approved plan does not name any source file, so SCTVA has nothing to "
            "transform. Re-run the plan stage against a CUQA report that carries file paths.",
            status=422,
        )

    try:
        sources = fetch_workspace_sources(paths)
    except SCTVAError as exc:
        if wf_id:
            # Named paths, because "could not read 3 files" is not fixable.
            log_event(wf_id, "transformation", "sctva_sources_failed",
                      {"sctva_url": sctva_base_url(), "status": exc.status,
                       "reason": exc.message}, actor="system")
        raise TransformationError(exc.message, status=exc.status,
                                  detail={"sctva_url": sctva_base_url()}) from exc

    if not sources["files"]:
        raise TransformationError(
            f"SCTVA could not read the source of any planned file "
            f"({len(sources['missing'])} missing). The CUQA temp workspace is where it "
            "looks, so re-run the analysis in the Code Smell Review stage to recreate it, "
            "then transform again.",
            status=422,
            detail={"missing": sources["missing"]},
        )

    resolved_language = (
        normalize_language(language)
        or normalize_language((sources["files"][0] or {}).get("language"))
        or ""
    )
    if not resolved_language:
        raise TransformationError(
            f"SCTVA does not support language '{language or 'unknown'}'. "
            "Supported: c, java, python.",
            status=422,
        )

    request = {
        "request_id": request_id or f"sctva_diwo_{uuid.uuid4().hex[:12]}",
        "language": resolved_language,
        "source_files": sources["files"],
        "refactoring_plan": mapping["plan"],
        "execution_options": {**DEFAULT_EXECUTION_OPTIONS, **(execution_options or {})},
    }

    try:
        raw = sctva_execute(request)
    except SCTVAError as exc:
        if wf_id:
            log_event(wf_id, "transformation", "sctva_execute_failed",
                      {"sctva_url": sctva_base_url(), "status": exc.status,
                       "reason": exc.message}, actor="system")
        raise TransformationError(exc.message, status=exc.status,
                                  detail={"sctva_url": sctva_base_url()}) from exc

    result = normalize_execute_result(raw, sources["files"])

    if wf_id:
        # Per FILE, and counts for the rest.
        #
        # There is deliberately no per-ACTION list here. The actions are a
        # one-to-one restatement of the approved plan steps, which the
        # plan_approved entry already itemises as smell -> refactoring -> file;
        # repeating them under a second name produced a block of rows carrying
        # nothing the reader had not just read. The totals below say how many
        # were dispatched, and the plan entry says what they were.
        files_touched, files_omitted = capped([
            {"file": f["path"], "changed": f["changed"], "success": f["success"],
             "replacements": f["total_replacements"],
             "rolled_back": f["rollback_occurred"]}
            for f in result.get("files") or []
        ])

        log_event(wf_id, "transformation", "sctva_transformation_executed", {
            "sctva_url":     sctva_base_url(),
            "request_id":    result["requestId"],
            "language":      resolved_language,
            "actions":       len(mapping["plan"]["actions"]),
            "executable":    mapping["executableCount"],
            "noops":         mapping["noopCount"],
            "files_sent":    len(sources["files"]),
            "files_missing": len(sources["missing"]),
            "success":       result["success"],
            "rollback":      result["rollbackOccurred"],
            "file_detail":   files_touched,
            "missing_files": sources["missing"][:DETAIL_LIMIT],
            **({"files_omitted": files_omitted} if files_omitted else {}),
        }, actor="system")

    return {
        "result":      result,
        "request":     request,
        "sources":     sources,
        "mapping":     mapping,
        "sctva_url":   sctva_base_url(),
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


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
