"""
unit/test_java_smells.py
-------------------------
Unit tests for Java code smell detectors in report_generator.py.
"""

import pytest
# pyrefly: ignore [missing-import]
from report_generator import generate_file_report
# pyrefly: ignore [missing-import]
from java_ast_parser import JAVALANG_AVAILABLE


@pytest.mark.unit
class TestJavaSmells:
    def test_java_long_method_and_metadata(self):
        if not JAVALANG_AVAILABLE:
            pytest.skip("javalang not installed")

        body_31 = "\n".join([f"        int x{i} = {i};" for i in range(30)])
        src = f"public class Foo {{\n    public void test() {{\n{body_31}\n    }}\n}}\n"
        rep = generate_file_report(src, "Foo.java")
        smells = [s for s in rep["code_smells"] if s["type"] == "LongMethod"]
        assert len(smells) == 1
        s = smells[0]
        assert s["severity"] == "high"
        assert s["entity"] == "test"
        assert "start_line" in s
        assert "end_line" in s
        assert "parameter_count" in s
        assert "cyclomatic_complexity" in s

    def test_java_too_many_parameters(self):
        if not JAVALANG_AVAILABLE:
            pytest.skip("javalang not installed")

        src_6 = "public class Foo {\n    public void test(int a, int b, int c, int d, int e, int f) {}\n}\n"
        rep = generate_file_report(src_6, "Foo.java")
        smells = [s for s in rep["code_smells"] if s["type"] == "TooManyParameters"]
        assert len(smells) == 1
        assert smells[0]["parameter_count"] == 6

    def test_java_large_class(self):
        if not JAVALANG_AVAILABLE:
            pytest.skip("javalang not installed")

        methods_16 = "\n".join([f"    public void m{i}() {{}}" for i in range(16)])
        src = f"public class Big {{\n{methods_16}\n}}\n"
        rep = generate_file_report(src, "Big.java")
        smells = [s for s in rep["code_smells"] if s["type"] == "LargeClass"]
        assert len(smells) == 1
        assert smells[0]["method_count"] == 16

    def test_java_magic_number_in_code_vs_comments_strings(self):
        if not JAVALANG_AVAILABLE:
            pytest.skip("javalang not installed")

        src = '''\
public class Magic {
    // int val = 999;
    String str = "magic 888";
    int codeVal = 777;
}
'''
        rep = generate_file_report(src, "Magic.java")
        smells = [s for s in rep["code_smells"] if s["type"] == "MagicNumber"]
        messages = [s["message"] for s in smells]
        # 777 in real code MUST be reported
        assert any("777" in m for m in messages)

    def test_java_magic_number_comparison_variable_context(self):
        if not JAVALANG_AVAILABLE:
            pytest.skip("javalang not installed")

        src = '''\
public class Magic {
    public void check(int studentMarks) {
        if (studentMarks > 50) {
            System.out.println("Pass");
        }
    }
}
'''
        rep = generate_file_report(src, "Magic.java")
        smells = [s for s in rep["code_smells"] if s["type"] == "MagicNumber"]
        assert len(smells) == 1
        smell = smells[0]
        assert smell.get("variable_context") == "studentMarks"
        assert "Magic number 50 compared to variable 'studentMarks'" in smell["details"]

    def test_java_method_declaration_without_name(self, monkeypatch):
        if not JAVALANG_AVAILABLE:
            pytest.skip("javalang not installed")
        import javalang
        import report_generator
        from types import SimpleNamespace

        # Create object that passes isinstance(node, javalang.tree.MethodDeclaration)
        mock_node = javalang.tree.MethodDeclaration(name=None, parameters=[1, 2, 3, 4, 5, 6])
        setattr(mock_node, "_position", SimpleNamespace(line=1))

        mock_tree = [(None, mock_node)]
        monkeypatch.setattr(javalang.parse, "parse", lambda src: mock_tree)

        smells = report_generator._analyze_java_smells("public class Foo {}")
        too_many_params = [s for s in smells if s["type"] == "TooManyParameters"]
        assert len(too_many_params) == 1
        assert too_many_params[0]["entity"] == ""

    def test_java_binary_operation_without_operator(self, monkeypatch):
        if not JAVALANG_AVAILABLE:
            pytest.skip("javalang not installed")
        import javalang
        import report_generator
        from types import SimpleNamespace

        mock_literal = javalang.tree.Literal(value="42")
        setattr(mock_literal, "_position", SimpleNamespace(line=5))

        mock_bin_op = javalang.tree.BinaryOperation()
        if hasattr(mock_bin_op, "operator"):
            delattr(mock_bin_op, "operator")

        mock_tree = [([mock_bin_op], mock_literal)]
        monkeypatch.setattr(javalang.parse, "parse", lambda src: mock_tree)

        smells = report_generator._analyze_java_smells("public class Foo {}")
        magic_smells = [s for s in smells if s["type"] == "MagicNumber"]
        assert len(magic_smells) >= 1

    def test_java_literal_without_value(self, monkeypatch):
        if not JAVALANG_AVAILABLE:
            pytest.skip("javalang not installed")
        import javalang
        import report_generator

        mock_literal = javalang.tree.Literal()
        if hasattr(mock_literal, "value"):
            delattr(mock_literal, "value")

        mock_tree = [([], mock_literal)]
        monkeypatch.setattr(javalang.parse, "parse", lambda src: mock_tree)

        smells = report_generator._analyze_java_smells("public class Foo {}")
        assert isinstance(smells, list)





