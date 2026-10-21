from sctva.model_assistance.rule_benchmark import (
    aggregate_language_metrics,
    classification_metrics,
    markdown_report,
)


def test_rule_metrics_use_retained_rule_output_as_the_positive_prediction():
    rows = [
        {
            "language": "java",
            "expected_rule_acceptance": True,
            "final_rule_output_retained": True,
            "accepted_rule_actions": 1,
            "final_validation_passed": True,
            "total_execution_seconds": 0.5,
        },
        {
            "language": "java",
            "expected_rule_acceptance": True,
            "final_rule_output_retained": False,
            "accepted_rule_actions": 0,
            "final_validation_passed": True,
            "total_execution_seconds": 0.2,
        },
        {
            "language": "java",
            "expected_rule_acceptance": False,
            "final_rule_output_retained": False,
            "accepted_rule_actions": 0,
            "final_validation_passed": True,
            "total_execution_seconds": 0.1,
        },
        {
            "language": "java",
            "expected_rule_acceptance": False,
            "final_rule_output_retained": True,
            "accepted_rule_actions": 1,
            "final_validation_passed": True,
            "total_execution_seconds": 0.2,
        },
    ]

    metrics = classification_metrics(rows)

    assert metrics["true_positive"] == 1
    assert metrics["false_positive"] == 1
    assert metrics["true_negative"] == 1
    assert metrics["false_negative"] == 1
    assert metrics["accuracy_percent"] == 50.0
    assert metrics["precision_percent"] == 50.0
    assert metrics["recall_percent"] == 50.0
    assert metrics["f1_score_percent"] == 50.0

    language_metrics = aggregate_language_metrics(rows)["java"]
    assert language_metrics["rule_transformation_rate_percent"] == 50.0
    assert language_metrics["final_validation_pass_rate_percent"] == 100.0
    assert "Rule-Based Transformation Evaluation" in markdown_report(
        {
            "overall_classification_metrics": metrics,
            "language_metrics": {"java": language_metrics},
            "action_metrics": {"java:introduce_constant": {"language": "java", "action_type": "introduce_constant", **metrics}},
        }
    )
    assert "Refactoring Action Results" in markdown_report(
        {
            "overall_classification_metrics": metrics,
            "language_metrics": {"java": language_metrics},
            "action_metrics": {"java:introduce_constant": {"language": "java", "action_type": "introduce_constant", **metrics}},
        }
    )
