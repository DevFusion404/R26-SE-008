from agents.transformation_agent.safe_code_transformation_agent.sctva.transformers import python_transformers
from sctva.transformers import c_transformers
from sctva.transformers import java_transformers
import ast


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


def test_python_extract_constant_normalizes_legacy_magic_name():
    source = "def value():\n    return 10\n"
    transformed, count = python_transformers.apply_extract_constant(source, 10, "MAGIC_NUMBER_10")
    assert count >= 1
    assert "CONSTANT_NUMBER_10" in transformed
    assert "MAGIC_NUMBER_10" not in transformed


def test_python_rename_symbol_changes_function_name():
    source = "def calc(x):\n    return x + 1\n"
    transformed, count = python_transformers.apply_rename_symbol(source, "calc", "calculate")
    assert count >= 1
    assert "def calculate" in transformed


def test_python_rename_method_updates_module_function_and_references():
    source = (
        "def calc(x):\n"
        "    return x + 1\n\n"
        "def run():\n"
        "    fn = calc\n"
        "    return calc(2) + fn(3)\n"
    )
    transformed, count, metadata = python_transformers.apply_rename_method(
        source,
        "calc",
        "calculate",
    )
    assert metadata["status"] == "success"
    assert count == 3
    assert "def calculate(x):" in transformed
    assert "fn = calculate" in transformed
    assert "return calculate(2) + fn(3)" in transformed
    function_names = {
        node.name for node in ast.parse(transformed).body if isinstance(node, ast.FunctionDef)
    }
    assert "calc" not in function_names


def test_python_rename_method_updates_class_method_and_attribute_calls():
    source = (
        "class PaymentService:\n"
        "    def process_payment(self, amount):\n"
        "        return amount + 1\n\n"
        "    def run(self):\n"
        "        return self.process_payment(2)\n\n"
        "def execute(service):\n"
        "    return service.process_payment(3)\n"
    )
    transformed, count, metadata = python_transformers.apply_rename_method(
        source,
        "process_payment",
        "calculate_payment",
        source_class="PaymentService",
    )
    assert metadata["status"] == "success"
    assert count == 3
    assert "def calculate_payment(self, amount):" in transformed
    assert "self.calculate_payment(2)" in transformed
    assert "service.calculate_payment(3)" in transformed
    assert ".process_payment(" not in transformed


def test_python_rename_method_rejects_ambiguous_class_method_without_owner():
    source = (
        "class A:\n"
        "    def value(self):\n"
        "        return 1\n\n"
        "class B:\n"
        "    def value(self):\n"
        "        return 2\n"
    )
    transformed, count, metadata = python_transformers.apply_rename_method(
        source,
        "value",
        "renamed_value",
    )
    assert transformed == source
    assert count == 0
    assert metadata["status"] == "review_required"
    assert metadata["reason"] == "AMBIGUOUS_METHOD_TARGET"


def test_python_extract_method_creates_sibling_helper_with_data_flow():
    source = (
        "def total(value):\n"
        "    subtotal = value + 10\n"
        "    discount = subtotal * 0.1\n"
        "    tax = subtotal * 0.2\n"
        "    result = subtotal - discount + tax\n"
        "    print(result)\n"
        "    return result\n"
    )
    transformed, count = python_transformers.apply_extract_method(source, "total_core", 3, 5)
    assert count == 1
    assert "\ndef total_core(" in transformed
    assert "    def total_core(" not in transformed
    assert "result = total_core(" in transformed


def test_python_extract_method_rejects_trivial_return_only_function():
    source = "def total(value):\n    tax = value * 0.1\n    return value + tax\n"
    transformed, count = python_transformers.apply_extract_method(source, "extracted_total", 1, 3)
    assert count == 0
    assert transformed == source


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


def test_java_extract_constant_converts_numeric_string_to_string_constant():
    source = (
        "public class T {\n"
        "    void connect() {\n"
        "        String pass = \"1234\";\n"
        "    }\n"
        "}\n"
    )
    transformed, count = java_transformers.apply_extract_constant(
        source,
        1234,
        "EXTRACTED_CONSTANT",
        source_line=3,
    )
    assert count == 1
    assert 'private static final String CONSTANT_STRING_N_1234 = "1234";' in transformed
    assert "String pass = CONSTANT_STRING_N_1234;" in transformed
    assert 'String pass = "CONSTANT_NUMBER_1234";' not in transformed


def test_java_extract_constant_does_not_replace_number_inside_longer_string():
    source = (
        "public class T {\n"
        "    void connect() {\n"
        "        String url = \"jdbc:mysql://localhost:3306/contact\";\n"
        "    }\n"
        "}\n"
    )
    transformed, count = java_transformers.apply_extract_constant(
        source,
        3306,
        "EXTRACTED_CONSTANT",
        source_line=3,
    )
    assert count == 0
    assert transformed == source


