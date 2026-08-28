"""Tests for RDP plan evaluation metrics."""

from __future__ import annotations

from rdp_agent import evaluate_rdp_plan, evaluate_rdp_result


def _sample_plan():
    return {
        "plan_id": "plan_eval",
        "target": "Example.py",
        "steps": [
            {
                "step_id": 1,
                "smell_id": "s1",
                "refactoring": "Extract Method",
                "target": {},
                "parameters": {},
                "explanation": "",
            },
            {
                "step_id": 2,
                "smell_id": "s2",
                "refactoring": "Extract Class",
                "target": {},
                "parameters": {},
                "explanation": "",
            },
        ],
        "summary": "2-step plan",
    }


def _sample_trace():
    return {
        "input_summary": {"total_smells": 3},
        "candidate_generation": [
            {
                "smell_id": "s1",
                "selected": "Extract Method",
                "selected_score": 0.91,
                "scoring_method": "mcda_ml",
                "candidates": [
                    {
                        "name": "Extract Method",
                        "score": 0.91,
                        "preconditions_met": True,
                        "scoring_method": "mcda_ml",
                        "ml_adjustment": 0.08,
                    },
                    {
                        "name": "Replace Temp with Query",
                        "score": 0.72,
                        "preconditions_met": True,
                        "scoring_method": "mcda_ml",
                        "ml_adjustment": -0.02,
                    },
                    {
                        "name": "Introduce Parameter Object",
                        "score": None,
                        "preconditions_met": False,
                    },
                ],
            },
            {
                "smell_id": "s2",
                "selected": "Extract Class",
                "selected_score": 0.66,
                "scoring_method": "mcda",
                "candidates": [
                    {
                        "name": "Extract Class",
                        "score": 0.66,
                        "preconditions_met": True,
                        "scoring_method": "mcda",
                    },
                    {
                        "name": "Extract Subclass",
                        "score": 0.61,
                        "preconditions_met": True,
                        "scoring_method": "mcda",
                    },
                ],
            },
            {
                "smell_id": "s3",
                "selected": None,
                "selected_score": None,
                "candidates": [
                    {
                        "name": "Move Method",
                        "score": None,
                        "preconditions_met": False,
                    },
                ],
            },
        ],
    }


def test_evaluate_rdp_plan_reports_accuracy_ranking_and_coverage():
    expected = [
        {"smell_id": "s1", "expected_refactoring": "Extract Method"},
        {"smell_id": "s2", "expected_refactoring": "Extract Subclass"},
        {"smell_id": "s3", "expected_refactoring": "Move Method"},
    ]

    evaluation = evaluate_rdp_plan(
        expected=expected,
        generated_plan=_sample_plan(),
        trace=_sample_trace(),
        top_k=2,
    )

    metrics = evaluation.metrics
    assert metrics["total_expected"] == 3
    assert metrics["planned_smells"] == 2
    assert metrics["correct_top1"] == 1
    assert metrics["recommendation_accuracy"] == 0.3333
    assert metrics["planned_selection_accuracy"] == 0.5
    assert metrics["top_2_accuracy"] == 0.6667
    assert metrics["plan_coverage"] == 0.6667
    assert metrics["expected_plan_coverage"] == 0.6667
    assert metrics["mean_reciprocal_rank"] == 0.5
    assert metrics["ndcg_at_2"] == 0.5436
    assert metrics["precondition_pass_rate"] == 0.6667
    assert metrics["average_selected_score"] == 0.785
    assert metrics["average_ml_adjustment"] == 0.08

    by_smell = {item["smell_id"]: item for item in evaluation.per_smell}
    assert by_smell["s1"]["matched"] is True
    assert by_smell["s2"]["expected_rank"] == 2
    assert by_smell["s3"]["expected_rank"] is None
    assert "top-1 accuracy 33.33%" in evaluation.summary


def test_evaluate_rdp_result_accepts_generate_response_and_downstream_metrics():
    expected = {"s1": "Extract Method", "s2": "Extract Class"}
    result = {"plan": _sample_plan(), "trace": _sample_trace()}
    downstream = {
        "applied_steps": [
            {"status": "applied"},
            {"status": "failed"},
        ],
        "validation_results": [
            {"passed": True},
            {"passed": False},
            {"status": "passed"},
        ],
    }

    evaluation = evaluate_rdp_result(
        expected=expected,
        result=result,
        downstream_result=downstream,
    )

    assert evaluation.metrics["recommendation_accuracy"] == 1.0
    assert evaluation.metrics["execution_success_rate"] == 0.5
    assert evaluation.metrics["validation_pass_rate"] == 0.6667
