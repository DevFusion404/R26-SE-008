import ast

from sctva.contracts import RefactoringAction
from sctva.transformers.python_extract_method import apply_extract_method
from sctva.validators.structural_validator import StructuralValidator


def _call(source: str, name: str, *args):
    namespace: dict[str, object] = {}
    exec(source, namespace)
    return namespace[name](*args)


def _action(method: str, helper: str) -> RefactoringAction:
    return RefactoringAction(
        action_type="extract_method",
        parameters={"method": method, "new_method_name": helper},
    )


def test_normal_long_function_moves_real_logic_into_requested_helper():
    source = '''def calculate(a, b):
    prefix = a + 1
    subtotal = prefix + b
    fee = subtotal * 0.1
    total = subtotal + fee
    rounded = round(total, 2)
    return rounded
'''
    transformed, count, metadata = apply_extract_method(
        source,
        method_name="calculate",
        new_method_name="extracted_calculate",
        start_line=3,
        end_line=5,
    )

    assert count == 1
    assert "def extracted_calculate(" in transformed
    assert "total = extracted_calculate(b, prefix)" in transformed
    assert metadata["before_loc"] > metadata["after_loc"]
    assert _call(source, "calculate", 4, 6) == _call(transformed, "calculate", 4, 6)


def test_extract_method_recovers_a_module_function_when_rdp_sends_a_filename_as_class():
    source = '''def process_student_results(mark):
    label = "F"
    if mark >= 75:
        label = "A"
    elif mark >= 65:
        label = "B"
    return label
'''
    transformed, count, metadata = apply_extract_method(
        source,
        method_name="process_student_results",
        new_method_name="extracted_process_student_results",
        source_class="01_long_method_student_marks",
        start_line=3,
        end_line=6,
    )

    assert count == 1
    assert metadata["source_class_resolution"] == "stale_module_class_ignored"
    assert "def extracted_process_student_results(" in transformed
    assert _call(source, "process_student_results", 80) == _call(
        transformed, "process_student_results", 80
    )


def test_extract_method_structural_validation_accepts_later_constant_introduction():
    source = '''def calculate_grade(mark):
    label = "F"
    if mark >= 75:
        label = "A"
    elif mark >= 65:
        label = "B"
    return label
'''
    transformed, count, _ = apply_extract_method(
        source,
        method_name="calculate_grade",
        new_method_name="calculate_grade_label",
        start_line=3,
        end_line=6,
    )
    transformed = (
        "CONSTANT_75 = 75\n\n"
        + transformed.replace("mark >= 75", "mark >= CONSTANT_75")
    )
    action = RefactoringAction(
        action_type="extract_method",
        parameters={
            "method": "calculate_grade",
            "new_method_name": "calculate_grade_label",
            "source_class": "01_long_method_student_marks",
        },
    )

    result = StructuralValidator().validate(
        language="python",
        original_code=source,
        transformed_code=transformed,
        actions=[action],
    )

    assert count == 1
    assert result.passed is True
    assert result.details["extract_method_validation"][0]["checks"][
        "helper_contains_real_moved_logic"
    ] is True


def test_extracted_function_receives_required_parameters_and_returns_one_value():
    source = '''def calculate(a, b):
    prefix = a + 1
    subtotal = prefix + b
    fee = subtotal * 0.1
    total = subtotal + fee
    return total
'''
    transformed, count, _ = apply_extract_method(
        source,
        method_name="calculate",
        new_method_name="calculate_total",
        start_line=3,
        end_line=5,
    )

    helper = next(
        node for node in ast.parse(transformed).body
        if isinstance(node, ast.FunctionDef) and node.name == "calculate_total"
    )
    assert count == 1
    assert {argument.arg for argument in helper.args.args} == {"prefix", "b"}
    assert any(isinstance(node, ast.Return) and node.value is not None for node in ast.walk(helper))
    assert _call(source, "calculate", 2, 5) == _call(transformed, "calculate", 2, 5)


def test_parameter_only_extraction_keeps_the_original_call_contract():
    source = '''def format_score(score, prefix):
    normalized = max(0, min(100, score))
    text = f"{prefix}: {normalized}"
    decorated = f"[{text}]"
    return decorated
'''
    transformed, count, _ = apply_extract_method(
        source,
        method_name="format_score",
        new_method_name="build_score_text",
        start_line=2,
        end_line=4,
    )

    assert count == 1
    assert _call(source, "format_score", 110, "Result") == _call(
        transformed, "format_score", 110, "Result"
    )


def test_extracted_function_returns_multiple_values_when_both_are_used_afterward():
    source = '''def calculate(a, b):
    base = a + b
    first = base * 2
    second = base * 3
    bonus = base + 1
    result = first + second
    return result
'''
    transformed, count, metadata = apply_extract_method(
        source,
        method_name="calculate",
        new_method_name="calculate_parts",
        start_line=3,
        end_line=5,
    )

    assert count == 1
    assert "first, second = calculate_parts(base)" in transformed
    assert metadata["outputs"] == ["first", "second"]
    assert _call(source, "calculate", 3, 4) == _call(transformed, "calculate", 3, 4)


