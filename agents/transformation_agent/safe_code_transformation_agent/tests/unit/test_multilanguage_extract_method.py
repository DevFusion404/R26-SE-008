import asyncio

from sctva.integration.planner_adapter import PlannerAdapter
from sctva.transformers.c_extract_method import apply_extract_method as extract_c_method
from sctva.transformers.java_extract_method import apply_extract_method as extract_java_method
from sctva.transformers.python_extract_method import apply_extract_method as extract_python_method
from sctva.validators.syntax_validator import SyntaxValidator


PYTHON_FUNCTION = '''def process(a: int, b: int):
    subtotal = a + b
    discount = subtotal * 0.1
    tax = subtotal * 0.2
    total = subtotal - discount + tax
    observed = round(total, 2)
    return observed
'''


JAVA_METHOD = '''public class Calculator {
    public static int process(int a, int b) throws Exception {
        int subtotal = a + b;
        int checked = validate(subtotal);
        int tax = checked / 5;
        int total = checked + tax;
        System.out.println(total);
        return total;
    }

    private static int validate(int value) throws Exception {
        return value;
    }
}
'''


C_FUNCTION = '''struct Totals {
    int first;
    int second;
};

int process(int value) {
    int first = value + 1;
    int second = value + 2;
    first += value;
    second += value;
    int observed = first + second;
    return observed;
}
'''


def _call_python(source: str, name: str, *args):
    namespace = {}
    exec(source, namespace)
    return namespace[name](*args)


def test_python_extracts_real_module_sibling_and_preserves_behavior():
    transformed, count, metadata = extract_python_method(
        PYTHON_FUNCTION,
        new_method_name="calculate_total",
        method_name="process",
        start_line=999,
        end_line=1001,
    )

    assert count == 1
    assert "\ndef calculate_total(" in transformed
    assert "    def calculate_total(" not in transformed
    assert _call_python(PYTHON_FUNCTION, "process", 10, 5) == _call_python(
        transformed, "process", 10, 5
    )
    assert metadata["inputs"]
    assert metadata["outputs"]
    assert metadata["validation"]["long_method_reduction"] == "PASS"
    assert metadata["after_loc"] < metadata["before_loc"]


def test_python_class_extraction_preserves_self_state_and_public_behavior():
    source = '''class Account:
    def __init__(self):
        self.total = 0

    def process(self, values):
        subtotal = sum(values)
        fee = subtotal * 0.1
        tax = subtotal * 0.2
        self.total = subtotal + fee + tax
        observed = round(self.total, 2)
        return observed
'''
    transformed, count, metadata = extract_python_method(
        source,
        new_method_name="_calculate_total",
        method_name="process",
        source_class="Account",
    )

    original_ns = {}
    transformed_ns = {}
    exec(source, original_ns)
    exec(transformed, transformed_ns)
    original = original_ns["Account"]()
    changed = transformed_ns["Account"]()
    assert count == 1
    assert "    def _calculate_total(self" in transformed
    assert original.process([2, 3, 5]) == changed.process([2, 3, 5])
    assert original.total == changed.total
    assert metadata["validation"]["data_flow"] == "PASS"


def test_python_async_extraction_creates_awaited_sibling():
    source = '''class AsyncCalculator:
    async def process(self, value):
        first = value + 1
        await asyncio.sleep(0)
        second = first * 2
        third = second + 3
        observed = third - 1
        return observed
'''
    transformed, count, _ = extract_python_method(
        source,
        new_method_name="_calculate_async",
        method_name="process",
        source_class="AsyncCalculator",
    )
    namespace = {"asyncio": asyncio}
    exec(transformed, namespace)

    assert count == 1
    assert "async def _calculate_async(self" in transformed
    assert "await self._calculate_async(" in transformed
    assert asyncio.run(namespace["AsyncCalculator"]().process(4)) == 12


def test_python_unsafe_early_return_is_review_required():
    source = '''def process(value):
    current = value + 1
    if current < 0:
        return 0
    return current
'''
    transformed, count, metadata = extract_python_method(
        source,
        new_method_name="calculate",
        method_name="process",
    )
    assert transformed == source
    assert count == 0
    assert metadata["status"] == "review_required"
    assert metadata["reason"] == "NO_SAFE_COHESIVE_BLOCK"


