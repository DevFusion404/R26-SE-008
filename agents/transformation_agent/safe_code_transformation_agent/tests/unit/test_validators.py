import subprocess
from types import SimpleNamespace

from sctva.validators.syntax_validator import SyntaxValidator
from sctva.validators.structural_validator import StructuralValidator
from sctva.contracts import RefactoringAction
from sctva.transformers.c_extract_method import apply_extract_method as extract_c_method
from sctva.validators.c_support import compare_c_static_summaries, validate_c_behavior


def test_python_syntax_validator_passes_valid_code():
    validator = SyntaxValidator()
    result = validator.validate(
        language="python",
        source_code="def a():\n    return 1\n",
        require_compilation=False,
        timeout_seconds=5,
    )
    assert result.passed is True


def test_python_syntax_validator_fails_invalid_code():
    validator = SyntaxValidator()
    result = validator.validate(
        language="python",
        source_code="def a(:\n    return 1\n",
        require_compilation=False,
        timeout_seconds=5,
    )
    assert result.passed is False


def test_python_syntax_validator_reports_token_errors():
    validator = SyntaxValidator()
    result = validator.validate(
        language="python",
        source_code="value = '''unterminated\n",
        require_compilation=False,
        timeout_seconds=5,
    )
    assert result.passed is False
    assert result.details["diagnostics"]


def test_structural_similarity_python_high_for_small_change():
    validator = StructuralValidator()
    result = validator.validate(
        language="python",
        original_code="def add(a,b):\n    return a+b\n",
        transformed_code="def add(a, b):\n    return a + b\n",
    )
    assert result.score >= 0.8


def test_c_syntax_validator_passes_valid_code_without_compiler(monkeypatch):
    monkeypatch.setattr("sctva.validators.syntax_validator.shutil.which", lambda name: None)
    validator = SyntaxValidator()
    result = validator.validate(
        language="c",
        source_code="int add(int a, int b) { return a + b; }\n",
        require_compilation=True,
        timeout_seconds=5,
    )
    assert result.passed is True


def test_c_syntax_validator_fails_invalid_code():
    validator = SyntaxValidator()
    result = validator.validate(
        language="c",
        source_code="int add(int a, int b) { return a + b\n",
        require_compilation=False,
        timeout_seconds=5,
    )
    assert result.passed is False


def test_c_syntax_validator_ignores_braces_in_strings_and_comments():
    validator = SyntaxValidator()
    result = validator.validate(
        language="c",
        source_code='int show(void) { char *s = "}"; /* { */ return 0; }\n',
        require_compilation=False,
        timeout_seconds=5,
    )
    assert result.passed is True


def test_c_syntax_validator_records_gcc_compiler_pass(monkeypatch):
    monkeypatch.setattr(
        "sctva.validators.syntax_validator.shutil.which",
        lambda name: "C:/tools/gcc.exe" if name == "gcc" else None,
    )
    monkeypatch.setattr(
        "sctva.validators.syntax_validator.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    result = SyntaxValidator().validate(
        language="c",
        source_code="int value(void) { return 1; }\n",
        require_compilation=True,
        timeout_seconds=5,
    )

    assert result.passed is True
    assert result.details["compiler_validation"] == "PASS"
    assert result.details["compiler"] == "gcc"


def test_c_syntax_validator_fails_real_compiler_error(monkeypatch):
    monkeypatch.setattr(
        "sctva.validators.syntax_validator.shutil.which",
        lambda name: "C:/tools/gcc.exe" if name == "gcc" else None,
    )
    monkeypatch.setattr(
        "sctva.validators.syntax_validator.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="sctva_temp.c:3:9: error: unknown type name 'MissingType'",
        ),
    )

    result = SyntaxValidator().validate(
        language="c",
        source_code="int value(void) { return 1; }\n",
        require_compilation=True,
        timeout_seconds=5,
    )

    assert result.passed is False
    assert result.details["compiler_validation"] == "FAIL"
    assert result.details["compiler_details"]["reason"] == "C_COMPILER_SYNTAX_ERROR"
    assert result.details["diagnostics"][0]["line"] == 3


