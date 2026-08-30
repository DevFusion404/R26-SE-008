from sctva.validators.syntax_validator import SyntaxValidator
from sctva.validators.structural_validator import StructuralValidator
from sctva.contracts import RefactoringAction
from sctva.validators.c_support import compare_c_static_summaries


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
