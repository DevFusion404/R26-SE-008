from agents.transformation_agent.safe_code_transformation_agent.sctva.transformers import python_transformers
from sctva.transformers import c_transformers
from sctva.transformers import java_transformers


def test_python_extract_constant_replaces_literals():
    source = "def value():\n    return 10\n"
    transformed, count = python_transformers.apply_extract_constant(source, 10, "BASE")
    assert count >= 1
    assert "BASE" in transformed


def test_python_extract_constant_falls_back_when_source_line_misses():
    source = "def value():\n    return 10\n"
    transformed, count = python_transformers.apply_extract_constant(source, 10, "BASE", source_line=99)
    assert count >= 1
    assert "BASE" in transformed


def test_python_rename_symbol_changes_function_name():
    source = "def calc(x):\n    return x + 1\n"
    transformed, count = python_transformers.apply_rename_symbol(source, "calc", "calculate")
    assert count >= 1
    assert "def calculate" in transformed


def test_python_extract_method_creates_nested_helper_for_return_block():
    source = "def total(value):\n    tax = value * 0.1\n    return value + tax\n"
    transformed, count = python_transformers.apply_extract_method(source, "total_core", 3, 3)
    assert count == 1
    assert "def total_core()" in transformed
    assert "return total_core()" in transformed


def test_python_extract_method_handles_full_function_range():
    source = "def total(value):\n    tax = value * 0.1\n    return value + tax\n"
    transformed, count = python_transformers.apply_extract_method(source, "extracted_total", 1, 3)
    assert count == 1
    assert "def extracted_total()" in transformed
    assert "return extracted_total()" in transformed


def test_python_remove_dead_code_rejects_live_assertion():
    source = "def test_value():\n    assert 1 == 1\n"
    transformed, count = python_transformers.apply_remove_dead_code(source, "", source_line=2)
    assert count == 0
    assert transformed == source


def test_python_remove_dead_code_removes_unused_literal_local():
    source = "def value():\n    unused = 10\n    return 1\n"
    transformed, count = python_transformers.apply_remove_dead_code(source, "", source_line=2)
    assert count == 1
    assert "unused" not in transformed


def test_python_remove_dead_code_removes_unreachable_statement():
    source = "def value():\n    return 1\n    print('unreachable')\n"
    transformed, count = python_transformers.apply_remove_dead_code(source, "", source_line=3)
    assert count == 1
    assert "unreachable" not in transformed


def test_java_replace_literal_changes_value():
    source = "public class T { int x() { return 5; } }"
    transformed, count = java_transformers.apply_replace_literal(source, 5, 7)
    assert count == 1
    assert "return 7" in transformed


def test_java_extract_constant_falls_back_when_source_line_misses():
    source = "public class T { int x() { return 5; } }"
    transformed, count = java_transformers.apply_extract_constant(source, 5, "BASE", source_line=99)
    assert count == 1
    assert "BASE" in transformed


def test_java_normalize_multiline_statement_extracts_sql_constant():
    source = (
        "import java.sql.PreparedStatement;\n"
        "class T {\n"
        "    void save(java.sql.Connection connection) throws Exception {\n"
        "        PreparedStatement pst = connection.prepareStatement(\"insert into t \" +\n"
        "                \"values (?)\");\n"
        "    }\n"
        "}\n"
    )
    transformed, count = java_transformers.apply_normalize_multiline_statement(
        source,
        source_line=4,
        constant_name="SCTVA_INSERT_SQL",
        normalization="java_prepare_statement_sql",
    )
    assert count == 1
    assert "private static final String SCTVA_INSERT_SQL" in transformed
    assert "prepareStatement(SCTVA_INSERT_SQL)" in transformed


def test_java_rename_symbol_replaces_word_boundary_matches():
    source = "public class T { int processPayment(){ return 1; } }"
    transformed, count = java_transformers.apply_rename_symbol(source, "processPayment", "processPaymentPolymorphic")
    assert count == 1
    assert "processPaymentPolymorphic" in transformed