def test_c_repository_compiler_validates_header_through_translation_unit(monkeypatch):
    commands = []
    monkeypatch.setattr(
        "sctva.validators.syntax_validator.shutil.which",
        lambda name: "C:/tools/gcc.exe" if name == "gcc" else None,
    )

    def successful_compile(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        "sctva.validators.syntax_validator.subprocess.run",
        successful_compile,
    )
    result = SyntaxValidator().validate_c_project(
        [
            {"file_name": "include/config.h", "source_code": "#define LIMIT 10\n"},
            {
                "file_name": "src/main.c",
                "source_code": '#include "config.h"\nint main(void) { return LIMIT; }\n',
            },
        ],
        require_compilation=True,
        timeout_seconds=5,
    )

    assert result.passed is True
    assert result.details["compiler_validation"] == "PASS"
    assert result.details["compiler_details"]["validated_translation_units"] == ["src/main.c"]
    assert len(commands) == 1
    assert commands[0][-1].endswith("src\\main.c") or commands[0][-1].endswith("src/main.c")


def test_c_syntax_validator_reports_compiler_unavailable(monkeypatch):
    monkeypatch.setattr("sctva.validators.syntax_validator.shutil.which", lambda name: None)

    result = SyntaxValidator().validate(
        language="c",
        source_code="int value(void) { return 1; }\n",
        require_compilation=True,
        timeout_seconds=5,
    )

    assert result.passed is True
    assert result.details["compiler_validation"] == "UNAVAILABLE"
    assert result.details["compiler_details"]["reason"] == "C_COMPILER_NOT_AVAILABLE"


def test_c_syntax_validator_uses_clang_when_gcc_is_unavailable(monkeypatch):
    monkeypatch.setattr(
        "sctva.validators.syntax_validator.shutil.which",
        lambda name: "C:/tools/clang.exe" if name == "clang" else None,
    )
    monkeypatch.setattr(
        "sctva.validators.syntax_validator.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    result = SyntaxValidator().validate(
        language="c",
        source_code="int value(void) { return 1; }\n",
        require_compilation=True,
        timeout_seconds=5,
    )

    assert result.passed is True
    assert result.details["compiler"] == "clang"


def test_c_syntax_validator_fails_compiler_timeout(monkeypatch):
    monkeypatch.setattr(
        "sctva.validators.syntax_validator.shutil.which",
        lambda name: "C:/tools/gcc.exe" if name == "gcc" else None,
    )

    def timed_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr("sctva.validators.syntax_validator.subprocess.run", timed_out)
    result = SyntaxValidator().validate(
        language="c",
        source_code="int value(void) { return 1; }\n",
        require_compilation=True,
        timeout_seconds=1,
    )

    assert result.passed is False
    assert result.details["compiler_validation"] == "FAIL"
    assert result.details["compiler_details"]["reason"] == "C_COMPILER_TIMEOUT"


def test_c_syntax_validator_detects_missing_return_semicolon():
    validator = SyntaxValidator()
    result = validator.validate(
        language="c",
        source_code="int add(void) {\n    return 1\n}\n",
        require_compilation=False,
        timeout_seconds=5,
    )
    assert result.passed is False
    assert "missing semicolon" in result.message


def test_c_syntax_validator_accepts_relative_include():
    validator = SyntaxValidator()
    result = validator.validate(
        language="c",
        source_code='#include "../ini.h"\nint main(void) { return 0; }\n',
        require_compilation=False,
        timeout_seconds=5,
    )
    assert result.passed is True


