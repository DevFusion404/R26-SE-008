from sctva.agent import SafeCodeTransformationValidationAgent
from sctva.transformers import java_transformers


def test_introduce_constant_rejects_numeric_html_css_string_target():
    source = (
        "public class T {\n"
        "    void render() {\n"
        "        System.out.println(\"<div class='my-5' style='width:50%'>\");\n"
        "    }\n"
        "}\n"
    )

    transformed, count, metadata = java_transformers.apply_introduce_constant(
        source,
        5,
        "THRESHOLD_LIMIT_5",
        3,
        reference_source_code=source,
    )

    assert count == 0
    assert transformed == source
    assert metadata["status"] == "not_applicable"
    assert metadata["final_decision"] == "NOT_APPLICABLE"
    assert metadata["reason"] == "TARGET_NOT_JAVA_NUMERIC_LITERAL"
    assert metadata["target_context"] == "STRING_LITERAL"


def test_introduce_constant_does_not_fall_back_from_string_line_to_other_code_literal():
    source = (
        "public class T {\n"
        "    int value() { return 5; }\n"
        "    void render() { System.out.println(\"my-5\"); }\n"
        "}\n"
    )

    transformed, count, metadata = java_transformers.apply_introduce_constant(
        source,
        5,
        "LIMIT",
        3,
        reference_source_code=source,
    )

    assert count == 0
    assert transformed == source
    assert metadata["reason"] == "TARGET_NOT_JAVA_NUMERIC_LITERAL"


def test_introduce_constant_can_extract_exact_targeted_java_string_when_enabled():
    source = (
        "public class T {\n"
        "    void render() {\n"
        "        System.out.println(\"<td colspan='6'>Books</td>\");\n"
        "    }\n"
        "}\n"
    )

    transformed, count, metadata = java_transformers.apply_introduce_constant(
        source,
        6,
        "THRESHOLD_LIMIT_6",
        3,
        reference_source_code=source,
        allow_string_literal_extraction=True,
    )

    constant_name = metadata["constant_name"]
    assert count == 1
    assert metadata["status"] == "success"
    assert metadata["reason"] == "introduce_string_constant_applied"
    assert metadata["target_context"] == "STRING_LITERAL"
    assert f"private static final String {constant_name}" in transformed
    assert f"System.out.println({constant_name});" in transformed
    assert "colspan='6'" in transformed


def test_introduce_constant_applies_only_to_real_java_numeric_literal():
    source = (
        "public class T {\n"
        "    boolean retry(int count) {\n"
        "        return count > 5;\n"
        "    }\n"
        "}\n"
    )

    transformed, count, metadata = java_transformers.apply_introduce_constant(
        source,
        5,
        "MAX_RETRY",
        3,
        reference_source_code=source,
    )

    assert count == 1
    assert metadata["status"] == "success"
    assert metadata["target_context"] == "JAVA_NUMERIC_LITERAL"
    assert "private static final int MAX_RETRY = 5;" in transformed
    assert "return count > MAX_RETRY;" in transformed


def test_introduce_constant_recovers_after_prior_constant_insertion_shifted_lines():
    source = (
        "public class T {\n"
        "    int first() { return 3; }\n"
        "    int second() { return 4; }\n"
        "}\n"
    )

    after_first, first_count, _ = java_transformers.apply_introduce_constant(
        source,
        3,
        "THREE",
        2,
        reference_source_code=source,
    )
    transformed, second_count, metadata = java_transformers.apply_introduce_constant(
        after_first,
        4,
        "FOUR",
        3,
        reference_source_code=source,
    )

    assert first_count == 1
    assert second_count == 1
    assert metadata["target_resolution"] == "current_numeric_literal_scan"
    assert "return FOUR;" in transformed


def test_duplicate_introduce_constant_action_is_safely_not_applicable():
    source = (
        "public class T {\n"
        "    int value() { return 5; }\n"
        "}\n"
    )

    transformed, first_count, _ = java_transformers.apply_introduce_constant(
        source,
        5,
        "FIVE",
        2,
        reference_source_code=source,
    )
    transformed_again, second_count, metadata = java_transformers.apply_introduce_constant(
        transformed,
        5,
        "FIVE",
        2,
        reference_source_code=source,
    )

    assert first_count == 1
    assert second_count == 0
    assert transformed_again == transformed
    assert metadata["status"] == "not_applicable"
    assert metadata["reason"] == "TARGET_ALREADY_REFACTORED_BY_PREVIOUS_ACTION"


