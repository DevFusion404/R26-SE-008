from sctva.agent import SafeCodeTransformationValidationAgent
from sctva.contracts import RefactoringAction
from sctva.model_assistance.benchmark import (
    aggregate_action_metrics,
    classification_metrics,
)
from sctva.transformers import java_transformers
from sctva.transformers.java_extract_class import apply_extract_class
from sctva.transformers.java_hide_delegate import apply_hide_delegate, validate_hide_delegate
from sctva.model_assistance.rdp_benchmark_dataset import (
    RDP_REFACTORINGS,
    RULE_IMPLEMENTED_ACTIONS_BY_LANGUAGE,
    cases_for_rule_implemented_actions,
    generate_rdp_benchmark_cases,
    rdp_action_coverage,
    rule_implemented_action_coverage,
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


def test_rule_capability_dataset_excludes_model_only_rdp_actions():
    all_cases = generate_rdp_benchmark_cases(seed=23)
    rule_cases = generate_rdp_benchmark_cases(
        seed=23,
        implemented_rules_only=True,
    )

    assert len(rule_cases) < len(all_cases)
    assert rule_cases == cases_for_rule_implemented_actions(all_cases)
    assert all(
        case.action_type in RULE_IMPLEMENTED_ACTIONS_BY_LANGUAGE[case.language]
        for case in rule_cases
    )
    coverage = rule_implemented_action_coverage()
    assert "Collapse Hierarchy" not in coverage
    assert "move_c_function" not in RULE_IMPLEMENTED_ACTIONS_BY_LANGUAGE["c"]
    assert coverage["Extract Method"] == ("c", "java", "python")


def test_c_rule_benchmark_cases_satisfy_rule_safety_preconditions():
    cases = generate_rdp_benchmark_cases(
        seed=23,
        include_negative=False,
        languages=["c"],
        implemented_rules_only=True,
    )
    by_action = {case.action_type: case for case in cases}

    assert "static int shared_" in by_action["encapsulate_c_variable"].source_code
    component = by_action["extract_c_component"]
    assert component.parameters["target_file"] == "same_file"
    assert len(component.parameters["functions_to_extract"]) == 2
    assert component.parameters["globals_to_extract"]
    assert "static int process_" in by_action["replace_parameter_with_method_call"].source_code
    assert "char destination[64]" in by_action["replace_unsafe_function"].source_code


def test_java_rule_benchmark_cases_satisfy_rule_safety_preconditions():
    cases = generate_rdp_benchmark_cases(
        seed=23,
        include_negative=False,
        languages=["java"],
        implemented_rules_only=True,
    )
    by_action = {case.action_type: case for case in cases}

    extract_case = by_action["extract_java_class"]
    extracted, extract_count, extract_metadata = apply_extract_class(
        extract_case.source_code,
        source_file=extract_case.file_name,
        current_file_name=extract_case.file_name,
        source_class=extract_case.parameters["source_class"],
        new_class_name=extract_case.parameters["new_class_name"],
        methods_to_extract=extract_case.parameters["methods_to_extract"],
        fields_to_extract=None,
        preserve_public_api=True,
        delegation_strategy="wrapper",
        target_file="same_file",
    )
    assert extract_count == 1
    assert extract_metadata["status"] == "success"
    assert extract_metadata["validation"]["large_class_reduction"] == "PASS"
    assert "class AccountDetails" in extracted

    delegate_case = by_action["hide_delegate"]
    delegated, delegate_count, delegate_metadata = apply_hide_delegate(
        delegate_case.source_code,
        **delegate_case.parameters,
    )
    assert delegate_count == 2
    assert delegate_metadata["status"] == "success"
    assert validate_hide_delegate(
        delegate_case.source_code,
        delegated,
        **delegate_metadata["effective_action_parameters"],
    )["passed"]

    constant_case = by_action["introduce_constant"]
    constant_code, constant_count, constant_metadata = (
        java_transformers.apply_introduce_constant(
            constant_case.source_code,
            constant_case.parameters["literal_value"],
            constant_case.parameters["constant_name"],
            constant_case.parameters["source_line"],
            reference_source_code=constant_case.source_code,
            source_class=constant_case.parameters["source_class"],
        )
    )
    assert constant_count == 1
    assert constant_metadata["status"] == "success"
    declaration_index = constant_code.index(
        f"private static final int {constant_case.parameters['constant_name']}"
    )
    account_index = constant_code.index(
        f"class {constant_case.parameters['source_class']}"
    )
    client_index = constant_code.index("class AccountClient")
    assert account_index < declaration_index < client_index

    dead_code_case = by_action["remove_dead_code"]
    dead_code, dead_count = java_transformers.apply_remove_dead_code(
        dead_code_case.source_code,
        dead_code_case.parameters["method"],
        dead_code_case.parameters["source_class"],
    )
    assert dead_count == 1
    assert dead_code_case.parameters["method"] not in dead_code


def test_java_rule_dead_code_case_survives_final_sctva_validation():
    case = next(
        item
        for item in generate_rdp_benchmark_cases(
            seed=23,
            include_negative=False,
            languages=["java"],
            implemented_rules_only=True,
        )
        if item.action_type == "remove_dead_code"
    )

    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": case.case_id,
        "language": "java",
        "source_files": [{
            "file_name": case.file_name,
            "language": "java",
            "source_code": case.source_code,
        }],
        "refactoring_plan": {
            "plan_id": case.case_id,
            "actions": [case.action_payload()],
            "behavior_tests": [],
            "metadata": {},
        },
        "execution_options": {
            "strict_mode": True,
            "enable_behavior_tests": False,
            "timeout_seconds": 1,
            "require_compilation": False,
            "enable_sctva_auto_refactoring": False,
            "enable_qwen": False,
            "qwen_mode": "fallback",
        },
    })

    assert result["success"] is True
    assert result["rollback_occurred"] is False
    assert result["accepted_output_engine"] == "RULE_BASED"
    assert result["validation"]["structural"]["details"][
        "dead_code_validation_status"
    ] == "PASS"


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