def test_c_syntax_validator_accepts_header_without_function_body():
    validator = SyntaxValidator()
    source = (
        "#ifndef INI_H\n"
        "#define INI_H\n"
        "#include <stdio.h>\n"
        "typedef int (*ini_handler)(void* user, const char* section);\n"
        "int ini_parse(const char* filename, ini_handler handler, void* user);\n"
        "#endif\n"
    )
    result = validator.validate(
        language="c",
        source_code=source,
        require_compilation=False,
        timeout_seconds=5,
    )
    assert result.passed is True
    assert "header/declaration unit" in " ".join(result.details["warnings"])


def test_c_syntax_validator_accepts_typedef_enum_header():
    source = (
        "#ifndef ERRORS_H\n"
        "#define ERRORS_H\n"
        "typedef enum {\n"
        "    ERROR_NONE = 0,\n"
        "    ERROR_INVALID = -1\n"
        "} error_t;\n"
        "#endif\n"
    )

    result = SyntaxValidator().validate(
        language="c",
        source_code=source,
        require_compilation=False,
        timeout_seconds=5,
    )

    assert result.passed is True


def test_c_syntax_validator_accepts_preprocessor_only_multiline_macro_header():
    source = (
        "#ifndef VERSION_H\n"
        "#define VERSION_H\n"
        "#define VERSION_NUMBER \\\n"
        "    ((1 * 10000) + (2 * 100) + 3)\n"
        "#endif\n"
    )

    result = SyntaxValidator().validate(
        language="c",
        source_code=source,
        require_compilation=False,
        timeout_seconds=5,
    )

    assert result.passed is True


def test_c_syntax_validator_accepts_conditional_platform_entry_points():
    source = (
        "#ifdef _WIN32\n"
        "int WINAPI WinMain(void) {\n"
        "#else\n"
        "int main(void) {\n"
        "#endif\n"
        "    return 0;\n"
        "}\n"
    )

    result = SyntaxValidator().validate(
        language="c",
        source_code=source,
        require_compilation=False,
        timeout_seconds=5,
    )

    assert result.passed is True


def test_c_syntax_validator_accepts_multiline_assignment_initializer():
    source = (
        "#include <stdlib.h>\n"
        "int create(void) {\n"
        "    int *value =\n"
        "        (int *)malloc(sizeof(int));\n"
        "    free(value);\n"
        "    return 0;\n"
        "}\n"
    )

    result = SyntaxValidator().validate(
        language="c",
        source_code=source,
        require_compilation=False,
        timeout_seconds=5,
    )

    assert result.passed is True


def test_c_syntax_validator_accepts_multiline_string_assignment():
    source = (
        "static const char html[] = \"first\\\n"
        "second\";\n"
        "int main(void) { return html[0] == 'f' ? 0 : 1; }\n"
    )

    result = SyntaxValidator().validate(
        language="c",
        source_code=source,
        require_compilation=False,
        timeout_seconds=5,
    )

    assert result.passed is True


def test_c_syntax_validator_accepts_cpp_header_like_unit():
    validator = SyntaxValidator()
    source = (
        "#ifndef INIREADER_H\n"
        "#define INIREADER_H\n"
        "#include <map>\n"
        "#include <string>\n"
        "#if defined(__GNUC__) && __GNUC__ >= 4\n"
        "#define INI_API __attribute__ ((visibility (\"default\")))\n"
        "#endif\n"
        "class INIReader {\n"
        "public:\n"
        "    INIReader(const std::string& filename);\n"
        "    int GetInteger(const std::string& section, const std::string& name, int default_value) const;\n"
        "};\n"
        "#endif\n"
    )
    result = validator.validate(
        language="c",
        source_code=source,
        require_compilation=False,
        timeout_seconds=5,
    )
    assert result.passed is True


def test_java_syntax_validator_ignores_braces_in_strings_and_comments():
    validator = SyntaxValidator()
    result = validator.validate(
        language="java",
        source_code='class Demo { String text() { return "}"; } /* { */ }\n',
        require_compilation=False,
        timeout_seconds=5,
    )
    assert result.passed is True