def test_duplicate_java_introduce_constant_plan_is_reported_as_already_handled():
    source = (
        "public class T {\n"
        "    int value() { return 5; }\n"
        "}\n"
    )

    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "duplicate_java_introduce_constant",
        "language": "java",
        "source_files": [{
            "file_name": "T.java",
            "source_code": source,
            "language": "java",
            "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "duplicate_java_introduce_constant",
            "actions": [
                {
                    "action_type": "introduce_constant",
                    "parameters": {
                        "source_file": "T.java",
                        "literal_value": 5,
                        "constant_name": "FIVE",
                        "source_line": 2,
                    },
                },
                {
                    "action_type": "introduce_constant",
                    "parameters": {
                        "source_file": "T.java",
                        "literal_value": 5,
                        "constant_name": "FIVE",
                        "source_line": 2,
                    },
                },
            ],
            "behavior_tests": [],
            "metadata": {},
        },
        "execution_options": {
            "strict_mode": True,
            "enable_behavior_tests": True,
            "timeout_seconds": 10,
            "require_compilation": False,
            "enable_sctva_auto_refactoring": False,
        },
    })

    logs = result["safety_report"]["transformation_log"]
    assert logs[0]["metadata"]["final_decision"] == "ACCEPT"
    assert logs[1]["metadata"]["status"] == "already_handled"
    assert logs[1]["metadata"]["final_decision"] == "ALREADY_HANDLED"
    assert "return FIVE;" in result["refactored_code"]


def test_duplicate_java_string_constant_plan_is_reported_as_already_handled():
    source = (
        "public class T {\n"
        "    void render() {\n"
        "        System.out.println(\"<td colspan='6'>Books</td>\");\n"
        "    }\n"
        "}\n"
    )

    result = SafeCodeTransformationValidationAgent().execute({
        "request_id": "duplicate_java_string_constant",
        "language": "java",
        "source_files": [{
            "file_name": "T.java",
            "source_code": source,
            "language": "java",
            "source_mode": "raw",
        }],
        "refactoring_plan": {
            "plan_id": "duplicate_java_string_constant",
            "actions": [
                {
                    "action_type": "introduce_constant",
                    "parameters": {
                        "source_file": "T.java",
                        "literal_value": 6,
                        "constant_name": "THRESHOLD_LIMIT_6",
                        "source_line": 3,
                    },
                },
                {
                    "action_type": "introduce_constant",
                    "parameters": {
                        "source_file": "T.java",
                        "literal_value": 6,
                        "constant_name": "THRESHOLD_LIMIT_6",
                        "source_line": 3,
                    },
                },
            ],
            "behavior_tests": [],
            "metadata": {},
        },
        "execution_options": {
            "strict_mode": True,
            "enable_behavior_tests": True,
            "timeout_seconds": 10,
            "require_compilation": False,
            "enable_sctva_auto_refactoring": False,
        },
    })

    logs = result["safety_report"]["transformation_log"]
    assert logs[0]["metadata"]["reason"] == "introduce_string_constant_applied"
    assert logs[1]["metadata"]["status"] == "already_handled"
    assert logs[1]["metadata"]["final_decision"] == "ALREADY_HANDLED"
    assert "private static final String" in result["refactored_code"]



def test_introduce_constant_reuses_exact_requested_existing_constant_only():
    source = (
        "public class T {\n"
        "    private static final int FIVE = 5;\n"
        "    int value() { return 5; }\n"
        "}\n"
    )

    transformed, count, metadata = java_transformers.apply_introduce_constant(
        source,
        5,
        "FIVE",
        3,
        reference_source_code=source,
    )

    assert count == 1
    assert metadata["existing_requested_constant"] == "FIVE"
    assert metadata["reused_existing_constant"] is True
    assert "return FIVE;" in transformed
    assert transformed.count("static final int FIVE = 5") == 1


def test_introduce_constant_does_not_reuse_unrelated_same_value_constant():
    source = (
        "public class T {\n"
        "    private static final int HTTP_RETRY_LIMIT = 5;\n"
        "    int pageSize() { return 5; }\n"
        "}\n"
    )

    transformed, count, metadata = java_transformers.apply_introduce_constant(
        source,
        5,
        "PAGE_SIZE",
        3,
        reference_source_code=source,
    )

    assert count == 1
    assert metadata["reused_existing_constant"] is False
    assert "static final int PAGE_SIZE = 5" in transformed
    assert "return PAGE_SIZE;" in transformed
