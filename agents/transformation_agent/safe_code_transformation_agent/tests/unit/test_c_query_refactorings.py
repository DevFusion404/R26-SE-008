from sctva.transformers.c_query_refactorings import (
    apply_replace_parameter_with_method_call,
    apply_replace_temp_with_query,
)
from sctva.contracts import RefactoringAction
from sctva.model_assistance.qwen import _c_context


def test_replace_temp_with_query_extracts_only_a_pure_stable_expression():
    source = """static int square_sum(int left, int right) {
    int total = left * left + right * right;
    return total + total;
}
"""

    transformed, replacements, metadata = apply_replace_temp_with_query(
        source,
        method_name="square_sum",
    )

    assert replacements == 3
    assert metadata["status"] == "success"
    assert "static int query_square_sum_total(int left, int right)" in transformed
    assert "return query_square_sum_total(left, right) + query_square_sum_total(left, right);" in transformed


def test_replace_temp_with_query_refuses_a_mutated_dependency():
    source = """static int adjust(int value) {
    int total = value + 1;
    value++;
    return total;
}
"""

    transformed, replacements, metadata = apply_replace_temp_with_query(
        source,
        method_name="adjust",
    )

    assert transformed == source
    assert replacements == 0
    assert metadata["reason"] == "TEMPORARY_QUERY_DEPENDENCY_MUTATED"


def test_replace_parameter_with_method_call_updates_static_local_call_sites():
    source = """static int capped_value(const struct Config *config, int limit) {
    return limit + 1;
}

int run(const struct Config *config) {
    return capped_value(config, config->limit);
}
"""

    transformed, replacements, metadata = apply_replace_parameter_with_method_call(
        source,
        method_name="capped_value",
        parameter_name="limit",
        query_expression="config->limit",
        query_parameters=["config"],
    )

    assert replacements == 3
    assert metadata["status"] == "success"
    assert "static int capped_value(const struct Config *config)" in transformed
    assert "return capped_value(config);" in transformed
    assert "return query_capped_value_limit(config) + 1;" in transformed


def test_replace_parameter_with_method_call_requires_explicit_rdp_query_metadata():
    source = """static int capped_value(int limit) {
    return limit + 1;
}
"""

    transformed, replacements, metadata = apply_replace_parameter_with_method_call(
        source,
        method_name="capped_value",
    )

    assert transformed == source
    assert replacements == 0
    assert metadata["reason"] == "PARAMETER_OR_QUERY_EXPRESSION_MISSING_FROM_PLAN"


def test_qwen_uses_translation_unit_context_for_c_query_refactorings():
    source = """static int square_sum(int left, int right) {
    int total = left * left + right * right;
    return total + total;
}
"""

    context = _c_context(
        source,
        RefactoringAction(
            action_type="replace_temp_with_query",
            parameters={"method": "square_sum"},
        ),
    )

    assert context is not None
    assert context.kind == "translation_unit"
    assert context.source == source