def test_java_syntax_validator_detects_import_after_class():
    validator = SyntaxValidator()
    result = validator.validate(
        language="java",
        source_code="class Demo {}\nimport java.util.List;\n",
        require_compilation=False,
        timeout_seconds=5,
    )
    assert result.passed is False
    assert "import declaration" in result.message


def test_java_syntax_validator_accepts_multiline_assignment():
    validator = SyntaxValidator()
    source = (
        "import java.sql.PreparedStatement;\n"
        "class Demo {\n"
        "    void save(java.sql.Connection connection) throws Exception {\n"
        "        PreparedStatement pst = connection.prepareStatement(\"insert into t \" +\n"
        "                \"values (?)\");\n"
        "        pst.executeUpdate();\n"
        "    }\n"
        "}\n"
    )
    result = validator.validate(
        language="java",
        source_code=source,
        require_compilation=False,
        timeout_seconds=5,
    )
    assert result.passed is True


def test_java_syntax_validator_accepts_leading_operator_continuation():
    validator = SyntaxValidator()
    source = (
        "class Demo {\n"
        "    private static final String SQL = \"UPDATE t \"\n"
        "            + \"SET a=? \"\n"
        "            + \"WHERE id=?\";\n"
        "}\n"
    )
    result = validator.validate(
        language="java",
        source_code=source,
        require_compilation=False,
        timeout_seconds=5,
    )
    assert result.passed is True


def test_java_syntax_validator_detects_unfinished_multiline_assignment():
    validator = SyntaxValidator()
    result = validator.validate(
        language="java",
        source_code="class Demo {\n    int value() {\n        int total = 1 +\n        return total;\n    }\n}\n",
        require_compilation=False,
        timeout_seconds=5,
    )
    assert result.passed is False
    assert "missing semicolon" in result.message


def test_java_syntax_validator_detects_nested_method_declaration():
    validator = SyntaxValidator()
    result = validator.validate(
        language="java",
        source_code=(
            "class Demo {\n"
            "    void outer() {\n"
            "        void inner() {\n"
            "        }\n"
            "    }\n"
            "}\n"
        ),
        require_compilation=False,
        timeout_seconds=5,
    )
    assert result.passed is False
    assert "nested method declaration" in result.message


def test_java_syntax_validator_accepts_action_listener_anonymous_class_method():
    source = '''import java.awt.event.*;
class Demo {
    void outer(Button button) {
        button.addActionListener(new ActionListener() {
            @Override
            public void actionPerformed(ActionEvent event) {
                System.out.println("clicked");
            }
        });
    }
}
'''
    result = SyntaxValidator().validate(language="java", source_code=source, require_compilation=False, timeout_seconds=5)
    assert result.passed is True


def test_java_syntax_validator_accepts_mouse_adapter_anonymous_class_method():
    source = '''import java.awt.event.*;
class Demo {
    void outer(Component component) {
        component.addMouseListener(new MouseAdapter() {
            @Override
            public void mouseClicked(MouseEvent event) {
                System.out.println(event.getX());
            }
        });
    }
}
'''
    result = SyntaxValidator().validate(language="java", source_code=source, require_compilation=False, timeout_seconds=5)
    assert result.passed is True


def test_java_syntax_validator_accepts_key_adapter_anonymous_class_method():
    source = '''import java.awt.event.*;
class Demo {
    void outer(Component component) {
        component.addKeyListener(new KeyAdapter() {
            @Override
            public void keyPressed(KeyEvent event) {
                System.out.println(event.getKeyCode());
            }
        });
    }
}
'''
    result = SyntaxValidator().validate(language="java", source_code=source, require_compilation=False, timeout_seconds=5)
    assert result.passed is True


