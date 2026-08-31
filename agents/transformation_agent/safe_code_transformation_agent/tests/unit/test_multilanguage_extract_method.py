import asyncio

from sctva.integration.planner_adapter import PlannerAdapter
from sctva.transformers import c_extract_method
from sctva.transformers.c_extract_method import apply_extract_method as extract_c_method
from sctva.transformers.extract_method_common import StatementSpan
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
    assert metadata["outputs"] == []
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


def test_c_extract_method_collision_avoidance_and_compiler_verification():
    source = '''#include <stdio.h>

void helper(void) {
    printf("Existing helper\\n");
}

int calculate(int x, int y) {
    int a = x + 10;
    int b = y + 20;
    int c = a * b;
    int res = c + 5;
    return res;
}
'''
    transformed, count, metadata = extract_c_method(
        source,
        new_method_name="helper",
        method_name="calculate",
        start_line=8,
        end_line=11,
    )
    assert count == 1
    assert "static void helper_1(" in transformed
    assert "helper_1(" in transformed
    assert metadata["status"] == "success"
    assert SyntaxValidator().validate(
        language="c",
        source_code=transformed,
        require_compilation=True,
        timeout_seconds=10,
    ).passed is True


def test_c_extract_method_long_function_threshold_and_compiler_status(monkeypatch):
    # Construct a function with 45 lines
    lines = ["int calculate_long(int x) {", "    int total = x;"]
    for i in range(40):
        lines.append(f"    total += {i};")
    lines.append("    return total;")
    lines.append("}")
    source = "\n".join(lines)

    transformed, count, metadata = extract_c_method(
        source,
        new_method_name="process_chunk",
        method_name="calculate_long",
    )
    assert count == 1
    assert metadata["status"] == "success"
    assert metadata["validation"]["long_method_reduction"] == "PASS"
    assert metadata["validation"]["smell_reduction"] == "PASS"
    assert metadata["after_loc"] <= 40

    # Simulate C compiler being unavailable
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    _, _, unavail_meta = extract_c_method(
        source,
        new_method_name="process_chunk_alt",
        method_name="calculate_long",
    )
    assert unavail_meta["validation"]["compilation"] == "UNAVAILABLE"
    assert unavail_meta["validation"]["compilation"] != "PASS"


def test_c_extract_method_forwarding_existing_pointer_parameters():
    source = '''#include <stdio.h>

static void helper_one(int val, int *total_out) {
    int sub1 = val * 2;
    int sub2 = val * 3;
    int sub3 = val * 4;
    *total_out = sub1 + sub2 + sub3;
    printf("Total calculated\\n");
}

int calculate(int x) {
    int total = 0;
    helper_one(x, &total);
    return total;
}
'''
    transformed, count, metadata = extract_c_method(
        source,
        new_method_name="helper_inner",
        method_name="helper_one",
        start_line=4,
        end_line=7,
    )
    assert count == 1
    assert "static void helper_inner(" in transformed
    assert "int *total_out" in transformed
    assert "helper_inner(" in transformed
    assert "int (*total_out);" not in transformed
    assert "&(*total_out)" not in transformed
    assert metadata["status"] == "success"
    assert SyntaxValidator().validate(
        language="c",
        source_code=transformed,
        require_compilation=True,
        timeout_seconds=10,
    ).passed is True


def test_c_extract_method_no_redeclared_pointer_local_or_nested_address_of_deref():
    source = '''#include <stdio.h>

static void helper(int input, int *total_out) {
    int temp1 = input * 2;
    int temp2 = input * 3;
    int temp3 = input * 4;
    *total_out = temp1 + temp2 + temp3;
    printf("Result: %d\\n", *total_out);
}

int main(void) {
    int sum = 0;
    helper(10, &sum);
    return 0;
}
'''
    transformed, count, metadata = extract_c_method(
        source,
        new_method_name="another_helper",
        method_name="helper",
        start_line=4,
        end_line=7,
    )
    assert count == 1
    assert "int (*total_out);" not in transformed
    assert "&(*total_out)" not in transformed
    assert "another_helper(" in transformed
    assert "total_out" in transformed
    assert metadata["status"] == "success"
    assert SyntaxValidator().validate(
        language="c",
        source_code=transformed,
        require_compilation=True,
        timeout_seconds=10,
    ).passed is True


