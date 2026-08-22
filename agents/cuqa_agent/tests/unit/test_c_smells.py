"""
unit/test_c_smells.py
---------------------
Unit tests for C code smell detectors in report_generator.py.
"""

import pytest
# pyrefly: ignore [missing-import]
from report_generator import generate_file_report


@pytest.mark.unit
class TestCSmells:
    def test_c_long_function_boundary(self):
        # 40 lines -> no smell
        lines_40 = "\n".join([f"    int x{i} = {i};" for i in range(38)])
        src_40 = f"int fn(void) {{\n{lines_40}\n    return 0;\n}}\n"
        r40 = generate_file_report(src_40, "test.c")
        assert not any(s["type"] == "LongFunction" for s in r40["code_smells"])

        # 41 lines -> TRIGGERS
        lines_41 = "\n".join([f"    int x{i} = {i};" for i in range(39)])
        src_41 = f"int fn(void) {{\n{lines_41}\n    return 0;\n}}\n"
        r41 = generate_file_report(src_41, "test.c")
        s41 = [s for s in r41["code_smells"] if s["type"] == "LongFunction"]
        assert len(s41) == 1
        assert s41[0]["severity"] == "high"

    def test_c_too_many_parameters(self):
        src_5 = "void fn(int a, int b, int c, int d, int e) {}\n"
        r5 = generate_file_report(src_5, "test.c")
        assert not any(s["type"] == "TooManyParameters" for s in r5["code_smells"])

        src_6 = "void fn(int *a, int b[], int c, int d, int e, int f) {}\n"
        r6 = generate_file_report(src_6, "test.c")
        s6 = [s for s in r6["code_smells"] if s["type"] == "TooManyParameters"]
        assert len(s6) == 1

    def test_c_deep_nesting(self):
        # Function body brace (1) + 3 nested ifs (3) = max depth 4 -> no smell
        src_4 = "void fn() { if(1){ if(1){ if(1){} } } }\n"
        r4 = generate_file_report(src_4, "test.c")
        assert not any(s["type"] == "DeepNesting" for s in r4["code_smells"])

        # Function body brace (1) + 4 nested ifs (4) = max depth 5 -> TRIGGERS
        src_5 = "void fn() { if(1){ if(1){ if(1){ if(1){} } } } }\n"
        r5 = generate_file_report(src_5, "test.c")
        s5 = [s for s in r5["code_smells"] if s["type"] == "DeepNesting"]
        assert len(s5) == 1

    def test_c_magic_number_in_comments_and_strings(self):
        src = '''\
#include <stdio.h>
void fn() {
    // int val = 999;
    printf("value = %d", 888);
    int codeVal = 777;
}
'''
        r = generate_file_report(src, "test.c")
        smells = [s for s in r["code_smells"] if s["type"] == "MagicNumber"]
        vals = [s["message"] for s in smells]
        assert any("777" in v for v in vals)

    def test_c_unsafe_functions(self):
        src = '''\
void fn(char *b, char *s) {
    // strcpy(b, s);
    char *msg = "gets(buffer)";
    strcpy(b, s);
    gets(b);
}
'''
        r = generate_file_report(src, "test.c")
        smells = [s for s in r["code_smells"] if s["type"] == "UnsafeFunctionUsage"]
        funcs = [s["entity"] for s in smells]
        assert "strcpy" in funcs
        assert "gets" in funcs

    def test_c_global_variable(self):
        src = "int counter;\nstatic int buf_size;\nvoid fn() { int local_var; }\n"
        r = generate_file_report(src, "test.c")
        smells = [s for s in r["code_smells"] if s["type"] == "GlobalVariable"]
        names = [s["entity"] for s in smells]
        assert "counter" in names

    def test_c_large_header_file(self):
        lines_300 = "// header\n" + "\n".join([f"// line {i}" for i in range(299)])
        r300 = generate_file_report(lines_300, "test.h")
        assert not any(s["type"] == "LargeHeaderFile" for s in r300["code_smells"])

        lines_301 = "// header\n" + "\n".join([f"// line {i}" for i in range(300)])
        r301 = generate_file_report(lines_301, "test.h")
        s301 = [s for s in r301["code_smells"] if s["type"] == "LargeHeaderFile"]
        assert len(s301) == 1

    def test_c_magic_number_comparison_variable_context(self):
        src = '''\
void check(int student_marks) {
    if (student_marks > 50) {
        // pass
    }
}
'''
        r = generate_file_report(src, "test.c")
        smells = [s for s in r["code_smells"] if s["type"] == "MagicNumber"]
        assert len(smells) == 1
        smell = smells[0]
        assert smell.get("variable_context") == "student_marks"
        assert "Magic number 50 compared to variable 'student_marks'" in smell["details"]