def test_java_syntax_validator_accepts_anonymous_jtable_subclass_method():
    source = '''import javax.swing.JTable;
class Demo {
    JTable table = new JTable() {
        @Override
        public boolean isCellEditable(int row, int column) {
            return false;
        }
    };
}
'''
    result = SyntaxValidator().validate(language="java", source_code=source, require_compilation=False, timeout_seconds=5)
    assert result.passed is True


def test_java_syntax_validator_accepts_method_in_local_named_class():
    source = '''class Demo {
    void outer() {
        class LocalHandler {
            void handle() {
            }
        }
        new LocalHandler().handle();
    }
}
'''
    result = SyntaxValidator().validate(language="java", source_code=source, require_compilation=False, timeout_seconds=5)
    assert result.passed is True


def test_c_syntax_validator_accepts_multiline_assignment():
    validator = SyntaxValidator()
    source = (
        "int value(void) {\n"
        "    int total = 1 +\n"
        "        2;\n"
        "    return total;\n"
        "}\n"
    )
    result = validator.validate(
        language="c",
        source_code=source,
        require_compilation=False,
        timeout_seconds=5,
    )
    assert result.passed is True


def test_c_syntax_validator_accepts_multiline_if_comparison():
    validator = SyntaxValidator()
    source = (
        "int value(int lineno, char *start) {\n"
        "    if (lineno == 1 && (unsigned char)start[0] == 0xEF &&\n"
        "                       (unsigned char)start[1] == 0xBB &&\n"
        "                       (unsigned char)start[2] == 0xBF) {\n"
        "        start += 3;\n"
        "    }\n"
        "    return 0;\n"
        "}\n"
    )
    result = validator.validate(
        language="c",
        source_code=source,
        require_compilation=False,
        timeout_seconds=5,
    )
    assert result.passed is True


def test_c_structural_validator_high_for_small_change():
    validator = StructuralValidator()
    result = validator.validate(
        language="c",
        original_code="int add(int a, int b) { return a + b; }\n",
        transformed_code="int add(int a, int b) { return a + b; }\n",
    )
    assert result.score >= 0.8


def test_c_extract_method_structural_validation_requires_clean_scope_data_flow():
    original = '''int process(int value) {
    int first = value + 1;
    int second = first + 2;
    int third = second + 3;
    int observed = third + 4;
    return observed;
}
'''
    transformed, count, metadata = extract_c_method(
        original,
        new_method_name="calculate_values",
        method_name="process",
        start_line=2,
        end_line=5,
    )
    assert count == 1

    action = RefactoringAction(
        action_type="extract_method",
        parameters={
            "function": "process",
            "new_function_name": "calculate_values",
            "applied_transformation_metadata": metadata,
        },
    )
    result = StructuralValidator().validate(
        language="c",
        original_code=original,
        transformed_code=transformed,
        actions=[action],
    )
    assert result.passed is True
    checks = result.details["extract_method_validation"][0]["checks"]
    assert checks["scope_data_flow"] is True

    invalid_action = RefactoringAction(
        action_type="extract_method",
        parameters={
            **action.parameters,
            "applied_transformation_metadata": {
                **metadata,
                "scope_validation": {
                    **metadata["scope_validation"],
                    "undefined_identifiers": ["fcp"],
                },
            },
        },
    )
    invalid_result = StructuralValidator().validate(
        language="c",
        original_code=original,
        transformed_code=transformed,
        actions=[invalid_action],
    )
    assert invalid_result.passed is False


