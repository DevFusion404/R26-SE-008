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
