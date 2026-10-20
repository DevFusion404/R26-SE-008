from sctva.model_assistance.benchmark import (
    BENCHMARK_CASES,
    aggregate_language_metrics,
    cases_for_languages,
    markdown_report,
)


def test_benchmark_has_three_isolated_cases_for_each_supported_language():
    assert {case.language for case in BENCHMARK_CASES} == {"java", "python", "c"}
    for language in ("java", "python", "c"):
        cases = cases_for_languages([language])
        assert len(cases) == 3
        assert all(case.language == language for case in cases)
        assert all(case.parameters.get("source_file") is None for case in cases)


def test_benchmark_aggregation_excludes_rule_fallback_from_model_retention():
    rows = [
        {
            "language": "java", "model_attempts": 2, "generated_candidates": 1,
            "action_checkpoint_passed_candidates": 1, "accepted_model_actions": 1,
            "final_model_output_retained": True, "validated_quality_score_percent": 95.0,
            "total_generation_seconds": 8.0, "rule_fallback_used": False,
        },
        {
            "language": "java", "model_attempts": 1, "generated_candidates": 1,
            "action_checkpoint_passed_candidates": 0, "accepted_model_actions": 0,
            "final_model_output_retained": False, "validated_quality_score_percent": None,
            "total_generation_seconds": 4.0, "rule_fallback_used": True,
        },
    ]

    metrics = aggregate_language_metrics(rows)["java"]

    assert metrics["candidate_generation_rate_percent"] == 66.7
    assert metrics["action_checkpoint_pass_rate_percent"] == 50.0
    assert metrics["end_to_end_model_retention_rate_percent"] == 50.0
    assert metrics["mean_validated_quality_percent"] == 95.0
    assert "Rule-based fallback" in markdown_report({"language_metrics": {"java": metrics}})