def test_java_explicit_numeric_constant_preserves_text_with_concatenation():
    source = (
        "class T {\n"
        "    void announce() {\n"
        "        System.out.println(\"Library closes at 5 PM today.\");\n"
        "    }\n"
        "}\n"
    )
    transformed, count = java_transformers.apply_extract_constant(
        source,
        5,
        "CONSTANT_5",
        source_line=3,
    )

    assert count == 1
    assert "private static final int CONSTANT_5 = 5;" in transformed
    assert '"Library closes at " + CONSTANT_5 + " PM today."' in transformed


def test_java_extract_constant_handles_numeric_string_after_prior_constant_insertion():
    source = (
        "public class T {\n"
        "    void connect() {\n"
        "        String url = \"jdbc:mysql://localhost:3306/contact\";\n"
        "        String pass = \"1234\";\n"
        "    }\n"
        "}\n"
    )
    transformed, url_count = java_transformers.apply_extract_constant(
        source,
        "jdbc:mysql://localhost:3306/contact",
        "EXTRACTED_CONSTANT",
        source_line=3,
    )
    transformed, pass_count = java_transformers.apply_extract_constant(
        transformed,
        1234,
        "MAGIC_NUMBER_1234",
        source_line=4,
    )

    assert url_count == 1
    assert pass_count == 1
    assert "String url = CONSTANT_STRING_JDBC_MYSQL___LOCALHOST_3;" in transformed
    assert "String pass = CONSTANT_STRING_N_1234;" in transformed
    assert 'private static final String CONSTANT_STRING_N_1234 = "1234";' in transformed
    assert "MAGIC_NUMBER_1234" not in transformed


def test_java_extract_constant_inserts_constant_into_extending_class():
    source = (
        "package login;\n\n"
        "public class SignUpServlet extends HttpServlet {\n"
        "    void connect() {\n"
        "        String url = \"jdbc:mysql://localhost:3306/contact\";\n"
        "    }\n"
        "}\n"
    )
    transformed, count = java_transformers.apply_extract_constant(
        source,
        "jdbc:mysql://localhost:3306/contact",
        "EXTRACTED_CONSTANT",
        source_line=5,
    )

    assert count == 1
    assert (
        "private static final String CONSTANT_STRING_JDBC_MYSQL___LOCALHOST_3 = "
        '"jdbc:mysql://localhost:3306/contact";'
    ) in transformed
    assert "String url = CONSTANT_STRING_JDBC_MYSQL___LOCALHOST_3;" in transformed
    assert "public class SignUpServlet extends HttpServlet {" in transformed


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


def test_java_rename_method_updates_declaration_calls_and_method_references():
    source = (
        'public class T {\n'
        '    String text = "processPayment"; // processPayment\n'
        '    int processPayment(int amount) { return amount + 1; }\n'
        '    int run() { return processPayment(2) + this.processPayment(3); }\n'
        '    java.util.function.IntUnaryOperator op = this::processPayment;\n'
        '}\n'
    )
    transformed, count, metadata = java_transformers.apply_rename_method(
        source,
        "processPayment",
        "calculatePayment",
        source_class="T",
    )
    assert metadata["status"] == "success"
    assert count == 4
    assert "int calculatePayment(int amount)" in transformed
    assert "return calculatePayment(2) + this.calculatePayment(3);" in transformed
    assert "this::calculatePayment" in transformed
    assert '"processPayment"' in transformed
    assert "// processPayment" in transformed
    assert "processPayment(" not in transformed


def test_java_rename_method_rejects_ambiguous_overload_without_signature():
    source = (
        "public class T {\n"
        "    int value(int input) { return input; }\n"
        "    int value(String input) { return input.length(); }\n"
        "    int run() { return value(1); }\n"
        "}\n"
    )
    transformed, count, metadata = java_transformers.apply_rename_method(
        source,
        "value",
        "renamedValue",
        source_class="T",
    )
    assert transformed == source
    assert count == 0
    assert metadata["status"] == "review_required"
    assert metadata["reason"] == "AMBIGUOUS_METHOD_OVERLOAD"


def test_java_rename_method_uses_explicit_signature_for_overload():
    source = (
        "public class T {\n"
        "    int value(int input) { return input; }\n"
        "    int text(String input) { return input.length(); }\n"
        "    int run() { return value(1); }\n"
        "}\n"
    )
    transformed, count, metadata = java_transformers.apply_rename_method(
        source,
        "value",
        "renamedValue",
        source_class="T",
        parameter_types=["int"],
    )
    assert metadata["status"] == "success"
    assert count == 2
    assert "int renamedValue(int input)" in transformed
    assert "return renamedValue(1);" in transformed