def test_c_extract_method_rewrites_scalar_output_address_uses_to_output_pointer():
    source = '''static void add_input(int input, int *total_out) {
    *total_out += input;
}

int process(int input) {
    int total = 0;
    int bonus = input + 1;
    total += bonus;
    add_input(input, &total);
    return total;
}
'''
    transformed, count, metadata = extract_c_method(
        source,
        new_method_name="process_total",
        method_name="process",
        start_line=7,
        end_line=9,
    )

    assert count == 1
    assert "static void process_total(int input, int *total_out)" in transformed
    assert "add_input(input, total_out);" in transformed
    assert "add_input(input, &(*total_out));" not in transformed
    assert "process_total(input, &total);" in transformed
    assert metadata["status"] == "success"
    assert SyntaxValidator().validate(
        language="c",
        source_code=transformed,
        require_compilation=True,
        timeout_seconds=10,
    ).passed is True


def test_c_extract_method_removes_uninitialized_output_declaration_from_helper():
    source = '''int process(int input) {
    int total;
    total = input;
    total += 1;
    return total;
}
'''
    transformed, count, metadata = extract_c_method(
        source,
        new_method_name="process_total",
        method_name="process",
        start_line=2,
        end_line=4,
    )

    assert count == 1
    assert "static void process_total(int input, int *total_out)" in transformed
    assert "int (*total_out);" not in transformed
    assert "int total;\n    process_total(input, &total);" in transformed
    assert metadata["status"] == "success"
    assert SyntaxValidator().validate(
        language="c",
        source_code=transformed,
        require_compilation=True,
        timeout_seconds=10,
    ).passed is True


def test_c_extract_method_preserves_initializer_assignment_operator_for_output():
    source = '''int process(int input) {
    int total = input;
    total += 1;
    total += input;
    int observed = total;
    return observed;
}
'''
    transformed, count, metadata = extract_c_method(
        source,
        new_method_name="process_total",
        method_name="process",
        start_line=2,
        end_line=4,
    )

    assert count == 1
    assert "(*total_out) = input;" in transformed
    assert "(*total_out)  input;" not in transformed
    assert "int total;\n    process_total(input, &total);" in transformed
    assert metadata["outputs"] == ["total"]
    assert metadata["status"] == "success"
    assert SyntaxValidator().validate(
        language="c",
        source_code=transformed,
        require_compilation=True,
        timeout_seconds=10,
    ).passed is True


def test_c_extract_method_forwards_all_comma_declared_file_handles_and_keeps_caller_scope():
    source = '''#include <stdio.h>

int write_files(int value) {
    FILE *fp, *fcp;
    int first = value + 1;
    int second = value + 2;
    fp = tmpfile();
    fcp = tmpfile();
    fprintf(fp, "%d", first);
    fprintf(fcp, "%d", second);
    fclose(fp);
    fclose(fcp);
    return first + second;
}
'''
    transformed, count, metadata = extract_c_method(
        source,
        new_method_name="write_file_values",
        method_name="write_files",
        start_line=7,
        end_line=11,
    )

    assert count == 1
    assert "static void write_file_values(" in transformed
    assert "FILE **fcp_out" in transformed
    assert "    FILE * fp;" in transformed
    assert "write_file_values(first, second, &fcp);" in transformed
    assert "fclose(fcp);" in transformed
    assert metadata["scope_validation"] == {
        "undefined_identifiers": [],
        "out_of_scope_identifiers": [],
        "missing_inputs": [],
        "missing_outputs": [],
    }
    assert SyntaxValidator().validate(
        language="c",
        source_code=transformed,
        require_compilation=True,
        timeout_seconds=10,
    ).passed is True


