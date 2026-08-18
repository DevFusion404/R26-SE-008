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
