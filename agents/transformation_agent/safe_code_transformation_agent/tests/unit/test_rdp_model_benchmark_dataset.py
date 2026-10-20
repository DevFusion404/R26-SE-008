from sctva.contracts import RefactoringAction
from sctva.model_assistance.benchmark import (
    aggregate_action_metrics,
    classification_metrics,
)
from sctva.model_assistance.rdp_benchmark_dataset import (
    RDP_REFACTORINGS,
    generate_rdp_benchmark_cases,
    rdp_action_coverage,
)


def test_rdp_dataset_covers_every_catalog_refactoring_and_language():
    cases = generate_rdp_benchmark_cases(seed=7)

    assert set(rdp_action_coverage()) == set(RDP_REFACTORINGS)
    assert {case.source_refactoring for case in cases} == set(RDP_REFACTORINGS)
    assert {case.language for case in cases} == {"java", "python", "c"}
    assert any(case.expected_model_acceptance for case in cases)
    assert any(not case.expected_model_acceptance for case in cases)
    assert all(RefactoringAction.from_dict(case.action_payload()) for case in cases)

    for refactoring in RDP_REFACTORINGS:
        refactoring_cases = [
            case for case in cases if case.source_refactoring == refactoring
        ]
        assert any(case.expected_model_acceptance for case in refactoring_cases)
        assert any(not case.expected_model_acceptance for case in refactoring_cases)


def test_rdp_dataset_is_seeded_and_has_valid_rdp_payloads():
    first = generate_rdp_benchmark_cases(seed=19, samples_per_action=2)
    second = generate_rdp_benchmark_cases(seed=19, samples_per_action=2)
    different = generate_rdp_benchmark_cases(seed=20, samples_per_action=2)

    assert first == second
    assert first != different
    assert all(case.action_payload()["parameters"]["source_file"] == case.file_name for case in first)


def test_classification_metrics_report_accuracy_and_f1_without_rule_credit():
    rows = [
        {"language": "java", "action_type": "extract_method", "expected_model_acceptance": True,
         "final_model_output_retained": True},
        {"language": "java", "action_type": "extract_method", "expected_model_acceptance": True,
         "final_model_output_retained": False, "rule_fallback_used": True},
        {"language": "java", "action_type": "extract_method", "expected_model_acceptance": False,
         "final_model_output_retained": False},
        {"language": "java", "action_type": "extract_method", "expected_model_acceptance": False,
         "final_model_output_retained": True},
    ]

    metrics = classification_metrics(rows)
    assert metrics["evaluated_cases"] == 4
    assert metrics["positive_cases"] == 2
    assert metrics["negative_cases"] == 2
    assert metrics["true_positive"] == 1
    assert metrics["false_positive"] == 1
    assert metrics["true_negative"] == 1
    assert metrics["false_negative"] == 1
    assert metrics["accuracy_percent"] == 50.0
    assert metrics["precision_percent"] == 50.0
    assert metrics["recall_percent"] == 50.0
    assert metrics["specificity_percent"] == 50.0
    assert metrics["f1_score_percent"] == 50.0
    assert metrics["positive_model_attempt_rate_percent"] == 0.0
    by_action = aggregate_action_metrics(rows)["java:extract_method"]
    assert by_action["f1_score_percent"] == 50.0