def test_c_static_comparison_accepts_suffixed_duplicate_constant_names():
    original = (
        'void parse(const char *s) {}\n'
        'int main(void) {\n'
        '    parse("[sec]\\nfoo = 01234567890123456789\\nbar=4321\\n");\n'
        '    parse("[sec]\\nfoo = 0123456789012bix=1234\\n");\n'
        '    return 0;\n'
        '}\n'
    )
    transformed = (
        '#define MAGIC_STRING__SEC__FOO___012345678901 "[sec]\\nfoo = 01234567890123456789\\nbar=4321\\n"\n'
        '#define MAGIC_STRING__SEC__FOO___012345678901_2 "[sec]\\nfoo = 0123456789012bix=1234\\n"\n'
        'void parse(const char *s) {}\n'
        'int main(void) {\n'
        '    parse(MAGIC_STRING__SEC__FOO___012345678901);\n'
        '    parse(MAGIC_STRING__SEC__FOO___012345678901_2);\n'
        '    return 0;\n'
        '}\n'
    )
    actions = [
        RefactoringAction(
            action_type="introduce_constant",
            parameters={
                "literal_value": "[sec]\nfoo = 01234567890123456789\nbar=4321\n",
                "constant_name": "EXTRACTED_CONSTANT",
            },
        ),
        RefactoringAction(
            action_type="introduce_constant",
            parameters={
                "literal_value": "[sec]\nfoo = 0123456789012bix=1234\n",
                "constant_name": "EXTRACTED_CONSTANT",
            },
        ),
    ]

    assert compare_c_static_summaries(original, transformed, actions)["matched"] is True


def test_c_static_comparison_accepts_verified_numeric_macro_in_array_bound():
    original = "int extractYear(char userID[15]) { return userID[0]; }\n"
    transformed = (
        "#define THRESHOLD_LIMIT_15 15\n"
        "int extractYear(char userID[THRESHOLD_LIMIT_15]) { return userID[0]; }\n"
    )

    assert compare_c_static_summaries(original, transformed, [])['matched'] is True

    structural = StructuralValidator().validate(
        language="c",
        original_code=original,
        transformed_code=transformed,
    )
    assert structural.details["function_signature_similarity"] == 1.0


def test_c_static_behavior_accepts_verified_numeric_macro_in_array_bound():
    original = "int extractYear(char userID[15]) { return userID[0]; }\n"
    transformed = (
        "#define SIZE 15\n"
        "int extractYear(char userID[SIZE]) { return userID[0]; }\n"
    )

    result = validate_c_behavior(
        original_code=original,
        transformed_code=transformed,
        behavior_tests=[],
        actions=[],
        enable_behavior_tests=True,
        timeout_seconds=1,
    )
    assert result["passed"] is True
    assert result["details"]["c_results"][0]["comparison"]["matched"] is True


def test_c_static_comparison_rejects_wrong_numeric_macro_in_array_bound():
    original = "int extractYear(char userID[15]) { return userID[0]; }\n"
    transformed = (
        "#define THRESHOLD_LIMIT_15 14\n"
        "int extractYear(char userID[THRESHOLD_LIMIT_15]) { return userID[0]; }\n"
    )

    comparison = compare_c_static_summaries(original, transformed, [])
    assert comparison["matched"] is False
    assert comparison["missing_functions"] == ["extractYear:char userID[15]"]


def test_c_static_comparison_rejects_undefined_or_non_numeric_array_macros():
    original = "int extractYear(char userID[15]) { return userID[0]; }\n"
    transformed_sources = [
        "int extractYear(char userID[SIZE]) { return userID[0]; }\n",
        "#define SIZE LENGTH\nint extractYear(char userID[SIZE]) { return userID[0]; }\n",
        "#define SIZE(x) (x)\nint extractYear(char userID[SIZE]) { return userID[0]; }\n",
    ]

    for transformed in transformed_sources:
        assert compare_c_static_summaries(original, transformed, [])["matched"] is False


def test_c_static_comparison_does_not_mask_real_signature_changes():
    original = "int extractYear(char userID[15]) { return userID[0]; }\n"
    transformed = "int extractMonth(char userID[15]) { return userID[0]; }\n"

    comparison = compare_c_static_summaries(original, transformed, [])
    assert comparison["matched"] is False
    assert comparison["missing_functions"] == ["extractYear:char userID[15]"]