def test_python_exception_type_and_result_are_preserved():
    source = '''def process(value):
    adjusted = value + 1
    if adjusted < 0:
        raise ValueError("negative")
    doubled = adjusted * 2
    total = doubled + 3
    observed = total
    return observed
'''
    transformed, count, _ = extract_python_method(
        source,
        new_method_name="calculate",
        method_name="process",
    )
    assert count == 1
    assert _call_python(source, "process", 2) == _call_python(transformed, "process", 2)
    for candidate in (source, transformed):
        try:
            _call_python(candidate, "process", -5)
        except Exception as exc:
            assert type(exc) is ValueError
        else:
            raise AssertionError("ValueError was not preserved")


def test_python_too_many_parameters_is_not_introduced():
    source = '''def process(a, b, c, d, e, f, g):
    one = a + b + c + d + e + f + g
    two = one + a + b + c + d + e + f + g
    three = two + a + b + c + d + e + f + g
    total = three + a + b + c + d + e + f + g
    observed = total
    return observed
'''
    transformed, count, metadata = extract_python_method(
        source,
        new_method_name="calculate",
        method_name="process",
        start_line=2,
        end_line=5,
    )
    assert transformed == source
    assert count == 0
    assert metadata["status"] == "review_required"


def test_java_static_checked_exception_extraction_is_typed_and_valid():
    transformed, count, metadata = extract_java_method(
        JAVA_METHOD,
        new_method_name="calculateTotal",
        method_name="process",
        source_class="Calculator",
    )

    assert count == 1
    assert "private static int calculateTotal(" in transformed
    assert "throws Exception" in transformed
    assert metadata["validation"]["data_flow"] == "PASS"
    assert SyntaxValidator().validate(
        language="java",
        source_code=transformed,
        require_compilation=False,
        timeout_seconds=5,
    ).passed is True


def test_java_overload_requires_signature_and_stale_lines_are_ignored():
    source = JAVA_METHOD.replace(
        "\n    private static int validate",
        "\n    public static int process(String value) { return value.length(); }\n\n    private static int validate",
    )
    unchanged, count, metadata = extract_java_method(
        source,
        new_method_name="calculateTotal",
        method_name="process",
        source_class="Calculator",
    )
    transformed, resolved_count, resolved_metadata = extract_java_method(
        source,
        new_method_name="calculateTotal",
        method_name="process",
        source_class="Calculator",
        method_signature="process(int,int)",
        start_line=500,
        end_line=510,
    )

    assert unchanged == source
    assert count == 0
    assert metadata["reason"] == "AMBIGUOUS_OVERLOADED_METHOD_TARGET"
    assert resolved_count == 1
    assert resolved_metadata["resolved_source_range"]["start_line"] < 500
    assert "private static int calculateTotal(" in transformed


def test_java_early_return_cross_boundary_is_review_required():
    source = '''public class Calculator {
    int process(int value) {
        int current = value + 1;
        if (current < 0) {
            return 0;
        }
        int observed = current + 1;
        return observed;
    }
}
'''
    transformed, count, metadata = extract_java_method(
        source,
        new_method_name="calculate",
        method_name="process",
        source_class="Calculator",
    )
    assert transformed == source
    assert count == 0
    assert metadata["status"] == "review_required"


def test_c_multiple_outputs_use_pointer_parameters_and_compile():
    transformed, count, metadata = extract_c_method(
        C_FUNCTION,
        new_method_name="calculate_values",
        method_name="process",
        start_line=6,
        end_line=9,
    )

    assert count == 1
    assert "int *first_out" in transformed
    assert "int *second_out" in transformed
    assert "calculate_values(value, &first, &second);" in transformed
    assert metadata["outputs"] == ["first", "second"]
    assert SyntaxValidator().validate(
        language="c",
        source_code=transformed,
        require_compilation=True,
        timeout_seconds=10,
    ).passed is True


