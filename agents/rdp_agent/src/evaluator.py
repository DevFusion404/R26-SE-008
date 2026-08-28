"""
RDP Plan Evaluator
==================

Evaluation helpers for measuring how good a generated RDP refactoring plan is.
The evaluator treats RDP as a planning/recommendation system, so it measures
recommendation correctness, ranking quality, plan coverage, precondition
health, and optional downstream execution/validation success.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


EXPECTED_REFACTORING_KEYS = (
    "expected_refactoring",
    "actual_refactoring",
    "ground_truth_refactoring",
    "refactoring",
    "refactoring_suggested",
    "refactoring_applied",
)


def _normalize_name(value: Any) -> str:
    """Normalize refactoring names for stable comparison."""
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _number(value: Any) -> Optional[float]:
    """Convert numeric-looking values to float, preserving missing values."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        if math.isnan(float(value)):
            return None
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _safe_div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _round(value: Any, digits: int = 4) -> Optional[float]:
    num = _number(value)
    return round(num, digits) if num is not None else None


def _plan_dict(plan: Any) -> Dict[str, Any]:
    if hasattr(plan, "to_dict"):
        return plan.to_dict()
    return dict(plan or {})


def _trace_dict(trace: Any) -> Dict[str, Any]:
    return dict(trace or {})


def _expected_items(expected: Any) -> List[Dict[str, str]]:
    """Convert supported ground-truth shapes into evaluator records.

    Supported forms:
      - {"s1": "Extract Method", "s2": "Move Method"}
      - [{"smell_id": "s1", "expected_refactoring": "Extract Method"}, ...]
      - {"expected": [...]} or {"smells": [...]} with one of the expected keys
    """
    if expected is None:
        return []

    if isinstance(expected, Mapping):
        if isinstance(expected.get("expected"), Sequence):
            return _expected_items(expected.get("expected"))
        if isinstance(expected.get("smells"), Sequence):
            return _expected_items(expected.get("smells"))

        items = []
        for smell_id, refactoring in expected.items():
            if isinstance(refactoring, Mapping):
                refactoring = _extract_expected_refactoring(refactoring)
            if refactoring not in (None, "", True, False):
                items.append({
                    "smell_id": str(smell_id),
                    "expected_refactoring": str(refactoring),
                })
        return items

    items = []
    for raw in expected if isinstance(expected, Sequence) else []:
        if not isinstance(raw, Mapping):
            continue
        smell_id = raw.get("smell_id") or raw.get("id")
        refactoring = _extract_expected_refactoring(raw)
        if smell_id and refactoring not in (None, "", True, False):
            items.append({
                "smell_id": str(smell_id),
                "expected_refactoring": str(refactoring),
            })
    return items


def _extract_expected_refactoring(raw: Mapping[str, Any]) -> Any:
    for key in EXPECTED_REFACTORING_KEYS:
        value = raw.get(key)
        if key == "refactoring_applied" and value in (0, 1, True, False):
            continue
        if value not in (None, "", True, False):
            return value
    return None