def test_java_rename_symbol_ignores_strings_and_comments():
    source = 'public class T { String s = "processPayment"; // processPayment\n int processPayment(){ return 1; } }'
    transformed, count = java_transformers.apply_rename_symbol(source, "processPayment", "pay")
    assert count == 1
    assert '"processPayment"' in transformed
    assert "// processPayment" in transformed
    assert "int pay()" in transformed


def test_java_extract_method_passes_referenced_local_variables():
    source = "public class T {\n    int value(int input) {\n        int total = input + 1;\n        return total;\n    }\n}\n"
    transformed, count = java_transformers.apply_extract_method(source, "valueCore", 4, 4)
    assert count == 1
    assert "return valueCore(total);" in transformed
    assert "private int valueCore(int total)" in transformed


def test_java_extract_method_handles_full_method_range():
    source = "public class T {\n    int value(int input) {\n        int total = input + 1;\n        return total;\n    }\n}\n"
    transformed, count = java_transformers.apply_extract_method(source, "extractedValue", 2, 5)
    assert count == 1
    assert "return extractedValue(input);" in transformed
    assert "private int extractedValue(int input)" in transformed


def test_java_extract_method_uses_method_name_when_prior_edits_shift_lines():
    source = (
        "public class T {\n"
        "    int first(int a) {\n"
        "        int x = a + 1;\n"
        "        return x;\n"
        "    }\n"
        "\n"
        "    int second(int b) {\n"
        "        int y = b + 2;\n"
        "        return y;\n"
        "    }\n"
        "}\n"
    )
    shifted, first_count = java_transformers.apply_extract_method(
        source,
        "extractedFirst",
        2,
        5,
        method_name="first",
    )
    transformed, second_count = java_transformers.apply_extract_method(
        shifted,
        "extractedSecond",
        7,
        10,
        method_name="second",
    )
    assert first_count == 1
    assert second_count == 1
    assert "return extractedSecond(b);" in transformed
    assert "private int extractedSecond(int b)" in transformed


def test_java_fault_injection_replaces_return_logic():
    source = "public class T { double x() { double total = 1.0; return total; } }"
    transformed, count = java_transformers.apply_fault_injection(source, "return total;", "return total + 1;")
    assert count == 1
    assert "return total + 1;" in transformed


def test_python_fault_injection_replaces_return_logic():
    source = "def x(total):\n    return total\n"
    transformed, count = python_transformers.apply_fault_injection(source, "return total", "return total + 1")
    assert count == 1
    assert "return total + 1" in transformed


def test_c_rename_symbol_changes_function_name():
    source = "int calc(int value) { return value + 1; }\n"
    transformed, count = c_transformers.apply_rename_symbol(source, "calc", "calculate")
    assert count >= 1
    assert "calculate" in transformed


def test_c_rename_symbol_ignores_strings_and_comments():
    source = 'const char *s = "calc"; // calc\nint calc(int value) { return value + 1; }\n'
    transformed, count = c_transformers.apply_rename_symbol(source, "calc", "calculate")
    assert count == 1
    assert '"calc"' in transformed
    assert "// calc" in transformed
    assert "int calculate" in transformed


def test_c_extract_method_passes_referenced_local_variables():
    source = "int value(int input) {\n    int total = input + 1;\n    return total;\n}\n"
    transformed, count = c_transformers.apply_extract_method(source, "value_core", 3, 3)
    assert count == 1
    assert "return value_core(total);" in transformed
    assert "static int value_core(int total)" in transformed


def test_c_extract_method_handles_full_function_range():
    source = "int value(int input) {\n    int total = input + 1;\n    return total;\n}\n"
    transformed, count = c_transformers.apply_extract_method(source, "extracted_value", 1, 4)
    assert count == 1
    assert "return extracted_value(input);" in transformed
    assert "static int extracted_value(int input)" in transformed