def test_c_arrays_and_struct_pointers_keep_memory_ownership():
    source = '''struct Totals { int value; };

int process(int values[], int count, struct Totals *totals) {
    int index = 0;
    totals->value = 0;
    for (index = 0; index < count; index++) {
        totals->value += values[index];
    }
    totals->value += count;
    int observed = totals->value;
    return observed;
}
'''
    transformed, count, metadata = extract_c_method(
        source,
        new_method_name="accumulate_values",
        method_name="process",
        start_line=4,
        end_line=9,
    )

    assert count == 1
    assert "int values[]" in transformed
    assert "struct Totals *totals" in transformed
    assert "free(" not in transformed
    assert "malloc(" not in transformed
    assert metadata["outputs"] == ["observed"]
    assert SyntaxValidator().validate(
        language="c",
        source_code=transformed,
        require_compilation=True,
        timeout_seconds=10,
    ).passed is True


def test_c_goto_and_early_return_are_review_required():
    source = '''int process(int value) {
    int current = value + 1;
    if (current < 0) goto failed;
    int observed = current + 1;
    return observed;
failed:
    return 0;
}
'''
    transformed, count, metadata = extract_c_method(
        source,
        new_method_name="calculate",
        method_name="process",
    )
    assert transformed == source
    assert count == 0
    assert metadata["status"] == "review_required"


def test_c_pointer_mutation_remains_shared_with_the_caller():
    source = '''int process(int *value) {
    int base = *value;
    base += 1;
    *value = base;
    int doubled = base * 2;
    int observed = doubled + 3;
    return observed;
}
'''
    transformed, count, metadata = extract_c_method(
        source,
        new_method_name="update_value",
        method_name="process",
        start_line=2,
        end_line=5,
    )
    assert count == 1
    assert "int *value" in transformed
    assert "malloc(" not in transformed and "free(" not in transformed
    assert "value" in metadata["inputs"]
    assert SyntaxValidator().validate(
        language="c",
        source_code=transformed,
        require_compilation=True,
        timeout_seconds=10,
    ).passed is True


def test_invalid_semantic_target_returns_review_without_changes():
    transformed, count, metadata = extract_python_method(
        PYTHON_FUNCTION,
        new_method_name="calculate_total",
        method_name="missing_process",
        start_line=2,
        end_line=5,
    )
    assert transformed == PYTHON_FUNCTION
    assert count == 0
    assert metadata["reason"] == "METHOD_TARGET_NOT_FOUND"


def test_extract_method_is_idempotent_for_all_languages():
    python_first, python_count, _ = extract_python_method(
        PYTHON_FUNCTION,
        new_method_name="calculate_total",
        method_name="process",
    )
    python_second, python_second_count, python_metadata = extract_python_method(
        python_first,
        new_method_name="calculate_total",
        method_name="process",
    )
    java_first, java_count, _ = extract_java_method(
        JAVA_METHOD,
        new_method_name="calculateTotal",
        method_name="process",
        source_class="Calculator",
    )
    java_second, java_second_count, java_metadata = extract_java_method(
        java_first,
        new_method_name="calculateTotal",
        method_name="process",
        source_class="Calculator",
    )
    c_first, c_count, _ = extract_c_method(
        C_FUNCTION,
        new_method_name="calculate_values",
        method_name="process",
    )
    c_second, c_second_count, c_metadata = extract_c_method(
        c_first,
        new_method_name="calculate_values",
        method_name="process",
    )

    assert (python_count, java_count, c_count) == (1, 1, 1)
    assert python_second == python_first and python_second_count == 0
    assert java_second == java_first and java_second_count == 0
    assert c_second == c_first and c_second_count == 0
    assert python_metadata["status"] == "already_applied"
    assert java_metadata["status"] == "already_applied"
    assert c_metadata["status"] == "already_applied"


def test_planner_accepts_semantic_extract_method_without_line_numbers():
    normalized = PlannerAdapter().normalize_plan({
        "plan_id": "semantic_extract",
        "steps": [{
            "step_id": "step-1",
            "smell": "Long Method",
            "refactoring": "Extract Method",
            "target": {
                "file": "src/Calculator.java",
                "class": "Calculator",
                "method": "process",
                "signature": "process(int,int)",
            },
            "parameters": {"new_method_name": "calculateTotal"},
        }],
    })
    parameters = normalized["actions"][0]["parameters"]
    assert parameters["source_file"] == "src/Calculator.java"
    assert parameters["method"] == "process"
    assert parameters["method_signature"] == "process(int,int)"
    assert parameters["start_line"] is None
    assert parameters["end_line"] is None