def _steps_by_smell(plan: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    steps = plan.get("steps") or []
    return {
        str(step.get("smell_id")): dict(step)
        for step in steps
        if isinstance(step, Mapping) and step.get("smell_id")
    }


def _candidate_entries(trace: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    entries = trace.get("candidate_generation") or []
    return {
        str(entry.get("smell_id")): dict(entry)
        for entry in entries
        if isinstance(entry, Mapping) and entry.get("smell_id")
    }


def _ranked_candidates(entry: Mapping[str, Any]) -> List[Dict[str, Any]]:
    candidates = [
        dict(candidate)
        for candidate in (entry.get("candidates") or [])
        if isinstance(candidate, Mapping) and candidate.get("preconditions_met")
    ]
    candidates.sort(
        key=lambda candidate: _number(candidate.get("score")) or float("-inf"),
        reverse=True,
    )
    return candidates


def _find_rank(expected_refactoring: str, ranked: Sequence[Mapping[str, Any]]) -> Optional[int]:
    expected_norm = _normalize_name(expected_refactoring)
    for index, candidate in enumerate(ranked, start=1):
        if _normalize_name(candidate.get("name")) == expected_norm:
            return index
    return None


def _score_from_step_or_trace(
    step: Optional[Mapping[str, Any]],
    selection: Optional[Mapping[str, Any]],
) -> Optional[float]:
    if step:
        score = _number(step.get("score"))
        if score is not None:
            return score
    if selection:
        score = _number(selection.get("selected_score"))
        if score is not None:
            return score
    return None


def _precondition_metrics(trace: Mapping[str, Any]) -> Dict[str, Any]:
    total_candidates = 0
    viable_candidates = 0
    smells_with_viable_candidate = 0

    for entry in trace.get("candidate_generation") or []:
        if not isinstance(entry, Mapping):
            continue
        candidates = [
            candidate for candidate in entry.get("candidates") or []
            if isinstance(candidate, Mapping)
        ]
        passed = [candidate for candidate in candidates if candidate.get("preconditions_met")]
        total_candidates += len(candidates)
        viable_candidates += len(passed)
        if passed:
            smells_with_viable_candidate += 1

    return {
        "total_candidates": total_candidates,
        "viable_candidates": viable_candidates,
        "precondition_pass_rate": round(
            _safe_div(viable_candidates, total_candidates),
            4,
        ),
        "smells_with_viable_candidate": smells_with_viable_candidate,
    }


def _downstream_metrics(downstream_result: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Extract optional SCTVA-style execution/validation metrics.

    This accepts a few common shapes so the evaluator can be used before the
    downstream contract is fully standardized:
      - {"successful_steps": 4, "total_steps": 5}
      - {"applied_steps": [{"success": true}, ...]}
      - {"validation_results": [{"passed": true}, ...]}
    """
    if not isinstance(downstream_result, Mapping):
        return {}

    successful = _number(downstream_result.get("successful_steps"))
    total = _number(downstream_result.get("total_steps"))

    applied_steps = downstream_result.get("applied_steps")
    if isinstance(applied_steps, Sequence) and not isinstance(applied_steps, (str, bytes)):
        successes = [
            step for step in applied_steps
            if isinstance(step, Mapping)
            and (
                step.get("success") is True
                or step.get("passed") is True
                or str(step.get("status", "")).lower() in {"success", "passed", "applied"}
            )
        ]
        successful = float(len(successes))
        total = float(len(applied_steps))

    validation_results = downstream_result.get("validation_results")
    validation_passed = None
    validation_total = None
    if isinstance(validation_results, Sequence) and not isinstance(
        validation_results,
        (str, bytes),
    ):
        passed = [
            item for item in validation_results
            if isinstance(item, Mapping)
            and (
                item.get("passed") is True
                or item.get("success") is True
                or str(item.get("status", "")).lower() in {"success", "passed"}
            )
        ]
        validation_passed = float(len(passed))
        validation_total = float(len(validation_results))

    metrics: Dict[str, Any] = {}
    if successful is not None and total is not None:
        metrics.update({
            "successful_steps": int(successful),
            "executed_steps": int(total),
            "execution_success_rate": round(_safe_div(successful, total), 4),
        })
    if validation_passed is not None and validation_total is not None:
        metrics.update({
            "validation_passed": int(validation_passed),
            "validation_total": int(validation_total),
            "validation_pass_rate": round(
                _safe_div(validation_passed, validation_total),
                4,
            ),
        })
    return metrics


@dataclass
class PlanEvaluation:
    """Serializable result returned by the RDP evaluator."""

    metrics: Dict[str, Any]
    per_smell: List[Dict[str, Any]] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def evaluate_rdp_plan(
    expected: Any,
    generated_plan: Any,
    trace: Optional[Mapping[str, Any]] = None,
    top_k: int = 3,
    downstream_result: Optional[Mapping[str, Any]] = None,
) -> PlanEvaluation:
    """Evaluate a generated RDP plan against expected refactoring decisions.

    Args:
        expected: Ground truth mapping/list. See ``_expected_items`` for shapes.
        generated_plan: RDP plan dict or ``RefactoringPlan``.
        trace: Optional pipeline trace from ``process_report_with_trace``.
        top_k: Candidate ranking cutoff for top-k accuracy and NDCG.
        downstream_result: Optional SCTVA execution/validation result.

    Returns:
        ``PlanEvaluation`` containing aggregate metrics and per-smell evidence.
    """
    plan = _plan_dict(generated_plan)
    trace_data = _trace_dict(trace)
    top_k = max(1, int(top_k or 1))

    expected_records = _expected_items(expected)
    expected_by_smell = {
        item["smell_id"]: item["expected_refactoring"]
        for item in expected_records
    }
    steps_by_smell = _steps_by_smell(plan)
    candidate_by_smell = _candidate_entries(trace_data)

    exact_correct = 0
    planned_with_expected = 0
    top_k_correct = 0
    reciprocal_rank_sum = 0.0
    ndcg_sum = 0.0
    selected_scores: List[float] = []
    ml_adjustments: List[float] = []
    per_smell: List[Dict[str, Any]] = []

    for smell_id, expected_refactoring in expected_by_smell.items():
        step = steps_by_smell.get(smell_id)
        selection = candidate_by_smell.get(smell_id)
        selected = None
        if step:
            selected = step.get("refactoring")
            planned_with_expected += 1
        elif selection:
            selected = selection.get("selected")

        score = _score_from_step_or_trace(step, selection)
        if score is not None:
            selected_scores.append(score)

        ranked = _ranked_candidates(selection or {})
        rank = _find_rank(expected_refactoring, ranked)
        in_top_k = rank is not None and rank <= top_k
        if in_top_k:
            top_k_correct += 1
            ndcg_sum += 1.0 / math.log2(rank + 1)
        if rank is not None:
            reciprocal_rank_sum += 1.0 / rank

        matched = _normalize_name(selected) == _normalize_name(expected_refactoring)
        if matched:
            exact_correct += 1

        selected_candidate = next(
            (
                candidate for candidate in ranked
                if _normalize_name(candidate.get("name")) == _normalize_name(selected)
            ),
            {},
        )
        adjustment = _number(selected_candidate.get("ml_adjustment"))
        if adjustment is not None:
            ml_adjustments.append(adjustment)

        per_smell.append({
            "smell_id": smell_id,
            "expected_refactoring": expected_refactoring,
            "selected_refactoring": selected,
            "matched": matched,
            "selected_score": _round(score),
            "scoring_method": (
                (selection or {}).get("scoring_method")
                or selected_candidate.get("scoring_method")
                or (step or {}).get("scoring_method")
            ),
            "expected_rank": rank,
            f"in_top_{top_k}": in_top_k,
            "candidate_count": len((selection or {}).get("candidates") or []),
            "viable_candidate_count": len(ranked),
        })

    total_expected = len(expected_by_smell)
    planned_smells = len(steps_by_smell)
    trace_total_smells = (
        (trace_data.get("input_summary") or {}).get("total_smells")
        if isinstance(trace_data.get("input_summary"), Mapping)
        else None
    )
    total_smells = int(_number(trace_total_smells) or total_expected or planned_smells)

    metrics: Dict[str, Any] = {
        "total_expected": total_expected,
        "total_smells": total_smells,
        "planned_smells": planned_smells,
        "planned_expected_smells": planned_with_expected,
        "correct_top1": exact_correct,
        "recommendation_accuracy": round(_safe_div(exact_correct, total_expected), 4),
        "planned_selection_accuracy": round(
            _safe_div(exact_correct, planned_with_expected),
            4,
        ),
        f"top_{top_k}_accuracy": round(_safe_div(top_k_correct, total_expected), 4),
        "plan_coverage": round(_safe_div(planned_smells, total_smells), 4),
        "expected_plan_coverage": round(
            _safe_div(planned_with_expected, total_expected),
            4,
        ),
        "mean_reciprocal_rank": round(
            _safe_div(reciprocal_rank_sum, total_expected),
            4,
        ),
        f"ndcg_at_{top_k}": round(_safe_div(ndcg_sum, total_expected), 4),
        "average_selected_score": round(
            _safe_div(sum(selected_scores), len(selected_scores)),
            4,
        ),
        "average_ml_adjustment": round(
            _safe_div(sum(ml_adjustments), len(ml_adjustments)),
            4,
        ),
    }
    metrics.update(_precondition_metrics(trace_data))
    metrics.update(_downstream_metrics(downstream_result))

    summary = (
        f"RDP evaluated {total_expected} expected smell(s): "
        f"top-1 accuracy {metrics['recommendation_accuracy']:.2%}, "
        f"top-{top_k} accuracy {metrics[f'top_{top_k}_accuracy']:.2%}, "
        f"coverage {metrics['plan_coverage']:.2%}."
    )
    return PlanEvaluation(metrics=metrics, per_smell=per_smell, summary=summary)


def evaluate_rdp_result(
    expected: Any,
    result: Mapping[str, Any],
    top_k: int = 3,
    downstream_result: Optional[Mapping[str, Any]] = None,
) -> PlanEvaluation:
    """Evaluate a ``process_report_with_trace`` or ``/generate`` response."""
    return evaluate_rdp_plan(
        expected=expected,
        generated_plan=result.get("plan") or {},
        trace=result.get("trace") or {},
        top_k=top_k,
        downstream_result=downstream_result,
    )


def _load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: str, data: Mapping[str, Any]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)


def main(argv: Optional[Iterable[str]] = None) -> None:
    """CLI for evaluating already-generated RDP output."""
    parser = argparse.ArgumentParser(
        description="Evaluate an RDP refactoring plan against expected decisions.",
    )
    parser.add_argument("--expected", required=True, help="JSON ground-truth file.")
    parser.add_argument(
        "--result",
        help="RDP result JSON containing both plan and trace.",
    )
    parser.add_argument("--plan", help="Generated RDP plan JSON.")
    parser.add_argument("--trace", help="Pipeline trace JSON.")
    parser.add_argument("--downstream", help="Optional SCTVA result JSON.")
    parser.add_argument("--top-k", type=int, default=3, help="Top-k ranking cutoff.")
    parser.add_argument("--output", "-o", help="Where to write evaluation JSON.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    expected = _load_json(args.expected)
    downstream = _load_json(args.downstream) if args.downstream else None

    if args.result:
        result = _load_json(args.result)
        evaluation = evaluate_rdp_result(
            expected,
            result,
            top_k=args.top_k,
            downstream_result=downstream,
        )
    elif args.plan:
        evaluation = evaluate_rdp_plan(
            expected,
            _load_json(args.plan),
            trace=_load_json(args.trace) if args.trace else None,
            top_k=args.top_k,
            downstream_result=downstream,
        )
    else:
        parser.error("Provide either --result or --plan.")

    data = evaluation.to_dict()
    if args.output:
        _write_json(args.output, data)
    print(json.dumps(data, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