def test_c_remove_dead_code_can_remove_safe_line_by_source_line():
    source = "int value(void) {\n    int unused = 0;\n    return 1;\n}\n"
    transformed, count = c_transformers.apply_remove_dead_code(source, "", source_line=2)
    assert count == 1
    assert "unused" not in transformed


def test_c_replace_unsafe_function_uses_safer_call_shape():
    source = "#include <string.h>\nvoid copy(char *dst, char *src) {\n    strcpy(dst, src);\n}\n"
    transformed, count = c_transformers.apply_replace_unsafe_function(source, "strcpy", "strncpy", 3)
    assert count == 1
    assert "strncpy(dst, src, sizeof(dst) - 1)" in transformed


def test_c_encapsulate_variable_adds_getter_and_setter():
    source = "int counter = 0;\nint read(void) { return counter; }\n"
    transformed, count = c_transformers.apply_encapsulate_variable(
        source,
        "counter",
        "get_counter",
        "set_counter",
    )
    assert count == 1
    assert "static int counter = 0;" in transformed
    assert "int get_counter(void)" in transformed
    assert "void set_counter(int value)" in transformed


def test_c_introduce_constant_uses_define_for_magic_number():
    source = "double ratio(void) { return 0.12; }\n"
    transformed, count = c_transformers.apply_extract_constant(source, 0.12, "EXTRACTED_CONSTANT")
    assert count == 1
    assert "#define MAGIC_NUMBER_0_12 0.12" in transformed
    assert "MAGIC_NUMBER_0_12" in transformed


def test_c_introduce_constant_replaces_escaped_newline_string():
    source = '#include <stdio.h>\nvoid show(void) { printf("hello\\n"); }\n'
    transformed, count = c_transformers.apply_extract_constant(
        source,
        "hello\n",
        "EXTRACTED_CONSTANT",
        source_line=2,
    )
    assert count == 1
    assert '#define MAGIC_STRING_HELLO "hello\\n"' in transformed
    assert "printf(MAGIC_STRING_HELLO)" in transformed


def test_c_extract_constant_falls_back_when_source_line_misses():
    source = "int size(void) { return 5; }\n"
    transformed, count = c_transformers.apply_extract_constant(source, 5, "BASE", source_line=99)
    assert count == 1
    assert "#define BASE 5" in transformed
    assert "return BASE" in transformed


def test_c_replace_literal_changes_value():
    source = "int size(void) { return 5; }\n"
    transformed, count = c_transformers.apply_replace_literal(source, 5, 7)
    assert count == 1
    assert "return 7" in transformed


def test_c_normalize_multiline_string_statement_extracts_constant():
    source = '#include <stdio.h>\nconst char *message = "hello "\n    "world";\n'
    transformed, count = c_transformers.apply_normalize_multiline_statement(
        source,
        source_line=2,
        constant_name="SCTVA_MESSAGE",
    )
    assert count == 1
    assert "static const char SCTVA_MESSAGE[]" in transformed
    assert "message = SCTVA_MESSAGE;" in transformed


def test_c_remove_dead_code_deletes_function():
    source = "int live(void) { return 1; }\nstatic int dead(void) { return 0; }\n"
    transformed, count = c_transformers.apply_remove_dead_code(source, "dead")
    assert count == 1
    assert "dead(void)" not in transformed


def test_java_remove_dead_code_rejects_live_statement():
    source = "class T { int value() { int answer = compute(); return answer; } }"
    transformed, count = java_transformers.apply_remove_dead_code(source, "", source_line=1)
    assert count == 0
    assert transformed == source


def test_c_fault_injection_replaces_return_logic():
    source = "int total(void) { int value = 1; return value; }\n"
    transformed, count = c_transformers.apply_fault_injection(source, "return value;", "return value + 1;")
    assert count == 1
    assert "return value + 1;" in transformed


def test_c_inject_syntax_error_breaks_source():
    source = "int total(void) { return 1; }\n"
    transformed, count = c_transformers.apply_inject_syntax_error(source)
    assert count == 1
    assert "__sctva_broken" in transformed