def test_nested_conditional_logic_is_moved_without_changing_result():
    source = '''def grade(mark):
    label = "F"
    if mark >= 75:
        if mark >= 90:
            label = "A+"
        else:
            label = "A"
    summary = f"Grade: {label}"
    return summary
'''
    transformed, count, _ = apply_extract_method(
        source,
        method_name="grade",
        new_method_name="resolve_grade",
        start_line=3,
        end_line=7,
    )

    assert count == 1
    assert "def resolve_grade(mark):" in transformed
    assert _call(source, "grade", 95) == _call(transformed, "grade", 95)
    assert _call(source, "grade", 80) == _call(transformed, "grade", 80)


def test_loop_logic_and_local_variable_dependencies_are_moved_safely():
    source = '''def summarize(values):
    total = 0
    count = 0
    for value in values:
        if value > 0:
            total += value
            count += 1
    average = total / count if count else 0
    return average
'''
    transformed, count, _ = apply_extract_method(
        source,
        method_name="summarize",
        new_method_name="accumulate_positive_values",
        start_line=4,
        end_line=7,
    )

    assert count == 1
    assert {"values", "total", "count"} <= {
        argument.arg
        for node in ast.parse(transformed).body
        if isinstance(node, ast.FunctionDef) and node.name == "accumulate_positive_values"
        for argument in node.args.args
    }
    assert _call(source, "summarize", [-1, 2, 4, 0]) == _call(
        transformed, "summarize", [-1, 2, 4, 0]
    )


def test_local_values_produced_by_helper_are_returned_to_the_original_method():
    source = '''def build_message(name):
    greeting = "Hello"
    title = name.upper()
    message = f"{greeting}, {title}"
    result = message + "!"
    return result
'''
    transformed, count, metadata = apply_extract_method(
        source,
        method_name="build_message",
        new_method_name="compose_message",
        start_line=3,
        end_line=5,
    )

    assert count == 1
    assert metadata["outputs"] == ["result"]
    assert _call(source, "build_message", "Ada") == _call(transformed, "build_message", "Ada")


def test_output_equivalence_holds_for_multiple_observable_inputs():
    source = '''def classify(score):
    adjusted = score + 5
    threshold = 75
    if adjusted >= threshold:
        label = "pass"
    else:
        label = "retry"
    return label
'''
    transformed, count, _ = apply_extract_method(
        source,
        method_name="classify",
        new_method_name="resolve_label",
        start_line=4,
        end_line=7,
    )

    assert count == 1
    for score in (0, 69, 70, 95):
        assert _call(source, "classify", score) == _call(transformed, "classify", score)


def test_unsafe_return_control_flow_is_review_required():
    source = '''def process(value):
    prepared = value + 1
    if prepared < 0:
        return 0
    doubled = prepared * 2
    return doubled
'''
    transformed, count, metadata = apply_extract_method(
        source,
        method_name="process",
        new_method_name="process_core",
    )

    assert transformed == source
    assert count == 0
    assert metadata["status"] == "review_required"


def test_structural_validation_rejects_empty_helper_without_moved_logic():
    original = '''def process(value):
    first = value + 1
    second = first * 2
    result = second + 3
    return result
'''
    fake = '''def process(value):
    first = value + 1
    second = first * 2
    result = second + 3
    return result

def extracted_process(value):
    pass
'''

    result = StructuralValidator().validate(
        language="python", original_code=original, transformed_code=fake,
        actions=[_action("process", "extracted_process")],
    )

    assert result.passed is False
    assert result.details["extract_method_validation"][0]["checks"]["helper_contains_real_moved_logic"] is False


def test_structural_validation_rejects_logic_duplicated_in_original_and_helper():
    original = '''def process(value):
    first = value + 1
    second = first * 2
    result = second + 3
    return result
'''
    duplicated = '''def process(value):
    first = value + 1
    second = first * 2
    result = extracted_process(first)
    return result

def extracted_process(first):
    second = first * 2
    result = second + 3
    return result
'''

    result = StructuralValidator().validate(
        language="python", original_code=original, transformed_code=duplicated,
        actions=[_action("process", "extracted_process")],
    )

    assert result.passed is False
    checks = result.details["extract_method_validation"][0]["checks"]
    assert checks["no_logic_duplicated"] is False


def test_structural_validation_rejects_helper_when_original_method_is_not_reduced():
    original = '''def process(value):
    first = value + 1
    second = first * 2
    result = second + 3
    return result
'''
    wrapper = '''def process(value):
    first = value + 1
    second = first * 2
    result = second + 3
    extracted_process(first)
    return result

def extracted_process(first):
    return first * 2
'''

    result = StructuralValidator().validate(
        language="python", original_code=original, transformed_code=wrapper,
        actions=[_action("process", "extracted_process")],
    )

    assert result.passed is False
    assert result.details["extract_method_validation"][0]["checks"]["original_method_loc_reduced"] is False