def test_c_extract_method_accepts_top_level_aggregate_state_and_sizeof_declaration():
    source = '''#include <stdio.h>

struct CustomerDetails {
    int room;
} s;

int update_record(int value) {
    FILE *f = tmpfile();
    long int size = sizeof(s);
    if (value > 0) {
        s.room = value;
        fseek(f, size, SEEK_CUR);
        fwrite(&s, sizeof(s), 1, f);
    }
    fclose(f);
    return s.room;
}
'''

    transformed, count, metadata = extract_c_method(
        source,
        new_method_name="write_customer_record",
        method_name="update_record",
        start_line=9,
        end_line=13,
    )

    assert count == 1
    assert "static void write_customer_record" in transformed
    # Nested extraction must forward the live file and size values instead of
    # relying on an unsafe implicit scope assumption.
    assert "write_customer_record(f, size, value);" in transformed
    assert metadata["scope_validation"] == {
        "undefined_identifiers": [],
        "out_of_scope_identifiers": [],
        "missing_inputs": [],
        "missing_outputs": [],
    }


def test_c_extract_method_allows_meaningful_reduction_without_full_smell_elimination():
    selected = [
        StatementSpan(start=0, end=1, text="line\n" * 7),
    ]

    assert c_extract_method._meaningfully_reduced(
        {"loc": 53, "complexity": 7},
        {"loc": 46, "complexity": 6},
        selected,
    ) is True
    assert c_extract_method._meaningfully_reduced(
        {"loc": 53, "complexity": 7},
        {"loc": 51, "complexity": 6},
        selected,
    ) is False
    assert c_extract_method._meaningfully_reduced(
        {"loc": 119, "complexity": 25},
        {"loc": 107, "complexity": 25},
        selected + [StatementSpan(start=1, end=2, text="line\n" * 5)],
    ) is True


def test_c_extract_method_uses_bounded_nested_block_instead_of_long_helper(monkeypatch):
    monkeypatch.setattr(
        c_extract_method,
        "_verify_c_compilation",
        lambda source: ("UNAVAILABLE", "test compiler unavailable"),
    )
    updates = "\n".join(f"        record.value += {value};" for value in range(45))
    source = f'''#include <stdio.h>

struct Record {{ int value; }};

void populate(FILE *fp) {{
    struct Record record;
    record.value = 0;
    int repeat = 0;
    while (repeat < 1) {{
{updates}
        fwrite(&record, sizeof(record), 1, fp);
        repeat++;
    }}
}}
'''

    transformed, count, metadata = extract_c_method(
        source,
        new_method_name="populate_record_values",
        method_name="populate",
    )

    assert count == 1
    assert metadata["status"] == "success"
    helper = c_extract_method._resolve_targets(transformed, "populate_record_values", "")[0]
    assert metadata["after_loc"] < metadata["before_loc"]
    assert c_extract_method._line_of(transformed, helper.end - 1) - c_extract_method._line_of(transformed, helper.start) + 1 <= 40
    assert "populate_record_values(" in transformed


def test_c_extract_method_rejects_undeclared_cross_boundary_file_handles():
    source = '''#include <stdio.h>

int write_files(int value) {
    fp = tmpfile();
    fcp = tmpfile();
    fprintf(fp, "%d", value);
    fprintf(fcp, "%d", value + 1);
    return value;
}
'''
    transformed, count, metadata = extract_c_method(
        source,
        new_method_name="write_file_values",
        method_name="write_files",
        start_line=4,
        end_line=7,
    )

    assert transformed == source
    assert count == 0
    assert metadata["status"] == "review_required"
    assert metadata["reason"] == "UNSAFE_C_EXTRACT_METHOD_DATA_FLOW"


def test_c_extract_method_returns_file_pointer_outputs_to_the_caller_by_address():
    source = '''#include <stdio.h>

int prepare_files(int value) {
    FILE *fp, *fcp;
    fp = tmpfile();
    fcp = tmpfile();
    value += 1;
    fclose(fp);
    fclose(fcp);
    return value;
}
'''
    transformed, count, metadata = extract_c_method(
        source,
        new_method_name="open_files",
        method_name="prepare_files",
        start_line=4,
        end_line=7,
    )

    assert count == 1
    assert "static void open_files(FILE **fcp_out, FILE **fp_out, int *value_out)" in transformed
    assert "open_files(&fcp, &fp, &value);" in transformed
    assert "fclose(fp);" in transformed
    assert "fclose(fcp);" in transformed
    assert metadata["outputs"] == ["fcp", "fp", "value"]
    assert metadata["scope_validation"] == {
        "undefined_identifiers": [],
        "out_of_scope_identifiers": [],
        "missing_inputs": [],
        "missing_outputs": [],
    }
    assert SyntaxValidator().validate(
        language="c",
        source_code=transformed,
        require_compilation=True,
        timeout_seconds=10,
    ).passed is True