def test_java_extract_method_passes_referenced_local_variables():
    source = "public class T {\n    int value(int input) {\n        int subtotal = input + 1;\n        int tax = subtotal / 5;\n        int fee = subtotal / 10;\n        int total = subtotal + tax + fee;\n        System.out.println(total);\n        return total;\n    }\n}\n"
    transformed, count = java_transformers.apply_extract_method(source, "valueCore", 3, 6)
    assert count == 1
    assert "int total = valueCore(input);" in transformed
    assert "private int valueCore(int input)" in transformed


def test_java_extract_method_rejects_trivial_full_method_wrapper():
    source = "public class T {\n    int value(int input) {\n        int total = input + 1;\n        return total;\n    }\n}\n"
    transformed, count = java_transformers.apply_extract_method(source, "extractedValue", 2, 5)
    assert count == 0
    assert transformed == source


def test_java_extract_method_uses_method_name_when_prior_edits_shift_lines():
    source = (
        "public class T {\n"
        "    int first(int a) {\n"
        "        int x = a + 1;\n        int y = x + 2;\n        int z = y + 3;\n"
        "        int total = x + y + z;\n        System.out.println(total);\n        return total;\n"
        "    }\n"
        "\n"
        "    int second(int b) {\n"
        "        int x = b + 2;\n        int y = x + 3;\n        int z = y + 4;\n"
        "        int total = x + y + z;\n        System.out.println(total);\n        return total;\n"
        "    }\n"
        "}\n"
    )
    shifted, first_count = java_transformers.apply_extract_method(
        source,
        "extractedFirst",
        3,
        6,
        method_name="first",
    )
    transformed, second_count = java_transformers.apply_extract_method(
        shifted,
        "extractedSecond",
        11,
        13,
        method_name="second",
    )
    assert first_count == 1
    assert second_count == 1
    assert "int total = extractedSecond(b);" in transformed
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
    source = "int value(int input) {\n    int subtotal = input + 1;\n    int tax = subtotal / 5;\n    int fee = subtotal / 10;\n    int total = subtotal + tax + fee;\n    int observed = total;\n    return observed;\n}\n"
    transformed, count = c_transformers.apply_extract_method(source, "value_core", 2, 5)
    assert count == 1
    assert "value_core(input, &total);" in transformed
    assert "static void value_core(int input, int *total_out)" in transformed


def test_c_extract_method_rejects_trivial_full_function_wrapper():
    source = "int value(int input) {\n    int total = input + 1;\n    return total;\n}\n"
    transformed, count = c_transformers.apply_extract_method(source, "extracted_value", 1, 4)
    assert count == 0
    assert transformed == source


def test_c_remove_dead_code_can_remove_safe_line_by_source_line():
    source = "int value(void) {\n    int unused = 0;\n    return 1;\n}\n"
    transformed, count = c_transformers.apply_remove_dead_code(source, "", source_line=2)
    assert count == 1
    assert "unused" not in transformed


def test_c_replace_unsafe_function_uses_safer_call_shape():
    source = "#include <string.h>\nvoid copy(char *src) {\n    char dst[256];\n    strcpy(dst, src);\n}\n"
    transformed, count = c_transformers.apply_replace_unsafe_function(source, "strcpy", "strncpy", 4)
    assert count == 1
    assert "strncpy(dst, src, sizeof(dst) - 1)" in transformed


def test_c_replace_unsafe_function_skips_pointer_dest():
    source = "#include <string.h>\nvoid copy(char *dst, char *src) {\n    strcpy(dst, src);\n}\n"
    transformed, count = c_transformers.apply_replace_unsafe_function(source, "strcpy", "strncpy", 3)
    assert count == 0
    assert "strcpy(dst, src)" in transformed


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
    assert "#define CONSTANT_NUMBER_0_12 0.12" in transformed
    assert "CONSTANT_NUMBER_0_12" in transformed


def test_c_extract_constant_normalizes_legacy_magic_name():
    source = "int size(void) { return 7; }\n"
    transformed, count = c_transformers.apply_extract_constant(source, 7, "MAGIC_NUMBER_7")
    assert count == 1
    assert "#define CONSTANT_NUMBER_7 7" in transformed
    assert "MAGIC_NUMBER_7" not in transformed


def test_c_introduce_constant_replaces_escaped_newline_string():
    source = '#include <stdio.h>\nvoid show(void) { printf("hello\\n"); }\n'
    transformed, count = c_transformers.apply_extract_constant(
        source,
        "hello\n",
        "EXTRACTED_CONSTANT",
        source_line=2,
    )
    assert count == 1
    assert '#define CONSTANT_STRING_HELLO "hello\\n"' in transformed
    assert "printf(CONSTANT_STRING_HELLO)" in transformed


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