def test_c_extract_method_forwards_all_three_comma_declared_handles():
    source = '''#include <stdio.h>

int load_files(int value) {
    FILE *f1, *f2, *f3;
    int first = value + 1;
    int second = value + 2;
    int third = value + 3;
    f1 = tmpfile();
    f2 = tmpfile();
    f3 = tmpfile();
    fprintf(f1, "%d", first);
    fprintf(f2, "%d", second);
    fprintf(f3, "%d", third);
    fclose(f1);
    fclose(f2);
    fclose(f3);
    return first + second + third;
}
'''
    transformed, count, metadata = extract_c_method(
        source,
        new_method_name="write_loaded_values",
        method_name="load_files",
        start_line=11,
        end_line=14,
    )

    assert count == 1
    helper_signature = next(
        line for line in transformed.splitlines()
        if line.startswith("static void write_loaded_values(")
    )
    assert "FILE * f1" in helper_signature
    assert "FILE * f2" in helper_signature
    assert "FILE * f3" in helper_signature
    assert "fclose(f2);" in transformed
    assert "fclose(f3);" in transformed
    assert metadata["scope_validation"] == {
        "undefined_identifiers": [],
        "out_of_scope_identifiers": [],
        "missing_inputs": [],
        "missing_outputs": [],
    }
    assert SyntaxValidator().validate(
        language="c",
        source_code=transformed,
        require_compilation=True,
        timeout_seconds=10,
    ).passed is True


def test_c_extract_method_keeps_proven_globals_shared_and_not_as_helper_parameters():
    source = '''static int global_total;

int update_total(int value) {
    int first = value + 1;
    global_total = first;
    global_total += value;
    int observed = global_total;
    return observed;
}
'''
    transformed, count, metadata = extract_c_method(
        source,
        new_method_name="update_global_total",
        method_name="update_total",
        start_line=4,
        end_line=6,
    )

    assert count == 1
    helper_signature = next(
        line for line in transformed.splitlines()
        if line.startswith("static void update_global_total(")
    )
    assert "global_total," not in helper_signature
    assert " global_total)" not in helper_signature
    assert "update_global_total(" in transformed
    assert metadata["scope_validation"]["undefined_identifiers"] == []
    assert SyntaxValidator().validate(
        language="c",
        source_code=transformed,
        require_compilation=True,
        timeout_seconds=10,
    ).passed is True


def test_c_extract_method_scope_validation_compares_caller_before_and_after(monkeypatch):
    monkeypatch.setattr(
        c_extract_method,
        "_verify_c_compilation",
        lambda source: ("UNAVAILABLE", "test compiler unavailable"),
    )
    source = '''void receive_callback(void *socket_id) {
    (void) socket_id;
}

int connect_client(void) {
    int socket_id = socket(AF_INET, SOCK_STREAM, 0);
    int first = socket_id + 1;
    int second = first + 1;
    register_callback(receive_callback, &socket_id);
    log_value(first);
    log_value(second);
    return socket_id;
}
'''

    transformed, count, metadata = extract_c_method(
        source,
        new_method_name="register_client_callback",
        method_name="connect_client",
        start_line=8,
        end_line=11,
    )

    assert count == 1
    assert "register_client_callback" in transformed
    assert metadata["scope_validation"] == {
        "undefined_identifiers": [],
        "out_of_scope_identifiers": [],
        "missing_inputs": [],
        "missing_outputs": [],
    }


def test_c_extract_method_detects_for_initializer_variables_as_locals():
    declarations = c_extract_method._c_local_declarations(
        "for (int i = 0; i < count; ++i) { total += i; }"
    )

    assert declarations["i"] == "int {name}"
